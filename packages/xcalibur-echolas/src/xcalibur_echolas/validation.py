"""Input validation for the ECHOLAS pipeline (issues #6 and #63).

The checks follow how the pipeline itself reads its inputs, because the dangerous
failures are the silent ones: a missing mate or two files that end up as the same sample
do not make Nextflow fail, they make it treat the reads differently and still produce
plausible results.

How the pipeline reads them (main.nf):

* reference: ``file("${reference}/*.{fna,fa,fasta}")``, needs exactly one match;
* reads: top-level files of ``raw_data`` whose name matches :data:`FASTQ_NAME`
  (``.fastq.gz`` or ``.fq.gz``, any case);
* sample and mate: :data:`MATE_SUFFIX` on the name without its extension. ``_R1``/``_R2``,
  ``_1``/``_2`` or ``_R1_NNN``/``_R2_NNN`` at the very end (any case) make a mate and the
  rest is the sample; any other name is a single-end sample. A sample is paired only when
  it has both mates.

``FASTQ_NAME`` and ``MATE_SUFFIX`` are written character for character in main.nf;
tests/test_echolas_validation.py checks that and, with Nextflow installed, runs both.

Only names and metadata are read (plus the two gzip magic bytes of each FASTQ); nothing
is written, and files are never read whole.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xcalibur.inputs import InputReport

FASTQ_NAME = r"(?i)^(.+)\.f(?:ast)?q\.gz$"
"""A FASTQ file the pipeline reads; group 1 is the name without the extension."""
MATE_SUFFIX = r"(?i)^(.+?)_(?:R([12])_\d{3}|R?([12]))$"
"""Mate tag at the end of that name; group 1 is the sample, group 2 or 3 the mate."""

REFERENCE_SUFFIXES = (".fna", ".fa", ".fasta")
IGNORED_DIRS = {"_disabled"}
"""Folders the ECHOLAS layout uses to park files; the pipeline never reads subfolders."""

GZIP_MAGIC = b"\x1f\x8b"
_FASTQ = re.compile(FASTQ_NAME)
_MATE = re.compile(MATE_SUFFIX)
_FASTQ_LIKE = re.compile(r"\.(fastq|fq)(\.(gz|bz2|xz|zst))?$", re.IGNORECASE)
_REFERENCE_LIKE = re.compile(r"\.(fna|fa|fasta)(\.(gz|bz2|xz|zst))?$", re.IGNORECASE)
_SAFE_SAMPLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_LANE = re.compile(r"(?i)^(.+)_L\d{3}$")


def parse_fastq_name(name: str) -> tuple[str, str] | None:
    """``(sample, mate)`` the pipeline derives from a file name, or None if it skips it.

    ``mate`` is ``R1``, ``R2`` or ``SE``.
    """
    fastq = _FASTQ.fullmatch(name)
    if fastq is None:
        return None
    stem = fastq.group(1)
    tag = _MATE.fullmatch(stem)
    if tag is None:
        return stem, "SE"
    return tag.group(1), "R" + (tag.group(2) or tag.group(3))


def validate_inputs(inputs: Mapping[str, Path]) -> InputReport:
    """Check ``raw_data`` and ``reference`` before the pipeline runs."""
    report = InputReport()
    reference = _check_reference(inputs.get("reference"), report)
    samples = _check_reads(inputs.get("raw_data"), report)

    paired = sum(1 for s in samples if s["layout"] == "paired-end")
    single = len(samples) - paired
    report.summary = {
        "samples": samples,
        "sample_count": len(samples),
        "paired_end": paired,
        "single_end": single,
        "reference": reference,
    }
    parts = [f"{len(samples)} sample{'s' if len(samples) != 1 else ''}"
             f" ({paired} paired-end, {single} single-end)"]
    if reference is not None:
        parts.append(f"reference {reference['name']} ({_size(reference['size_bytes'])})")
    report.overview = "; ".join(parts)
    return report


# -- reference ----------------------------------------------------------------------------


def _check_reference(path: Path | None, report: InputReport) -> dict[str, Any] | None:
    folder = _folder(path, "reference", "the folder with the genome (.fna, .fa or .fasta)",
                     report)
    if folder is None:
        return None
    listing = _list_folder(folder, "reference", report)
    if listing is None:
        return None
    files, _ = listing
    genomes = [f for f in files if f.name.endswith(REFERENCE_SUFFIXES)]
    if not genomes:
        near = [f.name for f in files if _REFERENCE_LIKE.search(f.name)]
        hint = (f" Found {near}: the pipeline reads only uncompressed, lowercase .fna, .fa "
                "or .fasta files, so decompress or rename it." if near else "")
        report.error("reference_missing",
                     f"No reference genome in {folder} (expected one .fna, .fa or .fasta "
                     f"file).{hint}", input="reference", path=folder)
        return None
    if len(genomes) > 1:
        names = sorted(f.name for f in genomes)
        report.error("reference_multiple",
                     f"{len(genomes)} reference genomes in {folder} ({', '.join(names)}): "
                     "keep exactly one .fna/.fa/.fasta file in that folder.",
                     input="reference", path=folder)
        return None
    genome = genomes[0]
    size = _file_size(genome, "reference", report)
    if size == 0:
        report.error("reference_empty", f"The reference genome {genome} is empty.",
                     input="reference", path=genome)
    return {"name": genome.name, "path": str(genome), "size_bytes": size}


# -- reads --------------------------------------------------------------------------------


def _check_reads(path: Path | None, report: InputReport) -> list[dict[str, Any]]:
    folder = _folder(path, "raw_data", "the folder with the .fastq.gz files", report)
    if folder is None:
        return []
    listing = _list_folder(folder, "raw_data", report)
    if listing is None:
        return []
    files, subfolders = listing

    for sub in subfolders:
        if sub.name not in IGNORED_DIRS:
            report.warn("subfolder_ignored",
                        f"The pipeline does not read subfolders: files in {sub} are ignored.",
                        input="raw_data", path=sub)

    groups: dict[str, list[tuple[Path, str]]] = {}
    for file in files:
        parsed = parse_fastq_name(file.name)
        if parsed is None:
            if _FASTQ_LIKE.search(file.name):
                report.error("fastq_not_gzipped",
                             f"{file} is not gzip-compressed (or not named .fastq.gz/.fq.gz), "
                             "so the pipeline would skip it: compress it with gzip.",
                             input="raw_data", path=file)
            else:
                report.warn("file_ignored", f"{file} is not a FASTQ file and is ignored.",
                            input="raw_data", path=file)
            continue
        if not _check_fastq_file(file, report):
            continue
        sample, mate = parsed
        if not _SAFE_SAMPLE.fullmatch(sample):
            report.error("sample_name_invalid",
                         f"{file}: the sample name would be {sample!r}. Sample names must "
                         "start with a letter or digit and use only letters, digits, '.', "
                         "'_' or '-'; rename the file.", input="raw_data", path=file)
            continue
        groups.setdefault(sample, []).append((file, mate))

    samples = [_check_sample(sample, files, report) for sample, files in sorted(groups.items())]
    samples = [s for s in samples if s is not None]
    _warn_lanes(sorted(groups), report)
    if not groups and not any(i.input == "raw_data" for i in report.errors):
        report.error("no_samples", f"No *.fastq.gz files in {folder}.",
                     input="raw_data", path=folder)
    return samples


def _check_sample(
    sample: str, files: list[tuple[Path, str]], report: InputReport
) -> dict[str, Any] | None:
    mates = sorted(mate for _, mate in files)
    names = sorted(f.name for f, _ in files)
    where = {"input": "raw_data", "sample": sample}

    if mates == ["R1", "R2"]:
        layout = "paired-end"
    elif mates == ["SE"]:
        layout = "single-end"
    elif mates in (["R1"], ["R2"]):
        file = files[0][0]
        report.error("mate_missing",
                     f"{file.name} has no mate: sample '{sample}' needs both an R1 and an R2 "
                     "file. Add the missing mate, or rename the file without the mate "
                     "suffix if the sample is single-end.", path=file, **where)
        return None
    elif "SE" in mates and ("R1" in mates or "R2" in mates):
        report.error("layout_mixed",
                     f"Sample '{sample}' mixes single-end and paired-end files "
                     f"({', '.join(names)}): the pipeline would silently drop the "
                     "single-end file. Keep one layout per sample.", **where)
        return None
    else:
        report.error("sample_collision",
                     f"Files {', '.join(names)} all become sample '{sample}' "
                     f"({', '.join(mates)}): give each sample exactly one R1 and one R2 "
                     "file, or a single single-end file (the mate tag is read in any case, "
                     "so A_R1 and A_r1 are the same mate; A.fastq.gz and A.fq.gz the same "
                     "file).", **where)
        return None

    ordered = sorted(files, key=lambda item: item[1])
    return {
        "sample": sample,
        "layout": layout,
        "files": [
            {"name": f.name, "mate": mate, "size_bytes": f.stat().st_size}
            for f, mate in ordered
        ],
    }


def _warn_lanes(samples: list[str], report: InputReport) -> None:
    """Samples that differ only by an Illumina lane tag (_L001, _L002) are run apart."""
    lanes: dict[str, list[str]] = {}
    for sample in samples:
        match = _LANE.fullmatch(sample)
        if match:
            lanes.setdefault(match.group(1), []).append(sample)
    for base, members in sorted(lanes.items()):
        if len(members) > 1:
            report.warn("lanes_not_merged",
                        f"Samples {', '.join(members)} look like lanes of '{base}'; the "
                        "pipeline runs them as separate samples. Merge the lanes first if "
                        "they are the same sample.", input="raw_data", sample=base)


def _check_fastq_file(file: Path, report: InputReport) -> bool:
    size = _file_size(file, "raw_data", report)
    if size is None:
        return False
    if size == 0:
        report.error("fastq_empty", f"{file} is empty.", input="raw_data", path=file)
        return False
    try:
        with file.open("rb") as handle:
            magic = handle.read(2)
    except OSError as exc:
        report.error("unreadable", f"Cannot read {file}: {exc.strerror or exc}.",
                     input="raw_data", path=file)
        return False
    if magic != GZIP_MAGIC:
        report.error("fastq_not_gzipped",
                     f"{file} has a .gz name but is not gzip-compressed: compress it with "
                     "gzip.", input="raw_data", path=file)
        return False
    return True


# -- helpers ------------------------------------------------------------------------------


def _folder(path: Path | None, name: str, what: str, report: InputReport) -> Path | None:
    if path is None:
        report.error("input_missing", f"Input '{name}' is required: give {what}.", input=name)
        return None
    if not path.exists():
        report.error("path_not_found", f"Input '{name}': {path} does not exist.",
                     input=name, path=path)
        return None
    if not path.is_dir():
        report.error("not_a_directory",
                     f"Input '{name}' must be a folder, but {path} is a file: give {what} "
                     f"(for example {path.parent}).", input=name, path=path)
        return None
    return path


def _list_folder(
    folder: Path, name: str, report: InputReport
) -> tuple[list[Path], list[Path]] | None:
    """Visible files (or links to files) and subfolders of ``folder``, sorted by name.

    Nextflow globs skip hidden files and do not descend into subfolders; neither do we.
    Broken links count as files so that they are reported. A folder that cannot be
    listed (no read or search permission, for example) is an error, not an exception.
    """
    files: list[Path] = []
    subfolders: list[Path] = []
    try:
        for entry in sorted(folder.iterdir()):
            if _hidden(entry):
                continue
            if entry.is_dir():
                subfolders.append(entry)
            elif entry.is_file() or (entry.is_symlink() and not entry.exists()):
                files.append(entry)
    except OSError as exc:
        report.error("unreadable",
                     f"Cannot read the folder {folder}: {exc.strerror or exc}. Check its "
                     "permissions (it needs read and execute/search access).",
                     input=name, path=folder)
        return None
    return files, subfolders


def _file_size(file: Path, name: str, report: InputReport) -> int | None:
    try:
        return file.stat().st_size
    except OSError as exc:
        report.error("unreadable", f"Cannot read {file}: {exc.strerror or exc}.",
                     input=name, path=file)
        return None


def _hidden(path: Path) -> bool:
    return path.name.startswith(".")


def _size(size: int | None) -> str:
    if size is None:
        return "unknown size"
    value = float(size)
    for unit in ("B", "kB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} GB"
