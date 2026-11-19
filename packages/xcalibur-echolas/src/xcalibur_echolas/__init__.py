"""ECHOLAS domain package for XcaliburAI."""

from pathlib import Path

from xcalibur.agents.base import AgentSpec
from xcalibur.manifest import load_manifest
from xcalibur.registry import DomainPlugin

ROOT = Path(__file__).parent


def plugin() -> DomainPlugin:
    return DomainPlugin(
        manifest=load_manifest(ROOT / "manifest.yaml"),
        root=ROOT,
        agents=(
            AgentSpec("echolas_executor", "execution", "Configures and launches the pipeline."),
            AgentSpec(
                "echolas_interpreter",
                "interpretation",
                "Reads VCF, PCA and tree outputs and explains population structure.",
            ),
        ),
    )
