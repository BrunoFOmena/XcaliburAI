"""fastp trimming paths of AUTO (issue #81): trim, then filter; never drop for quality alone."""

import re
from pathlib import Path

MODULE = (Path(__file__).resolve().parents[1] / "packages/xcalibur-echolas/src/xcalibur_echolas"
          / "workflow/modules/trimming_qc.nf")
FASTP = ("TRIM_PE_INTERMEDIATE", "TRIM_PE_AGGRESSIVE", "TRIM_SE_BASIC", "TRIM_SE_AGGRESSIVE")


def options(process: str) -> dict[str, str | None]:
    text = MODULE.read_text(encoding="utf-8")
    body = re.search(rf"process {process} \{{(.*?)\n\}}", text, re.S).group(1)
    script = body[body.index("fastp"):]
    return {m.group(1): m.group(2) for m in re.finditer(r"--([\w_]+)(?: +([^\s\\]+))?", script)}


def test_every_fastp_path_trims_with_a_q15_window():
    for process in FASTP:
        opts = options(process)
        assert "cut_tail" in opts, process
        assert opts["cut_window_size"] == "4" and opts["cut_mean_quality"] == "15", process


def test_quality_filter_is_fastps_default_after_trimming():
    # Q30 with the 40% limit dropped whole low-quality samples before #81.
    for process in FASTP:
        opts = options(process)
        assert opts["qualified_quality_phred"] == "15", process
        assert opts["unqualified_percent_limit"] == "40", process


def test_paths_differ_only_where_intended():
    assert "cut_front" not in options("TRIM_PE_INTERMEDIATE")
    assert "cut_right" in options("TRIM_PE_INTERMEDIATE")  # Trimmomatic SLIDINGWINDOW:4:15
    for process in ("TRIM_PE_AGGRESSIVE", "TRIM_SE_AGGRESSIVE"):
        opts = options(process)
        assert {"cut_front", "cut_right"} <= set(opts), process
        assert opts["complexity_threshold"] == "30", process
    assert {"cut_front", "cut_right"}.isdisjoint(options("TRIM_SE_BASIC"))
    lengths = {p: options(p)["length_required"] for p in FASTP}
    assert lengths == {"TRIM_PE_INTERMEDIATE": "50", "TRIM_PE_AGGRESSIVE": "50",
                       "TRIM_SE_BASIC": "36", "TRIM_SE_AGGRESSIVE": "50"}


def test_auto_decides_on_the_lower_mate_of_each_pair():
    """#48: qualities are grouped per sample before the decision (join kept whichever mate
    arrived first), and the lower one decides."""
    text = MODULE.read_text(encoding="utf-8")
    workflow = text[text.index("workflow TRIMMING_QC"):]
    assert "join(qmetrics)" not in workflow
    qmetrics = re.search(r"qmetrics = FASTQC_PARSE\(qc\.zip\)(.*?)\n\n", workflow, re.S).group(1)
    assert ".groupTuple(by: 0)" in qmetrics and "qs.min()" in qmetrics
    assert "paired.combine(qmetrics, by: 0)" in workflow
    assert "single.combine(qmetrics, by: 0)" in workflow
    # The mate goes through FastQC, so R1 and R2 stay apart until they are grouped.
    for process in ("FASTQC_RAW", "FASTQC_PARSE"):
        body = re.search(rf"process {process} \{{(.*?)\n\}}", text, re.S).group(1)
        assert "tuple val(sample), val(mate)" in body, process
