import re

import pytest

from xcalibur.manifest import ManifestError
from xcalibur_echolas import plugin

PLUGIN = plugin()
WORKFLOW_DIR = PLUGIN.workflow_path.parent
MODULES = ("trimming_qc", "alignment_variant", "genotyping", "variant_filtering", "pca_phylogeny")


def _config_params() -> dict[str, str]:
    """Read the ``params { ... }`` block of nextflow.config as name -> default."""
    text = (WORKFLOW_DIR / "nextflow.config").read_text(encoding="utf-8")
    block = re.search(r"^params\s*\{(.*?)^\}", text, re.S | re.M)
    assert block, "nextflow.config has no params block"
    return dict(re.findall(r"^\s*(\w+)\s*=\s*'([^']*)'", block.group(1), re.M))


def _workflow_sources() -> str:
    files = [PLUGIN.workflow_path, *(WORKFLOW_DIR / "modules").glob("*.nf")]
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def test_echolas_manifest_loads_and_resolves_defaults():
    assert PLUGIN.name == "echolas"
    assert PLUGIN.manifest.resolve_params({}) == {"mode": "AUTO"}
    assert {a.role for a in PLUGIN.agents} == {"execution", "interpretation"}


def test_mode_accepts_only_the_implemented_value():
    assert PLUGIN.manifest.resolve_params({"mode": "AUTO"}) == {"mode": "AUTO"}
    with pytest.raises(ManifestError):
        PLUGIN.manifest.resolve_params({"mode": "AGGRESSIVE"})


def test_workflow_files_are_installed():
    assert PLUGIN.workflow_path.is_file()
    assert (WORKFLOW_DIR / "nextflow.config").is_file()
    for module in MODULES:
        assert (WORKFLOW_DIR / "modules" / f"{module}.nf").is_file()


def test_manifest_and_nextflow_params_match():
    config = _config_params()
    manifest_params = {p.name: p.default for p in PLUGIN.manifest.params}
    input_names = {i.name for i in PLUGIN.manifest.inputs}

    # Every manifest param exists in the pipeline with the same default.
    for name, default in manifest_params.items():
        assert config.get(name) == default, f"param '{name}' differs from nextflow.config"
    # Every input is passed to Nextflow as a param of the same name.
    assert input_names <= set(config), "manifest inputs missing from nextflow.config params"
    # The pipeline has no parameter the manifest does not declare.
    assert set(config) == set(manifest_params) | input_names
    used = set(re.findall(r"\bparams\.(\w+)", _workflow_sources()))
    assert used <= set(config), f"undeclared params used by the workflow: {used - set(config)}"


def test_inputs_are_not_hardcoded_directories():
    main = PLUGIN.workflow_path.read_text(encoding="utf-8")
    assert '"raw_data/' not in main and '"reference/' not in main
    assert "${params.raw_data}" in main and "${params.reference}" in main


def test_scripts_used_by_modules_exist():
    referenced = re.findall(r"\$\{projectDir\}/scripts/([\w.\-]+)", _workflow_sources())
    assert referenced, "modules should resolve helper scripts from ${projectDir}/scripts"
    for name in referenced:
        assert (WORKFLOW_DIR / "scripts" / name).is_file(), f"missing script {name}"
    # No module may still point at a scripts/ folder relative to the launch directory.
    assert not re.search(r'fromPath\("scripts/', _workflow_sources())


def test_mit_notice_and_attribution_are_shipped():
    notice = (WORKFLOW_DIR / "NOTICE.md").read_text(encoding="utf-8")
    assert "MIT License" in notice
    assert "https://github.com/MCamposDA/ECHOLAS" in notice
