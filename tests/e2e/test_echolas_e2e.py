"""End-to-end smoke test: the full ECHOLAS pipeline on synthetic data (issue #11).

Runs ``xcalibur run echolas --profile test`` for real (Nextflow + conda/micromamba, or the
ECHOLAS image with ``XCALIBUR_E2E_PROFILE=test,docker``) on the
dataset from ``tests/data/make_synthetic.py`` and checks that the filtered VCF holds exactly
the inserted variants with the right genotypes, and that the PCA and the tree are produced
and separate the two clades.

Not part of the default ``pytest`` run. Run it with ``pytest -m e2e`` (see CONTRIBUTING.md).

Environment:
    XCALIBUR_E2E_REQUIRED=1   fail, instead of skip, when Nextflow or micromamba is missing
    XCALIBUR_E2E_PROFILE      workflow profile(s) (default "test"; "test,docker" runs every
                              step in the ECHOLAS image, which must be built: issue #4)
    XCALIBUR_E2E_WORKDIR      keep the data and workspace there (the CI uploads it on failure);
                              must be new, empty or left by an earlier run of this test
    XCALIBUR_E2E_TIMEOUT      seconds before the run is stopped (default 900)
    NXF_CONDA_CACHEDIR        where Nextflow keeps the conda environments (cache it)
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
sys.path.insert(0, str(DATA_DIR))
import make_synthetic as synth  # noqa: E402

pytestmark = pytest.mark.e2e

DEFAULT_TIMEOUT = 900  # 15 minutes, the target of issue #11
GRACE = 60  # seconds xcalibur gets to stop Nextflow after SIGTERM
MARKER = ".xcalibur-e2e"  # marks a work directory this test may wipe
PROFILE = os.environ.get("XCALIBUR_E2E_PROFILE", "test")
USES_DOCKER = "docker" in PROFILE.split(",")


@pytest.fixture(autouse=True)
def _no_network():
    """Lift the suite's network block: conda environments are downloaded. (The block only
    patches this Python process; it is lifted so the intent is explicit.)"""


def _require_tools() -> None:
    needed = ("nextflow", "docker") if USES_DOCKER else ("nextflow", "micromamba")
    missing = [tool for tool in needed if shutil.which(tool) is None]
    if not missing:
        return
    message = f"end-to-end test needs {', '.join(missing)} on PATH"
    if os.environ.get("XCALIBUR_E2E_REQUIRED") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    """Generate the data, run the pipeline once and return the paths."""
    _require_tools()
    base = os.environ.get("XCALIBUR_E2E_WORKDIR")
    root = Path(base) if base else tmp_path_factory.mktemp("e2e")
    marker = root / MARKER
    if root.exists() and any(root.iterdir()):
        # Only a directory this test created earlier is wiped, never one picked by mistake.
        if not marker.is_file():
            pytest.fail(f"{root} is not empty and was not created by this test; pick another")
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text("created by tests/e2e/test_echolas_e2e.py; safe to delete\n")
    data = root / "data"
    variants = synth.generate(data)
    workspace = root / "workspace"

    command = [
        sys.executable, "-c", "from xcalibur.cli import main; raise SystemExit(main())",
        "run", "echolas", "--profile", PROFILE, "--run-id", "smoke", "--json", "--no-color",
        "--workspace", str(workspace),
        "-i", f"raw_data={data / 'raw_data'}", "-i", f"reference={data / 'reference'}",
    ]  # fmt: skip
    timeout = int(os.environ.get("XCALIBUR_E2E_TIMEOUT", DEFAULT_TIMEOUT))
    with (root / "xcalibur.stderr.log").open("w") as err:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=err, text=True)  # noqa: S603
        try:
            stdout, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGTERM)  # xcalibur stops Nextflow and its children
            try:
                proc.communicate(timeout=GRACE)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            pytest.fail(f"pipeline did not finish within {timeout}s (see {root})")

    run_dir = workspace / "runs" / "smoke"
    log = run_dir / "nextflow.log"
    tail = "\n".join(log.read_text().splitlines()[-40:]) if log.exists() else "(no log)"
    assert proc.returncode == 0, f"xcalibur run exited {proc.returncode}\n{tail}"
    result = json.loads(stdout)
    return {"result": result, "run_dir": run_dir, "variants": variants, "log_tail": tail}


def _find(run_dir: Path, pattern: str) -> Path:
    matches = sorted(run_dir.glob(pattern))
    assert matches, f"no {pattern} in {run_dir}"
    return matches[0]


def _read_vcf(path: Path) -> tuple[list[str], dict[tuple[str, int, str, str], dict[str, str]]]:
    samples: list[str] = []
    calls = {}
    with gzip.open(path, "rt") as fh:  # BGZF is valid gzip
        for line in fh:
            if line.startswith("##"):
                continue
            fields = line.rstrip("\n").split("\t")
            if line.startswith("#"):
                samples = fields[9:]
                continue
            fmt = fields[8].split(":")
            genotypes = {
                s: dict(zip(fmt, v.split(":"), strict=False))["GT"].replace("|", "/")
                for s, v in zip(samples, fields[9:], strict=True)
            }
            calls[(fields[0], int(fields[1]), fields[3], fields[4])] = genotypes
    return samples, calls


def test_run_succeeds_with_every_output(run):
    result = run["result"]
    assert result["status"] == "succeeded"
    assert result["missing_outputs"] == [], run["log_tail"]
    record = json.loads(Path(result["record_path"]).read_text())
    assert record["status"] == "succeeded"
    command = record["command"]
    assert command[command.index("-profile") + 1] == PROFILE


def test_record_lists_the_tools_at_their_pinned_versions(run):
    """Issue #70: the record says which tool versions ran, in both profiles."""
    envs = Path(__file__).resolve().parents[2] / (
        "packages/xcalibur-echolas/src/xcalibur_echolas/workflow/envs")
    pinned = {}
    for name in ("echolas.yml", "echolas-pca.yml"):
        for dep in yaml.safe_load((envs / name).read_text())["dependencies"]:
            package, _, version = dep.partition("=")
            if version:
                pinned[package] = version
    software = json.loads(Path(run["result"]["record_path"]).read_text())["software"]
    tools = software["tools"]
    # Every step that ran is listed; fastp only runs on the single-end or low-quality paths.
    assert set(pinned) - {"fastp"} <= set(tools), software["notes"]
    for package, version in tools.items():
        assert pinned.get(package) == version, f"{package} ran {version}, not the pinned one"
    assert software["processes"]["PCA_PHYLOGENY:RUN_PCA"]
    if USES_DOCKER:
        (container,) = software["containers"]
        assert container["image"].startswith("ghcr.io/brunofomena/xcalibur-echolas:")
        assert container["id"].startswith("sha256:")


