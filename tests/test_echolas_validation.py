"""ECHOLAS input validation (issue #6): every rule has a passing and a failing case."""

import gzip
import json
import os
import re
import shutil
import subprocess

import pytest

from xcalibur import cli
from xcalibur_echolas import plugin
from xcalibur_echolas.validation import (
    FASTQ_NAME,
    MATE_SUFFIX,
    parse_fastq_name,
    validate_inputs,
)

GENOME = ">contig1\nACGTACGTAC\n"


def fastq(path, reads=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as handle:
        for i in range(reads):
            handle.write(f"@r{i}\nACGT\n+\nIIII\n")
    return path


def make(tmp_path, reads=(), genomes=("ref.fa",)):
    """A raw_data folder with the given FASTQ names and a reference folder."""
    raw, ref = tmp_path / "raw_data", tmp_path / "reference"
    raw.mkdir()
    ref.mkdir()
    for name in reads:
        fastq(raw / name)
    for name in genomes:
        (ref / name).write_text(GENOME)
    return {"raw_data": raw, "reference": ref}


def codes(report):
    return sorted(issue.code for issue in report.errors)


def warning_codes(report):
    return sorted(issue.code for issue in report.warnings)


# -- the naming rule (issue #63) ------------------------------------------------------

NAMES = [
    ("S1_R1.fastq.gz", ("S1", "R1")),
    ("S1_R2.fastq.gz", ("S1", "R2")),
    ("S2_1.fastq.gz", ("S2", "R1")),
    ("S2_2.fq.gz", ("S2", "R2")),
    ("S3.fastq.gz", ("S3", "SE")),
    ("A_r1.fastq.gz", ("A", "R1")),
    ("A_r2.FASTQ.GZ", ("A", "R2")),
    ("S4.lane1_R1.fastq.gz", ("S4.lane1", "R1")),
    ("S6_R1_001.fastq.gz", ("S6", "R1")),
    ("S7_1_R2.fastq.gz", ("S7_1", "R2")),
    ("S8_10.fastq.gz", ("S8_10", "SE")),
    ("SRR_123_1.fastq.gz", ("SRR_123", "R1")),
    ("X_L001_R2_001.fq.gz", ("X_L001", "R2")),
    ("A_1_123.fastq.gz", ("A_1_123", "SE")),
    ("_R1.fastq.gz", ("_R1", "SE")),
    ("S_R2_1.fastq.gz", ("S_R2", "R1")),
    ("A_R1_1234.fastq.gz", ("A_R1_1234", "SE")),
    ("A_R1_\u0661\u0662\u0663.fastq.gz", ("A_R1_\u0661\u0662\u0663", "SE")),
    ("A_r1_001.FQ.GZ", ("A", "R1")),
    ("1_2.fastq.gz", ("1", "R2")),
    ("A.fastq", None),
    ("A.fastq.bz2", None),
    ("notes.txt", None),
]


@pytest.mark.parametrize("name, expected", NAMES)
def test_sample_and_mate_from_the_file_name(name, expected):
    assert parse_fastq_name(name) == expected


def test_main_nf_uses_the_same_patterns():
    main = plugin().workflow_path.read_text(encoding="utf-8")
    assert f"~/{FASTQ_NAME}/" in main
    assert f"~/{MATE_SUFFIX}/" in main


PARITY_NF = """
nextflow.enable.dsl=2
workflow {
    def FASTQ_NAME  = ~/%s/
    def MATE_SUFFIX = ~/%s/
    Channel.fromPath("${params.raw_data}/*")
        .filter { f -> f.name ==~ FASTQ_NAME }
        .map { f ->
            def stem = (f.name =~ FASTQ_NAME)[0][1]
            def tag  = stem =~ MATE_SUFFIX
            def r = tag.matches()
                ? [tag.group(1), "R" + (tag.group(2) ?: tag.group(3))]
                : [stem, "SE"]
            "PARITY\t${f.name}\t${r[0]}\t${r[1]}"
        }
        .view()
}
"""


@pytest.mark.e2e
def test_nextflow_reads_names_exactly_like_python(tmp_path):
    if shutil.which("nextflow") is None:
        if os.environ.get("XCALIBUR_E2E_REQUIRED") == "1":  # the CI e2e job sets it
            pytest.fail("the parity test needs nextflow on PATH")
        pytest.skip("needs Nextflow")
    raw = tmp_path / "raw"
    raw.mkdir()
    for name, _ in NAMES:
        (raw / name).write_bytes(b"x")
    (raw / ".hidden_R1.fastq.gz").write_bytes(b"x")
    script = tmp_path / "parity.nf"
    script.write_text(PARITY_NF % (FASTQ_NAME, MATE_SUFFIX))
    out = subprocess.run(
        ["nextflow", "-q", "run", str(script), "--raw_data", str(raw)],
        cwd=tmp_path, capture_output=True, text=True, timeout=300, check=True,
    ).stdout
    # Read records, not lines: Nextflow 26 with -q may print view() items without newlines
    # when stdout is not a terminal.
    seen = {name: (sample, mate) for name, sample, mate
            in re.findall(r"PARITY\t([^\t]+)\t([^\t]*)\t(R1|R2|SE)", out)}
    assert seen == {name: parsed for name, parsed in NAMES if parsed is not None}


# -- reference ----------------------------------------------------------------------------

@pytest.mark.parametrize("genome", ["ref.fa", "ref.fna", "ref.fasta"])
def test_exactly_one_reference_passes(tmp_path, genome):
    report = validate_inputs(make(tmp_path, ["A_R1.fastq.gz", "A_R2.fastq.gz"], [genome]))
    assert report.ok, report.errors
    assert report.summary["reference"]["name"] == genome


def test_no_reference_fails(tmp_path):
    report = validate_inputs(make(tmp_path, ["A.fastq.gz"], genomes=()))
    assert codes(report) == ["reference_missing"]


def test_compressed_or_uppercase_reference_fails_with_a_hint(tmp_path):
    inputs = make(tmp_path, ["A.fastq.gz"], genomes=())
    (inputs["reference"] / "ref.fa.gz").write_bytes(b"\x1f\x8b")
    (inputs["reference"] / "REF.FASTA").write_text(GENOME)
    report = validate_inputs(inputs)
    assert codes(report) == ["reference_missing"]
    assert "ref.fa.gz" in report.errors[0].message and "decompress" in report.errors[0].message


def test_two_references_fail_and_are_named(tmp_path):
    report = validate_inputs(make(tmp_path, ["A.fastq.gz"], ["a.fa", "b.fasta"]))
    assert codes(report) == ["reference_multiple"]
    assert "a.fa" in report.errors[0].message and "b.fasta" in report.errors[0].message


def test_reference_index_files_are_not_counted(tmp_path):
    inputs = make(tmp_path, ["A.fastq.gz"])
    for extra in ("ref.fa.fai", "ref.dict", "ref.fa.bwt"):
        (inputs["reference"] / extra).write_text("x")
    assert validate_inputs(inputs).ok


def test_empty_reference_fails(tmp_path):
    inputs = make(tmp_path, ["A.fastq.gz"], genomes=())
    (inputs["reference"] / "ref.fa").write_text("")
    assert codes(validate_inputs(inputs)) == ["reference_empty"]


def test_reference_given_as_a_file_points_to_its_folder(tmp_path):
    inputs = make(tmp_path, ["A.fastq.gz"])
    inputs["reference"] = inputs["reference"] / "ref.fa"
    report = validate_inputs(inputs)
    assert codes(report) == ["not_a_directory"]
    assert str(tmp_path / "reference") in report.errors[0].message


def test_missing_paths_and_inputs_fail(tmp_path):
    report = validate_inputs({"raw_data": tmp_path / "nope"})
    assert codes(report) == ["input_missing", "path_not_found"]


# -- FASTQ files --------------------------------------------------------------------------

def test_gzipped_fastq_passes(tmp_path):
    assert validate_inputs(make(tmp_path, ["A.fastq.gz"])).ok


@pytest.mark.parametrize("name", ["A.fq.gz", "A.FASTQ.GZ", "A.Fastq.Gz"])
def test_fq_gz_and_any_case_pass(tmp_path, name):
    report = validate_inputs(make(tmp_path, [name]))
    assert report.ok, report.errors
    assert report.summary["samples"][0]["sample"] == "A"


@pytest.mark.parametrize("name", ["A.fastq", "A.fq", "A.FASTQ", "A.fastq.bz2"])
def test_uncompressed_fastq_fails(tmp_path, name):
    inputs = make(tmp_path, ["B.fastq.gz"])
    (inputs["raw_data"] / name).write_text("@r\nACGT\n+\nIIII\n")
    report = validate_inputs(inputs)
    assert codes(report) == ["fastq_not_gzipped"]
    assert name in report.errors[0].message


def test_fastq_gz_that_is_not_gzip_fails(tmp_path):
    inputs = make(tmp_path)
    (inputs["raw_data"] / "A.fastq.gz").write_text("@r\nACGT\n+\nIIII\n")
    assert codes(validate_inputs(inputs)) == ["fastq_not_gzipped"]


def test_empty_fastq_fails(tmp_path):
    inputs = make(tmp_path, ["B.fastq.gz"])
    (inputs["raw_data"] / "A.fastq.gz").write_bytes(b"")
    report = validate_inputs(inputs)
    assert codes(report) == ["fastq_empty"]
    assert report.errors[0].path == inputs["raw_data"] / "A.fastq.gz"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_broken_link_fails(tmp_path):
    inputs = make(tmp_path, ["B.fastq.gz"])
    (inputs["raw_data"] / "A.fastq.gz").symlink_to(tmp_path / "gone.fastq.gz")
    assert codes(validate_inputs(inputs)) == ["unreadable"]


def test_no_fastq_fails(tmp_path):
    report = validate_inputs(make(tmp_path))
    assert codes(report) == ["no_samples"]


def test_other_files_and_hidden_files_are_ignored(tmp_path):
    inputs = make(tmp_path, ["A.fastq.gz"])
    (inputs["raw_data"] / "README.md").write_text("notes")
    (inputs["raw_data"] / ".DS_Store").write_text("x")
    report = validate_inputs(inputs)
    assert report.ok and warning_codes(report) == ["file_ignored"]


def test_disabled_folder_is_ignored_silently_other_folders_warn(tmp_path):
    inputs = make(tmp_path, ["A.fastq.gz"])
    fastq(inputs["raw_data"] / "_disabled" / "OLD_R1.fastq.gz")  # no mate: still fine
    fastq(inputs["raw_data"] / "batch2" / "C.fastq.gz")
    report = validate_inputs(inputs)
    assert report.ok
    assert warning_codes(report) == ["subfolder_ignored"]
    assert [s["sample"] for s in report.summary["samples"]] == ["A"]


# -- pairing ------------------------------------------------------------------------------

@pytest.mark.parametrize("pair", [("A_R1", "A_R2"), ("A_1", "A_2"), ("A_R1_001", "A_R2_001")])
def test_complete_pairs_pass(tmp_path, pair):
    report = validate_inputs(make(tmp_path, [f"{n}.fastq.gz" for n in pair]))
    assert report.ok, report.errors
    (sample,) = report.summary["samples"]
    assert sample["layout"] == "paired-end"
    assert [f["mate"] for f in sample["files"]] == ["R1", "R2"]


@pytest.mark.parametrize("name", ["A_R1.fastq.gz", "A_2.fastq.gz"])
def test_missing_mate_fails(tmp_path, name):
    report = validate_inputs(make(tmp_path, [name, "B.fastq.gz"]))
    assert codes(report) == ["mate_missing"]
    assert report.errors[0].sample == "A" and name in report.errors[0].message


def test_single_end_without_suffix_passes(tmp_path):
    report = validate_inputs(make(tmp_path, ["A.fastq.gz", "B.fastq.gz"]))
    assert report.ok
    assert {s["layout"] for s in report.summary["samples"]} == {"single-end"}


def test_single_and_paired_files_for_one_sample_fail(tmp_path):
    report = validate_inputs(make(tmp_path, ["A.fastq.gz", "A_R1.fastq.gz", "A_R2.fastq.gz"]))
    assert codes(report) == ["layout_mixed"]


@pytest.mark.parametrize(
    "pair, sample",
    [
        (("A_r1.fastq.gz", "A_r2.fastq.gz"), "A"),
        (("A.v2_R1.fastq.gz", "A.v2_R2.fastq.gz"), "A.v2"),
        (("SRR_123_1.fastq.gz", "SRR_123_2.fastq.gz"), "SRR_123"),
        (("S_1_R1.fq.gz", "S_1_R2.fq.gz"), "S_1"),
        (("X_S1_L001_R1_001.fastq.gz", "X_S1_L001_R2_001.fastq.gz"), "X_S1_L001"),
    ],
)
def test_names_the_old_rule_got_wrong_now_pair_correctly(tmp_path, pair, sample):
    report = validate_inputs(make(tmp_path, pair))
    assert report.ok and not report.warnings, (report.errors, report.warnings)
    (found,) = report.summary["samples"]
    assert (found["sample"], found["layout"]) == (sample, "paired-end")


def test_mates_with_different_samples_are_not_paired(tmp_path):
    # S_2_R1 is sample S_2 and S_1_R2 is sample S_1: two incomplete pairs.
    report = validate_inputs(make(tmp_path, ["S_2_R1.fastq.gz", "S_1_R2.fastq.gz"]))
    assert codes(report) == ["mate_missing", "mate_missing"]


@pytest.mark.parametrize(
    "names",
    [
        ["A_R1.fastq.gz", "A_r1.fastq.gz", "A_R2.fastq.gz"],
        ["A.fastq.gz", "A.fq.gz"],
        ["A_1.fastq.gz", "A_R1.fastq.gz", "A_2.fastq.gz"],
        ["A_R1_001.fastq.gz", "A_R1_002.fastq.gz", "A_R2_001.fastq.gz"],
    ],
)
def test_two_files_for_the_same_mate_fail(tmp_path, names):
    report = validate_inputs(make(tmp_path, names))
    assert codes(report) == ["sample_collision"]
    assert report.errors[0].sample == "A"


def test_lanes_run_apart_with_a_warning(tmp_path):
    names = [f"A_L00{lane}_R{mate}_001.fastq.gz" for lane in (1, 2) for mate in (1, 2)]
    report = validate_inputs(make(tmp_path, names + ["B_L001.fastq.gz"]))
    assert report.ok
    assert [s["sample"] for s in report.summary["samples"]] == ["A_L001", "A_L002", "B_L001"]
    assert warning_codes(report) == ["lanes_not_merged"]
    assert report.warnings[0].sample == "A"


def test_unusable_sample_name_fails(tmp_path):
    report = validate_inputs(make(tmp_path, ["_R1.fastq.gz", "a b.fastq.gz", "-x.fastq.gz"]))
    assert codes(report) == ["sample_name_invalid"] * 3


# -- report -------------------------------------------------------------------------------

def test_report_lists_samples_layout_and_sizes(tmp_path):
    inputs = make(tmp_path, ["A_R1.fastq.gz", "A_R2.fastq.gz", "B.fastq.gz"])
    report = validate_inputs(inputs)
    summary = report.summary
    assert summary["sample_count"] == 2
    assert (summary["paired_end"], summary["single_end"]) == (1, 1)
    sizes = {f["name"]: f["size_bytes"] for s in summary["samples"] for f in s["files"]}
    assert sizes["B.fastq.gz"] == (inputs["raw_data"] / "B.fastq.gz").stat().st_size
    assert report.overview.startswith("2 samples (1 paired-end, 1 single-end); reference ref.fa")
    json.dumps(report.as_dict())  # serialisable for the CLI and the agents


def test_validation_only_reads_metadata(tmp_path):
    inputs = make(tmp_path, ["A_R1.fastq.gz", "A_R2.fastq.gz", "B_R1.fastq.gz"])

    def snapshot():
        return sorted((p, p.stat().st_mtime_ns, p.stat().st_size)
                      for p in tmp_path.rglob("*"))

    before = snapshot()
    validate_inputs(inputs)
    assert snapshot() == before


# -- wired into the core ------------------------------------------------------------------

def test_plugin_exposes_the_validator():
    assert plugin().validate_inputs is validate_inputs


def run_plan(inputs, tmp_path, *extra):
    return cli.main(["plan", "echolas", "-i", f"raw_data={inputs['raw_data']}",
                     "-i", f"reference={inputs['reference']}",
                     "--workspace", str(tmp_path), "--no-color", *extra])


def test_plan_reports_the_detected_samples(tmp_path, capsys):
    inputs = make(tmp_path, ["A_R1.fastq.gz", "A_R2.fastq.gz"])
    assert run_plan(inputs, tmp_path, "--json") == 0
    check = json.loads(capsys.readouterr().out)["input_check"]
    assert check["ok"] and check["summary"]["samples"][0]["sample"] == "A"

    assert run_plan(inputs, tmp_path) == 0
    captured = capsys.readouterr()
    assert "1 sample (1 paired-end, 0 single-end)" in captured.err
    assert captured.out.startswith("nextflow run ")


def test_plan_aborts_with_exit_2_on_invalid_inputs(tmp_path, capsys):
    inputs = make(tmp_path, ["A_R1.fastq.gz"])
    assert run_plan(inputs, tmp_path) == 2
    err = capsys.readouterr().err
    assert "1 problem in the inputs" in err and "A_R1.fastq.gz has no mate" in err

    assert run_plan(inputs, tmp_path, "--json") == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["type"] == "invalid_inputs"
    assert [e["code"] for e in error["errors"]] == ["mate_missing"]
    assert error["errors"][0]["sample"] == "A"
    assert "warnings" not in error
    assert not (tmp_path / "runs").exists()


def test_each_input_problem_gets_its_own_line(tmp_path, capsys):
    inputs = make(tmp_path, ["A_R1.fastq.gz", "B_R2.fastq.gz"], genomes=())
    assert run_plan(inputs, tmp_path) == 2
    err = capsys.readouterr().err.splitlines()
    assert err[0] == "error: 3 problems in the inputs:"
    assert len(err) == 4 and all(line.startswith("  - ") for line in err[1:])


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
@pytest.mark.parametrize("mode", [0o000, 0o600])  # no access; read but no search (x)
def test_unreadable_folder_is_an_input_error(tmp_path, capsys, mode):
    inputs = make(tmp_path, ["A.fastq.gz"])
    inputs["raw_data"].chmod(mode)
    try:
        report = validate_inputs(inputs)
        assert codes(report) == ["unreadable"]
        assert "permissions" in report.errors[0].message
        assert run_plan(inputs, tmp_path) == 2
        assert "Cannot read" in capsys.readouterr().err
    finally:
        inputs["raw_data"].chmod(0o755)


def test_run_refuses_invalid_inputs_before_starting(tmp_path, capsys):
    inputs = make(tmp_path, ["A_R1.fastq.gz", "A.fastq.gz"])
    code = cli.main(["run", "echolas", "-i", f"raw_data={inputs['raw_data']}",
                     "-i", f"reference={inputs['reference']}", "--workspace", str(tmp_path),
                     "--nextflow", str(tmp_path / "no-nextflow"), "--no-color"])
    assert code == 2
    assert "mixes single-end and paired-end" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()
