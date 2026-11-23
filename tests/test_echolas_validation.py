"""ECHOLAS input validation (issue #6): every rule has a passing and a failing case."""

import gzip
import json
import os

import pytest

from xcalibur import cli
from xcalibur_echolas import plugin
from xcalibur_echolas.validation import pipeline_mate, pipeline_sample, validate_inputs

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


# -- the pipeline's own naming rules -------------------------------------------------------
# Expected values were produced by running main.nf's and trimming_qc.nf's expressions in
# Nextflow on these exact names.

@pytest.mark.parametrize(
    "name, sample, mate",
    [
        ("S1_R1.fastq.gz", "S1", "R1"),
        ("S1_R2.fastq.gz", "S1", "R2"),
        ("S2_1.fastq.gz", "S2", "R1"),
        ("S2_2.fastq.gz", "S2", "R2"),
        ("S3.fastq.gz", "S3", "SE"),
        ("S4.lane1_R1.fastq.gz", "S4", "R1"),
        ("S5_r1.fastq.gz", "S5_r1", "SE"),
        ("S6_R1_001.fastq.gz", "S6_001", "R1"),
        ("S7_1_R2.fastq.gz", "S7", "R1"),
        ("S8_10.fastq.gz", "S80", "SE"),
        ("SRR_123_1.fastq.gz", "SRR23", "R1"),
        ("_R1.fastq.gz", "", "R1"),
    ],
)
def test_sample_and_mate_follow_the_pipeline(name, sample, mate):
    assert pipeline_sample(name) == sample
    assert pipeline_mate(name) == mate


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


@pytest.mark.parametrize("name", ["A.fastq", "A.fq", "A.fq.gz", "A.FASTQ.GZ", "A.fastq.bz2"])
def test_fastq_not_named_fastq_gz_fails(tmp_path, name):
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


def test_lowercase_mate_suffix_fails(tmp_path):
    report = validate_inputs(make(tmp_path, ["A_r1.fastq.gz", "A_r2.fastq.gz"]))
    assert codes(report) == ["mate_suffix_lowercase", "mate_suffix_lowercase"]
    assert "_R1" in report.errors[0].message


def test_lanes_that_collapse_into_one_sample_fail(tmp_path):
    names = ["A.L1_R1.fastq.gz", "A.L1_R2.fastq.gz", "A.L2_R1.fastq.gz", "A.L2_R2.fastq.gz"]
    report = validate_inputs(make(tmp_path, names))
    assert codes(report) == ["sample_collision"]
    assert report.errors[0].sample == "A"


def test_two_files_read_as_the_same_mate_fail(tmp_path):
    # "_1_" in S_1_R2 makes the pipeline read it as R1 too.
    report = validate_inputs(make(tmp_path, ["S_2_R1.fastq.gz", "S_1_R2.fastq.gz"]))
    assert codes(report) == ["sample_collision"]


def test_renamed_sample_is_a_warning(tmp_path):
    report = validate_inputs(make(tmp_path, ["SRR_123_1.fastq.gz", "SRR_123_2.fastq.gz"]))
    assert report.ok
    assert warning_codes(report) == ["sample_renamed", "sample_renamed"]
    assert report.summary["samples"][0]["sample"] == "SRR23"
    assert "SRR_123" in report.warnings[0].message


def test_unusable_sample_name_fails(tmp_path):
    report = validate_inputs(make(tmp_path, ["_R1.fastq.gz", "_R2.fastq.gz", "a b.fastq.gz"]))
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
    assert not (tmp_path / "runs").exists()


def test_run_refuses_invalid_inputs_before_starting(tmp_path, capsys):
    inputs = make(tmp_path, ["A_r1.fastq.gz", "A_r2.fastq.gz"])
    code = cli.main(["run", "echolas", "-i", f"raw_data={inputs['raw_data']}",
                     "-i", f"reference={inputs['reference']}", "--workspace", str(tmp_path),
                     "--nextflow", str(tmp_path / "no-nextflow"), "--no-color"])
    assert code == 2
    assert "uppercase '_R1'/'_R2'" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()