def test_auto_mode_picks_conservative_trimming(run):
    # The reads are about Q36, so AUTO must choose the conservative (Trimmomatic) path.
    trimmed = run["run_dir"] / "results" / "trimmed"
    assert sorted(p.name for p in trimmed.iterdir()) == ["pe_conservative"]
    paired = (trimmed / "pe_conservative").glob("*_paired.fastq.gz")
    samples = {p.name.split("_R")[0] for p in paired}
    assert samples == set(synth.SAMPLES)


def test_vcf_has_exactly_the_inserted_variants(run):
    samples, calls = _read_vcf(_find(run["run_dir"], "results/filtering/final/strain_PASS.vcf.gz"))
    assert sorted(samples) == sorted(synth.SAMPLES)

    expected = {(v.contig, v.pos, v.ref, v.alt): v for v in run["variants"]}
    missing = sorted(set(expected) - set(calls))
    extra = sorted(set(calls) - set(expected))
    assert not missing, f"inserted variants not in the final VCF: {missing}"
    assert not extra, f"variants in the final VCF that were never inserted: {extra}"

    wrong = []
    for key, variant in expected.items():
        for sample in synth.SAMPLES:
            want = "1/1" if sample in variant.samples else "0/0"
            if calls[key][sample] != want:
                wrong.append((key, sample, calls[key][sample], want))
    assert not wrong, f"wrong genotypes (site, sample, got, expected): {wrong}"


def test_pca_separates_the_clades(run):
    pdf = _find(run["run_dir"], "results/pca/**/strain_PC1_PC2.pdf")
    assert pdf.read_bytes()[:4] == b"%PDF"
    eigenvec = _find(run["run_dir"], "results/pca/**/strain.eigenvec")
    pc1 = {}
    for line in eigenvec.read_text().splitlines():
        fields = line.split()
        if fields and fields[0] != "#FID":
            pc1[fields[1]] = float(fields[2])
    assert sorted(pc1) == sorted(synth.SAMPLES)
    signs = {clade: {pc1[s] > 0 for s in members} for clade, members in synth.CLADES.items()}
    assert all(len(s) == 1 for s in signs.values()), f"PC1 mixes the clades: {pc1}"
    assert signs["A"] != signs["B"], f"PC1 does not separate the clades: {pc1}"


def _leaf_sets(newick: str) -> tuple[set[str], list[set[str]]]:
    """All leaf names, and the leaf set below each internal node, of a Newick tree."""
    # Drop branch lengths (":0.01") and internal-node labels (support values after ")").
    text = re.sub(r":[^,();]*", "", newick.strip().rstrip(";"))
    text = re.sub(r"\)[^,();]+", ")", text)
    stack: list[set[str]] = [set()]
    clades: list[set[str]] = []
    for token in re.findall(r"[(),]|[^(),]+", text):
        if token == "(":
            stack.append(set())
        elif token == ")":
            clade = stack.pop()
            clades.append(clade)
            stack[-1] |= clade
        elif token != ",":
            stack[-1].add(token.strip().strip("'"))
    return stack[0], clades


def test_tree_has_every_sample_and_both_clades(run):
    tree = _find(run["run_dir"], "results/phylogeny/tree/**/strain_tree.treefile")
    leaves, clades = _leaf_sets(tree.read_text())
    assert leaves == set(synth.SAMPLES)
    # IQ-TREE writes an unrooted tree: a clade shows up as one side of a split.
    splits = clades + [leaves - c for c in clades]
    for clade, members in synth.CLADES.items():
        assert set(members) in splits, f"clade {clade} is not a split of: {tree.read_text()}"
