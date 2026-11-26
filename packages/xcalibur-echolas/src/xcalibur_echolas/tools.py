"""ECHOLAS result tools for the agents (issue #9).

Registered as ``echolas__<name>``. All of them only read a finished run in the workspace,
take nothing but a ``run_id`` (and for the VCF, which call set), never leave the run
directory, and are ``grounded``: the numbers an answer gives about the results must come
from them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from xcalibur.agents.tools import Tool, ToolContext, ToolError, ToolInput
from xcalibur.provenance import RunRecord
from xcalibur.workspace import RUN_ID_PATTERN
from xcalibur_echolas.results import (
    ResultsError,
    read_pca,
    summarize_tree,
    summarize_vcf,
)

DOMAIN = "echolas"
FEW_SAMPLES = 10
HIGH_MISSING_PERCENT = 20.0

VCF_SETS = {
    "final": "results/filtering/final/strain_PASS.vcf.gz",
    "pca": "results/filtering/pca/strain_snps_Fpca.vcf.gz",
    "phylo": "results/filtering/phylo/strain_snps_Fphylo.vcf.gz",
}
EIGENVAL = "results/pca/**/*.eigenval"
EIGENVEC = "results/pca/**/*.eigenvec"
TREEFILE = "results/phylogeny/tree/**/*.treefile"


class RunArgs(ToolInput):
    run_id: str = Field(pattern=RUN_ID_PATTERN, max_length=64,
                        description="Run identifier of a finished ECHOLAS run.")


class VcfArgs(RunArgs):
    call_set: Literal["final", "pca", "phylo"] = Field(
        default="final",
        description="final: SNPs and indels that PASS (the joint VCF); pca: SNPs used "
        "for the PCA; phylo: SNPs used for the tree.",
    )


def vcf_summary(args: VcfArgs, ctx: ToolContext) -> dict[str, Any]:
    run_dir = _finished_run(ctx, args.run_id)
    path = _one_file(run_dir, VCF_SETS[args.call_set], f"{args.call_set} VCF")
    return {"call_set": args.call_set, **_read(summarize_vcf, path)}


def pca_summary(args: RunArgs, ctx: ToolContext) -> dict[str, Any]:
    run_dir = _finished_run(ctx, args.run_id)
    eigenval = _one_file(run_dir, EIGENVAL, "PCA eigenvalues (.eigenval)")
    eigenvec = _one_file(run_dir, EIGENVEC, "PCA eigenvectors (.eigenvec)")
    return _read(read_pca, eigenval, eigenvec)


def tree_summary(args: RunArgs, ctx: ToolContext) -> dict[str, Any]:
    run_dir = _finished_run(ctx, args.run_id)
    return _read(summarize_tree, _one_file(run_dir, TREEFILE, "tree (.treefile)"))


def run_summary(args: RunArgs, ctx: ToolContext) -> dict[str, Any]:
    """Everything the interpreter needs in one call, plus the caveats that apply."""
    run_dir = _finished_run(ctx, args.run_id)
    summary: dict[str, Any] = {"run_id": args.run_id, "missing": []}
    readers = {
        "vcf": lambda: vcf_summary(VcfArgs(run_id=args.run_id), ctx),
        "pca_snps": lambda: _snp_count(vcf_summary(
            VcfArgs(run_id=args.run_id, call_set="pca"), ctx)),
        "phylo_snps": lambda: _snp_count(vcf_summary(
            VcfArgs(run_id=args.run_id, call_set="phylo"), ctx)),
        "pca": lambda: pca_summary(args, ctx),
        "tree": lambda: tree_summary(args, ctx),
    }
    for key, read in readers.items():
        try:
            summary[key] = read()
        except ToolError as exc:
            summary["missing"].append({"part": key, "reason": str(exc)})
    if len(summary["missing"]) == len(readers):
        raise ToolError(f"No ECHOLAS results found in {run_dir}.", kind="results_missing")
    summary["caveats"] = caveats(summary)
    # The limits behind the caveats, as numbers an answer may quote (#68).
    summary["caveat_thresholds"] = {
        "few_samples": FEW_SAMPLES,
        "high_missing_percent": HIGH_MISSING_PERCENT,
    }
    return summary


def caveats(summary: dict[str, Any]) -> list[dict[str, str]]:
    """Limits of the results the answer must state, decided here and not by the model."""
    out: list[dict[str, str]] = []
    vcf, pca, tree = summary.get("vcf"), summary.get("pca"), summary.get("tree")
    samples = (vcf or pca or tree or {}).get("sample_count", (tree or {}).get("leaf_count"))
    if samples is not None and samples < FEW_SAMPLES:
        out.append({"code": "few_samples", "message": (
            f"Only {samples} samples: describe the groups seen in these samples; do not "
            "draw conclusions about whole populations, geography or evolution.")})
    if vcf:
        high = [s["sample"] for s in vcf["samples"]
                if s["missing_percent"] > HIGH_MISSING_PERCENT]
        if high:
            out.append({"code": "high_missingness", "message": (
                f"Samples with more than {HIGH_MISSING_PERCENT:g}% missing genotypes: "
                f"{', '.join(high)}; their placement in the PCA and tree is less reliable.")})
        if vcf["by_type"].get("snp", 0) and not any(s["het"] for s in vcf["samples"]):
            out.append({"code": "no_heterozygotes", "message": (
                "No heterozygous calls: expected for clonal or highly inbred strains, but "
                "check it is not an artefact before discussing heterozygosity.")})
    if pca and not pca["all_pcs_computed"]:
        out.append({"code": "pca_partial", "message": pca["scope_note"]})
    if tree:
        if tree["unsupported_splits"]:
            out.append({"code": "weak_splits", "message": (
                f"{tree['unsupported_splits']} of {tree['split_count']} splits are not "
                "supported (SH-aLRT < 80 or UFBoot < 95): do not present them as groups.")})
        out.append({"code": "branch_lengths", "message": (
            "The tree is inferred from SNP sites only with GTR+G and no ascertainment "
            "correction, so branch lengths are inflated and not comparable with genome-wide "
            "distances (issue #52): interpret the topology and support, not the lengths.")})
    out.append({"code": "ploidy", "message": (
        "Variants were called as diploid (HaplotypeCaller --ploidy 2); Leishmania is "
        "often aneuploid, so heterozygosity and allele balance can be biased.")})
    return out


TOOLS: tuple[Tool, ...] = (
    Tool(
        "summarize_run",
        "Summary of a finished ECHOLAS run: samples and variants (final VCF), SNPs used "
        "for PCA and tree, PCA eigenvalues and PC1/PC2 per sample, tree splits with "
        "SH-aLRT/UFBoot support, and the caveats that apply. Start here.",
        RunArgs, run_summary, grounded=True,
    ),
    Tool(
        "summarize_vcf",
        "Samples, variant counts by type and filter, per-contig counts and per-sample "
        "genotype counts of one ECHOLAS call set (final, pca or phylo).",
        VcfArgs, vcf_summary, grounded=True,
    ),
    Tool(
        "read_pca",
        "PCA of an ECHOLAS run: eigenvalues, each PC's share of the computed PCs, and "
        "PC1/PC2 per sample.",
        RunArgs, pca_summary, grounded=True,
    ),
    Tool(
        "read_tree",
        "Maximum-likelihood tree of an ECHOLAS run: leaves, splits with SH-aLRT/UFBoot "
        "support and whether each is supported, branch lengths.",
        RunArgs, tree_summary, grounded=True,
    ),
)


# -- helpers ------------------------------------------------------------------------------


def _finished_run(ctx: ToolContext, run_id: str) -> Path:
    if ctx.workspace is None:
        raise ToolError("No workspace is configured.", kind="no_workspace")
    run_dir = ctx.workspace.run_dir(run_id)
    if not run_dir.is_dir():
        raise ToolError(f"There is no run {run_id!r} in the workspace.", kind="unknown_run")
    attempts = RunRecord.next_attempt(run_dir, run_id) - 1
    if attempts > 0:
        path = run_dir / RunRecord.filename_for(run_id, attempts)
        try:
            record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolError(f"Could not read {path.name}: {exc}", kind="bad_record") from None
        if record.domain != DOMAIN:
            raise ToolError(f"Run {run_id!r} is a {record.domain!r} run, not ECHOLAS.",
                            kind="wrong_domain")
        if record.status == "running":
            raise ToolError(f"Run {run_id!r} is still running; check get_run_status.",
                            kind="run_not_finished")
    return run_dir


def _one_file(run_dir: Path, pattern: str, what: str) -> Path:
    root = run_dir.resolve()
    found = sorted(p for p in run_dir.glob(pattern)
                   if p.is_file() and p.resolve().is_relative_to(root))
    if not found:
        raise ToolError(f"No {what} in {run_dir} ({pattern}); the run may have failed "
                        "before this step.", kind="results_missing")
    return found[0]


def _read(reader, *paths: Path) -> dict[str, Any]:
    try:
        return reader(*paths)
    except ResultsError as exc:
        raise ToolError(str(exc), kind="bad_results") from None


def _snp_count(vcf: dict[str, Any]) -> dict[str, Any]:
    return {"variant_count": vcf["variant_count"], "by_type": vcf["by_type"],
            "sample_count": vcf["sample_count"]}

