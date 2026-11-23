"""ECHOLAS result readers and tools (issue #9), on outputs of the synthetic dataset."""

import gzip
import json
import shutil
from pathlib import Path

import pytest

from xcalibur.agents.core_tools import build_tool_registry
from xcalibur.agents.tools import ToolContext, ToolError
from xcalibur.provenance import RunRecord
from xcalibur.registry import DomainRegistry
from xcalibur.workspace import Workspace
from xcalibur_echolas import plugin
from xcalibur_echolas.results import (
    ResultsError,
    parse_newick,
    read_pca,
    summarize_tree,
    summarize_vcf,
)
from xcalibur_echolas.tools import (
    EIGENVAL,
    TREEFILE,
    VCF_SETS,
    RunArgs,
    VcfArgs,
    pca_summary,
    run_summary,
    tree_summary,
    vcf_summary,
)

OUTPUTS = Path(__file__).parent / "data" / "echolas_outputs"
LAYOUT = {
    "strain_PASS.vcf.gz": "results/filtering/final",
    "strain_snps_Fpca.vcf.gz": "results/filtering/pca",
    "strain_snps_Fphylo.vcf.gz": "results/filtering/phylo",
    "strain.eigenval": "results/pca/pca_output",
    "strain.eigenvec": "results/pca/pca_output",
    "strain_tree.treefile": "results/phylogeny/tree/phylogeny",
}
SAMPLES = ["SA1", "SA2", "SA3", "SB1", "SB2", "SB3"]


def make_run(tmp_path, run_id="r1", skip=(), status="succeeded", domain="echolas"):
    workspace = Workspace(tmp_path)
    run_dir = workspace.run_dir(run_id)
    for name, folder in LAYOUT.items():
        if name in skip:
            continue
        (run_dir / folder).mkdir(parents=True, exist_ok=True)
        shutil.copy(OUTPUTS / name, run_dir / folder / name)
    run_dir.mkdir(parents=True, exist_ok=True)
    record = RunRecord(run_id=run_id, domain=domain, domain_version="0.0.0", engine="nextflow",
                       command=["nextflow"], params={}, status=status, attempt=1)
    record.write(run_dir)
    return ToolContext(domains=DomainRegistry(), workspace=workspace)


# -- VCF ----------------------------------------------------------------------------------

def test_final_vcf_of_the_synthetic_run():
    summary = summarize_vcf(OUTPUTS / "strain_PASS.vcf.gz")
    assert summary["sample_count"] == 6
    assert [s["sample"] for s in summary["samples"]] == SAMPLES
    # make_synthetic.py inserts 8 SNPs per clade x 2, 3 shared, 1 private x 6, 2 indels.
    assert summary["variant_count"] == 27
    assert summary["by_type"] == {"indel": 2, "snp": 25}
    assert summary["by_filter"] == {"PASS": 27}
    assert summary["contig_count"] == 3
    for sample in summary["samples"]:
        assert sample["het"] == 0 and sample["missing"] == 0
        assert sample["hom_ref"] + sample["hom_alt"] == 27


def write_vcf(path, rows, samples=("S1", "S2")):
    header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t"
    lines = [header + "\t".join(samples)]
    lines += ["\t".join(row) for row in rows]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_vcf_types_genotypes_and_plain_text(tmp_path):
    rows = [
        ["c1", "1", ".", "A", "G", "50", "PASS", ".", "GT:DP", "0/1:10", "1|1:9"],
        ["c1", "2", ".", "AT", "A", "50", "LowQD", ".", "GT", "./.", "0/0"],
        ["c2", "3", ".", "AC", "GT", "50", "PASS", ".", "GT", "0/0", "1/1"],
        ["c2", "4", ".", "A", "G,AT", "50", "PASS", ".", "GT", "1/2", "0/0"],
        ["c2", "5", ".", "A", "<DEL>", "50", "PASS", ".", "GT", "0/1", "."],
        ["c2", "6", ".", "A", ".", "50", "PASS", ".", "DP", "5", "6"],
    ]
    summary = summarize_vcf(write_vcf(tmp_path / "x.vcf", rows))
    assert summary["by_type"] == {"indel": 1, "mixed": 1, "mnp": 1, "no_alt": 1,
                                  "snp": 1, "symbolic": 1}
    assert summary["by_filter"] == {"LowQD": 1, "PASS": 5}
    assert summary["multiallelic"] == 1
    s1, s2 = summary["samples"]
    assert (s1["het"], s1["hom_ref"], s1["missing"]) == (3, 1, 2)
    assert (s2["hom_alt"], s2["hom_ref"], s2["missing"]) == (2, 2, 2)
    assert s1["missing_percent"] == round(100 * 2 / 6, 2)


