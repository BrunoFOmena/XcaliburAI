"""Input validation for the ECHOLAS pipeline (issue #6).

The checks mirror how the pipeline itself reads its inputs, because the dangerous
failures are the silent ones: a missing mate, a lowercase ``_r1`` or a dot in a sample
name do not make Nextflow fail, they make it treat the reads differently (single-end
instead of paired, two samples merged into one) and still produce plausible results.

What the pipeline does (main.nf and modules/trimming_qc.nf):

* reference: ``file("${reference}/*.{fna,fa,fasta}")``, needs exactly one match;
* reads: ``Channel.fromPath("${raw_data}/*.fastq.gz")``, top level only, case-sensitive;
* sample name: ``simpleName`` (the file name up to its first ``.``) with every ``_R1``,
  ``_R2``, ``_1`` and ``_2`` removed, in that order;
* mate: R1 when the name contains ``_1`` or ``_R1`` followed by ``_`` or ``.``, else R2
  for ``_2``/``_R2``, else single-end; a sample is paired only when it has both mates.

Only names and metadata are read (plus the two gzip magic bytes of each FASTQ); nothing
is written, and files are never read whole.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xcalibur.inputs import InputReport

FASTQ_SUFFIX = ".fastq.gz"
REFERENCE_SUFFIXES = (".fna", ".fa", ".fasta")
IGNORED_DIRS = {"_disabled"}
"""Folders the ECHOLAS layout uses to park files; the pipeline never reads subfolders."""

GZIP_MAGIC = b"\x1f\x8b"
_R1 = re.compile(r"_R?1(_|\.)")
_R2 = re.compile(r"_R?2(_|\.)")
_LOWER_MATE = re.compile(r"_r[12](_|\.)")
_FASTQ_LIKE = re.compile(r"\.(fastq|fq)(\.(gz|bz2|xz|zst))?$", re.IGNORECASE)
_REFERENCE_LIKE = re.compile(r"\.(fna|fa|fasta)(\.(gz|bz2|xz|zst))?$", re.IGNORECASE)
_SAFE_SAMPLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def pipeline_sample(name: str) -> str:
    """Sample name the pipeline derives from a FASTQ file name (main.nf)."""
    sample = name.split(".", 1)[0]
    for suffix in ("_R1", "_R2", "_1", "_2"):
        sample = sample.replace(suffix, "")
    return sample


def pipeline_mate(name: str) -> str:
    """``R1``, ``R2`` or ``SE``, exactly as modules/trimming_qc.nf classifies a file."""
    if _R1.search(name):
        return "R1"
    if _R2.search(name):
        return "R2"
    return "SE"


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
    files = _top_level_files(folder)
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

    for sub in sorted(p for p in folder.iterdir() if p.is_dir() and not _hidden(p)):
        if sub.name not in IGNORED_DIRS:
            report.warn("subfolder_ignored",
                        f"The pipeline does not read subfolders: files in {sub} are ignored.",
                        input="raw_data", path=sub)

    groups: dict[str, list[tuple[Path, str]]] = {}
    for file in _top_level_files(folder):
        name = file.name
        if not name.endswith(FASTQ_SUFFIX):
            if _FASTQ_LIKE.search(name):
                report.error("fastq_not_gzipped",
                             f"{file} is not named *.fastq.gz, so the pipeline would skip "
                             "it: compress it with gzip and/or rename it to end in "
                             "'.fastq.gz' (lowercase).", input="raw_data", path=file)
            else:
                report.warn("file_ignored", f"{file} is not a FASTQ file and is ignored.",
                            input="raw_data", path=file)
            continue
        if not _check_fastq_file(file, report):
            continue
        if _LOWER_MATE.search(name) and pipeline_mate(name) == "SE":
            report.error("mate_suffix_lowercase",
                         f"{file}: the pipeline recognises only '_1'/'_2' or uppercase "
                         "'_R1'/'_R2', so it would treat this file as single-end. Rename it "
                         "with '_R1' or '_R2'.", input="raw_data", path=file)
            continue
        sample = pipeline_sample(name)
        if not _SAFE_SAMPLE.fullmatch(sample):
            shown = repr(sample) if sample else "an empty name"
            report.error("sample_name_invalid",
                         f"{file}: the pipeline would name this sample {shown}. Sample names "
                         "must start with a letter or digit and use only letters, digits, "
                         "'_' or '-'; rename the file.", input="raw_data", path=file)
            continue
        groups.setdefault(sample, []).append((file, pipeline_mate(name)))

    samples = [_check_sample(sample, files, report) for sample, files in sorted(groups.items())]
    samples = [s for s in samples if s is not None]
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
                     f"Files {', '.join(names)} all become sample '{sample}' in the pipeline "
                     "(it cuts the name at the first '.' and removes every '_1', '_2', '_R1' "
                     "and '_R2'). Give each sample a distinct name with exactly one R1 and "
                     "one R2 file (merge lanes first), or a single single-end file.", **where)
        return None

    for file, mate in files:
        expected = _expected_sample(file.name, mate)
        if expected != sample:
            report.warn("sample_renamed",
                        f"{file.name} will be reported as sample '{sample}', not '{expected}' "
                        "(the pipeline cuts the name at the first '.' and removes every '_1', "
                        "'_2', '_R1' and '_R2').", path=file, **where)

    ordered = sorted(files, key=lambda item: item[1])
    return {
        "sample": sample,
        "layout": layout,
        "files": [
            {"name": f.name, "mate": mate, "size_bytes": f.stat().st_size}
            for f, mate in ordered
        ],
    }


def _expected_sample(name: str, mate: str) -> str:
    """The name a person would expect: the file name without .fastq.gz and its mate tag."""
    stem = name[: -len(FASTQ_SUFFIX)]
    if mate == "SE":
        return stem
    match = (_R1 if mate == "R1" else _R2).search(name)
    if match is None:  # pragma: no cover - mate came from the same patterns
        return stem
    # Drop the tag ("_1" or "_R1") and keep the '_' or '.' that followed it.
    return (name[: match.start()] + name[match.end() - 1 :])[: -len(FASTQ_SUFFIX)]


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
                     f"{file} ends in .fastq.gz but is not gzip-compressed: compress it with "
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


def _top_level_files(folder: Path) -> list[Path]:
    """Visible entries of ``folder`` that are (or point to) files, sorted by name.

    Nextflow globs skip hidden files and do not descend into subfolders; neither do we.
    Broken links are kept so that they are reported.
    """
    return sorted(
        p for p in folder.iterdir()
        if not _hidden(p) and (p.is_file() or (p.is_symlink() and not p.exists()))
    )


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
