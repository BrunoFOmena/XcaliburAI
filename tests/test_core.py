from pathlib import Path

import pytest

from xcalibur.agents.supervisor import Supervisor
from xcalibur.execution.base import RunSpec
from xcalibur.execution.nextflow import NextflowEngine
from xcalibur.manifest import DomainManifest
from xcalibur.provenance import RunRecord
from xcalibur.registry import DomainPlugin, DomainRegistry
from xcalibur.version import __version__


def plugin(name="demo", keywords=("alpha",)):
    manifest = DomainManifest.model_validate(
        {
            "name": name,
            "display_name": name,
            "version": "0.0.0",
            "keywords": list(keywords),
            "workflow": {"entrypoint": "main.nf", "profile": "standard"},
            "params": [{"name": "mode", "default": "AUTO"}],
        }
    )
    return DomainPlugin(manifest=manifest, root=Path("/pkg"))


def test_version_is_zero():
    assert __version__ == "0.0.0"


def test_registry_rejects_duplicates_and_unknown():
    registry = DomainRegistry()
    registry.register(plugin())
    with pytest.raises(ValueError):
        registry.register(plugin())
    with pytest.raises(KeyError, match="unknown domain"):
        registry.get("nope")


def test_supervisor_routes_by_keyword():
    registry = DomainRegistry()
    registry.register(plugin("a", ("leishmania", "vcf")))
    registry.register(plugin("b", ("rna-seq",)))
    decision = Supervisor(registry).route("Call variants in Leishmania and give me a VCF")
    assert decision.domain == "a"
    assert Supervisor(registry).route("hello").domain is None


def test_nextflow_command_is_deterministic():
    spec = RunSpec(plugin=plugin(), params={"mode": "AUTO"}, inputs={"reference": Path("ref.fa")})
    cmd = NextflowEngine().build_command(spec)
    assert cmd[:3] == ["nextflow", "run", "/pkg/main.nf"]
    assert ["-profile", "standard"] == cmd[3:5]
    assert "--mode" in cmd and "--reference" in cmd


def test_dry_run_does_not_execute():
    spec = RunSpec(plugin=plugin(), params={}, dry_run=True)
    result = NextflowEngine(executable="definitely-not-installed").run(spec)
    assert result.status == "planned" and result.returncode is None


def test_provenance_roundtrip(tmp_path):
    record = RunRecord(domain="demo", domain_version="0.0.0", engine="nextflow",
                       command=["nextflow"], params={"mode": "AUTO"})
    path = record.write(tmp_path)
    assert RunRecord.model_validate_json(path.read_text()).run_id == record.run_id