def test_vcf_errors(tmp_path):
    (tmp_path / "bad.vcf").write_text("not a vcf\n")
    with pytest.raises(ResultsError, match="not a VCF record"):
        summarize_vcf(tmp_path / "bad.vcf")
    (tmp_path / "empty.vcf").write_text("##fileformat=VCFv4.2\n")
    with pytest.raises(ResultsError, match="is it a VCF"):
        summarize_vcf(tmp_path / "empty.vcf")
    data = gzip.compress(b"##fileformat=VCFv4.2\n" * 50)
    (tmp_path / "cut.vcf.gz").write_bytes(data[: len(data) // 2])
    with pytest.raises(ResultsError, match="Cannot read"):
        summarize_vcf(tmp_path / "cut.vcf.gz")


# -- PCA ----------------------------------------------------------------------------------

def test_pca_of_the_synthetic_run():
    pca = read_pca(OUTPUTS / "strain.eigenval", OUTPUTS / "strain.eigenvec")
    assert pca["pcs_computed"] == 6 and pca["sample_count"] == 6
    assert pca["eigenvalues"][:2] == [8.256, 0.576]
    total = 8.256 + 4 * 0.576
    assert pca["percent_of_computed"][0] == round(100 * 8.256 / total, 2)
    assert pca["all_pcs_computed"] is True
    pc1 = {c["sample"]: c["PC1"] for c in pca["coordinates"]}
    # The two clades sit on opposite sides of PC1.
    assert {s for s, v in pc1.items() if v > 0} in ({"SA1", "SA2", "SA3"}, {"SB1", "SB2", "SB3"})


def test_pca_partial_and_errors(tmp_path):
    (tmp_path / "e.eigenval").write_text("5\n3\n")
    (tmp_path / "e.eigenvec").write_text("\n".join(f"F{i} S{i} 0.1 0.2" for i in range(5)))
    pca = read_pca(tmp_path / "e.eigenval", tmp_path / "e.eigenvec")
    assert pca["all_pcs_computed"] is False
    assert pca["percent_of_computed"] == [62.5, 37.5]
    (tmp_path / "bad.eigenval").write_text("5\nnan\n")
    with pytest.raises(ResultsError, match="not finite"):
        read_pca(tmp_path / "bad.eigenval", tmp_path / "e.eigenvec")


# -- tree ---------------------------------------------------------------------------------

def test_tree_of_the_synthetic_run():
    tree = summarize_tree(OUTPUTS / "strain_tree.treefile")
    assert tree["leaf_count"] == 6 and tree["leaves"] == SAMPLES
    assert tree["split_count"] == 3
    best = tree["splits"][0]
    assert best["taxa"] == ["SA1", "SA2", "SA3"]
    assert (best["sh_alrt"], best["ufboot"], best["supported"]) == (100.0, 100.0, True)
    assert tree["supported_splits"] == 1 and tree["unsupported_splits"] == 2
    assert tree["total_tree_length"] == 2.4396  # IQ-TREE's own report says 2.4396


def test_newick_quotes_comments_and_single_support(tmp_path):
    root = parse_newick("[&R] ('a b':1,(c,'d''e')90:0.5,f)root;")
    assert sorted(root.leaves()) == ["a b", "c", "d'e", "f"]
    (tmp_path / "t.nwk").write_text("((a,b)97:1,(c,d)50:1,e);")
    tree = summarize_tree(tmp_path / "t.nwk")
    supports = {tuple(s["taxa"]): (s["sh_alrt"], s["ufboot"]) for s in tree["splits"]}
    assert supports == {("a", "b"): (None, 97.0), ("c", "d"): (None, 50.0)}
    assert tree["supported_splits"] == 0  # without SH-aLRT nothing counts as supported


@pytest.mark.parametrize("text", ["(a,b", "(a,b);x", "(a,,b);", "(a,a);", "(a:x,b);"])
def test_bad_newick_is_an_error(tmp_path, text):
    (tmp_path / "t.nwk").write_text(text)
    with pytest.raises(ResultsError):
        summarize_tree(tmp_path / "t.nwk")


# -- tools --------------------------------------------------------------------------------

def test_tools_are_registered_grounded_and_read_only_by_run_id():
    registry = DomainRegistry()
    registry.register(plugin())
    tools = build_tool_registry(registry)
    for name in ("summarize_run", "summarize_vcf", "read_pca", "read_tree"):
        tool = tools.get(f"echolas__{name}")
        assert tool.grounded and not tool.requires_confirmation
        assert set(tool.input_model.model_fields) <= {"run_id", "call_set"}


def test_tools_match_the_readers(tmp_path):
    ctx = make_run(tmp_path)
    run_dir = tmp_path / "runs" / "r1"
    vcf = vcf_summary(VcfArgs(run_id="r1"), ctx)
    assert vcf == {"call_set": "final",
                   **summarize_vcf(run_dir / VCF_SETS["final"])}
    assert pca_summary(RunArgs(run_id="r1"), ctx) == read_pca(
        next(run_dir.glob(EIGENVAL)), next(run_dir.glob(EIGENVAL.replace("val", "vec"))))
    assert tree_summary(RunArgs(run_id="r1"), ctx) == summarize_tree(next(run_dir.glob(TREEFILE)))


def test_run_summary_has_everything_and_the_caveats(tmp_path):
    summary = run_summary(RunArgs(run_id="r1"), make_run(tmp_path))
    assert summary["missing"] == []
    assert summary["vcf"]["variant_count"] == 27
    assert summary["pca_snps"]["by_type"] == {"snp": summary["pca_snps"]["variant_count"]}
    assert summary["tree"]["supported_splits"] == 1
    codes = [c["code"] for c in summary["caveats"]]
    assert codes == ["few_samples", "no_heterozygotes", "weak_splits", "branch_lengths",
                     "ploidy"]
    json.dumps(summary)


def test_run_summary_reports_missing_parts(tmp_path):
    ctx = make_run(tmp_path, skip=("strain_tree.treefile",))
    summary = run_summary(RunArgs(run_id="r1"), ctx)
    assert [m["part"] for m in summary["missing"]] == ["tree"]
    assert "weak_splits" not in [c["code"] for c in summary["caveats"]]


@pytest.mark.parametrize(
    "kwargs, run_id, kind",
    [
        ({}, "nope", "unknown_run"),
        ({"status": "running"}, "r1", "run_not_finished"),
        ({"domain": "other"}, "r1", "wrong_domain"),
        ({"skip": tuple(LAYOUT)}, "r1", "results_missing"),
    ],
)
def test_tool_errors(tmp_path, kwargs, run_id, kind):
    ctx = make_run(tmp_path, **kwargs)
    with pytest.raises(ToolError) as caught:
        run_summary(RunArgs(run_id=run_id), ctx)
    assert caught.value.kind == kind


def test_files_outside_the_run_are_never_read(tmp_path):
    ctx = make_run(tmp_path, skip=("strain_tree.treefile",))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    shutil.copy(OUTPUTS / "strain_tree.treefile", outside / "x.treefile")
    link = tmp_path / "runs" / "r1" / "results" / "phylogeny" / "tree" / "link"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ToolError) as caught:
        tree_summary(RunArgs(run_id="r1"), ctx)
    assert caught.value.kind == "results_missing"


def test_corrupt_output_is_a_tool_error(tmp_path):
    ctx = make_run(tmp_path)
    (tmp_path / "runs/r1/results/phylogeny/tree/phylogeny/strain_tree.treefile").write_text("(")
    with pytest.raises(ToolError) as caught:
        tree_summary(RunArgs(run_id="r1"), ctx)
    assert caught.value.kind == "bad_results"
