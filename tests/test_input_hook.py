"""The core runs a domain's optional input check before plan and run."""

import pytest

from xcalibur.agents.confirmation import ConfirmationRequest
from xcalibur.agents.core_tools import RunArgs, plan_run, preview_run
from xcalibur.agents.tools import ToolContext, ToolError
from xcalibur.inputs import InputReport
from xcalibur.manifest import DomainManifest
from xcalibur.registry import DomainPlugin, DomainRegistry
from xcalibur.workspace import Workspace

MANIFEST = DomainManifest.model_validate(
    {
        "name": "demo",
        "display_name": "demo",
        "version": "1.0.0",
        "workflow": {"entrypoint": "main.nf"},
        "inputs": [{"name": "reads", "kind": "fastq"}],
    }
)


def context(tmp_path, validator):
    registry = DomainRegistry()
    registry.register(DomainPlugin(MANIFEST, tmp_path / "pkg", validate_inputs=validator))
    return ToolContext(domains=registry, workspace=Workspace(tmp_path))


def test_without_a_validator_nothing_changes(tmp_path):
    plan = plan_run(RunArgs(domain="demo", inputs={"reads": "r"}), context(tmp_path, None))
    assert "input_check" not in plan


def test_the_validator_gets_absolute_paths_and_its_report_is_in_the_plan(tmp_path):
    seen = {}

    def validator(inputs):
        seen.update(inputs)
        report = InputReport(overview="1 sample")
        report.warn("odd", "something odd", input="reads")
        return report

    plan = plan_run(RunArgs(domain="demo", inputs={"reads": "r"}), context(tmp_path, validator))
    assert seen == {"reads": tmp_path / "r"}
    assert plan["input_check"]["ok"] and plan["input_check"]["overview"] == "1 sample"
    assert plan["input_check"]["warnings"] == [
        {"code": "odd", "message": "something odd", "input": "reads"}
    ]


@pytest.mark.parametrize("step", [plan_run, preview_run])
def test_errors_stop_plan_and_run_with_structured_details(tmp_path, step):
    (tmp_path / "r").mkdir()

    def validator(inputs):
        report = InputReport()
        report.error("bad", "first problem", input="reads", path=inputs["reads"])
        report.error("worse", "second problem", input="reads")
        report.warn("odd", "a warning")
        return report

    with pytest.raises(ToolError) as caught:
        step(RunArgs(domain="demo", inputs={"reads": "r"}), context(tmp_path, validator))
    error = caught.value
    assert error.kind == "invalid_inputs"
    assert str(error) == "2 problems in the inputs: first problem | second problem"
    assert [e["code"] for e in error.details["errors"]] == ["bad", "worse"]
    assert error.details["errors"][0]["path"] == str(tmp_path / "r")
    assert error.details["warnings"] == [{"code": "odd", "message": "a warning"}]


def test_validator_io_errors_become_input_errors(tmp_path):
    def validator(inputs):
        raise PermissionError(13, "Permission denied", str(inputs["reads"]))

    with pytest.raises(ToolError) as caught:
        plan_run(RunArgs(domain="demo", inputs={"reads": "r"}), context(tmp_path, validator))
    assert caught.value.kind == "invalid_inputs"
    assert "Permission denied" in str(caught.value)


def test_no_warnings_key_without_warnings(tmp_path):
    def validator(inputs):
        report = InputReport()
        report.error("bad", "problem")
        return report

    with pytest.raises(ToolError) as caught:
        plan_run(RunArgs(domain="demo", inputs={"reads": "r"}), context(tmp_path, validator))
    assert "warnings" not in caught.value.details


def test_chat_confirmation_shows_what_the_check_detected(tmp_path):
    from xcalibur.chat import ChatApp
    from xcalibur.terminal import Style

    plan = {"domain": "demo", "input_check": {
        "overview": "2 samples (2 paired-end, 0 single-end)",
        "warnings": [{"code": "odd", "message": "S_1 becomes S"}]}}
    app = ChatApp.__new__(ChatApp)
    app.style = Style(False)
    request = ConfirmationRequest(
        tool="run_pipeline", arguments={}, summary="nextflow run x", details=plan
    )
    text = app._plan_text(request)
    assert "detected 2 samples (2 paired-end, 0 single-end)" in text
    assert "warning  S_1 becomes S" in text
