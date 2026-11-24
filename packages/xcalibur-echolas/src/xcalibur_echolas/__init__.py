"""ECHOLAS domain package for XcaliburAI."""

from pathlib import Path

from xcalibur.agents.base import AgentSpec
from xcalibur.manifest import load_manifest
from xcalibur.registry import DomainPlugin
from xcalibur_echolas.tools import TOOLS
from xcalibur_echolas.validation import validate_inputs

ROOT = Path(__file__).parent
PROMPTS = ROOT / "prompts"

EXECUTOR_TOOLS = (
    "describe_domain",
    "plan_run",
    "run_pipeline",
    "get_run_status",
    "read_results",
)
INTERPRETER_TOOLS = (
    "get_run_status",
    "read_results",
    *(f"echolas__{tool.name}" for tool in TOOLS),
)


def prompt(name: str) -> str:
    """A system-prompt file of this package (versioned with the code, not inside it)."""
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").strip()


def plugin() -> DomainPlugin:
    return DomainPlugin(
        manifest=load_manifest(ROOT / "manifest.yaml"),
        root=ROOT,
        agents=(
            AgentSpec(
                "echolas_executor",
                "execution",
                "Configures and launches the pipeline.",
                instructions=prompt("executor"),
                tools=EXECUTOR_TOOLS,
            ),
            AgentSpec(
                "echolas_interpreter",
                "interpretation",
                "Reads VCF, PCA and tree outputs and explains population structure.",
                instructions=prompt("interpreter"),
                tools=INTERPRETER_TOOLS,
            ),
        ),
        tools=TOOLS,
        validate_inputs=validate_inputs,
    )
