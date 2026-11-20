"""Agent runtime with a scripted LLM (FakeProvider) and a fake engine: no network, no Nextflow."""

import json
from pathlib import Path

import pytest

from xcalibur.agents.base import AgentRequest, AgentSpec
from xcalibur.agents.confirmation import AllowAll, CallbackConfirmation
from xcalibur.agents.core_tools import CORE_TOOLS, build_tool_registry
from xcalibur.agents.runtime import AgentLoop, LLMAgent
from xcalibur.agents.session import Session
from xcalibur.agents.supervisor import LLMRouter, Supervisor
from xcalibur.agents.tools import Tool, ToolContext, ToolError, ToolInput, ToolRegistry
from xcalibur.execution.base import RunResult, RunSpec
from xcalibur.llm.base import Completion, ToolCall
from xcalibur.llm.errors import LLMConnectionError
from xcalibur.llm.fake import FakeProvider, tool_call
from xcalibur.manifest import DomainManifest
from xcalibur.provenance import RunRecord
from xcalibur.registry import DomainPlugin, DomainRegistry
from xcalibur.workspace import Workspace

CORE_NAMES = [
    "list_domains",
    "describe_domain",
    "plan_run",
    "run_pipeline",
    "get_run_status",
    "read_results",
]


def make_plugin(name="demo", keywords=("alpha",), tools=()):
    manifest = DomainManifest.model_validate(
        {
            "name": name,
            "display_name": f"{name} pipeline",
            "version": "1.0.0",
            "description": "Demo workflow.",
            "keywords": list(keywords),
            "workflow": {"entrypoint": "main.nf"},
            "inputs": [{"name": "reads", "kind": "fastq"}],
            "params": [
                {"name": "mode", "type": "enum", "choices": ["AUTO", "FAST"], "default": "AUTO"},
                {"name": "threads", "type": "integer", "default": 2, "minimum": 1, "maximum": 8},
            ],
            "outputs": [{"name": "vcf", "kind": "vcf", "pattern": "results/*.vcf"}],
        }
    )
    return DomainPlugin(manifest=manifest, root=Path("/pkg"), tools=tuple(tools))


class FakeEngine:
    """Writes a provenance record and one output, like a successful run."""

    name = "fake"

    def __init__(self):
        self.runs: list[RunSpec] = []

    def build_command(self, spec):
        cmd = ["fake-engine", "run", spec.plugin.name]
        for key in sorted(spec.params):
            cmd += [f"--{key}", str(spec.params[key])]
        for key in sorted(spec.inputs):
            cmd += [f"--{key}", str(spec.inputs[key])]
        return cmd

    def run(self, spec):
        command = self.build_command(spec)
        if spec.dry_run:
            return RunResult(spec.run_id, "planned", command)
        self.runs.append(spec)
        run_dir = spec.run_dir
        (run_dir / "results").mkdir(parents=True, exist_ok=True)
        (run_dir / "results" / "calls.vcf").write_text("##fileformat=VCFv4.2\n")
        (run_dir / "nextflow.log").write_text("line 1\nline 2\nDone\n")
        record = RunRecord(
            run_id=spec.run_id,
            domain=spec.plugin.name,
            domain_version="1.0.0",
            engine=self.name,
            command=command,
            params=spec.params,
            status="succeeded",
            returncode=0,
        )
        path = record.write(run_dir)
        return RunResult(spec.run_id, "succeeded", command, 0, None, run_dir, path)


@pytest.fixture
def env(tmp_path):
    domains = DomainRegistry()
    domains.register(make_plugin())
    workspace = Workspace(tmp_path / "ws").ensure()
    (workspace.data / "reads").mkdir()
    engine = FakeEngine()
    context = ToolContext(domains=domains, workspace=workspace, engine=engine)
    return domains, workspace, engine, context


def loop_for(provider, context, **options):
    return AgentLoop(provider, build_tool_registry(context.domains), context, **options)


def result_of(session, index):
    """Decoded content of the index-th tool result sent back to the model."""
    results = [r for m in session.messages for r in m.tool_results]
    return json.loads(results[index].content), results[index].is_error


RUN_ARGS = {"domain": "demo", "params": {"threads": 4}, "inputs": {"reads": "data/reads"}}


def test_plan_and_run_end_to_end(env):
    _, workspace, engine, context = env
    provider = FakeProvider.scripted(
        tool_call("list_domains", "c1"),
        tool_call("plan_run", "c2", **RUN_ARGS, run_id="r1"),
        Completion(
            text="I will run demo with threads=4 on data/reads.",
            tool_calls=[ToolCall("c3", "run_pipeline", {**RUN_ARGS, "run_id": "r1"})],
        ),
        tool_call("get_run_status", "c4", run_id="r1"),
        tool_call("read_results", "c5", run_id="r1"),
        "The run finished; the VCF is in results/calls.vcf.",
    )
    session = Session()
    result = loop_for(provider, context, confirmation=AllowAll()).run("Run demo", session)

    assert result.stop_reason == "completed" and result.steps == 6
    assert result.text.startswith("The run finished")
    assert [r.status for r in result.tool_calls] == ["ok"] * 5
    assert result.tool_calls[2].confirmation == "approved"
    assert len(engine.runs) == 1
    assert engine.runs[0].params == {"mode": "AUTO", "threads": 4}
    assert engine.runs[0].inputs["reads"] == workspace.root.absolute() / "data/reads"

    plan, _ = result_of(session, 1)
    assert plan["result"]["command"][:3] == ["fake-engine", "run", "demo"]
    assert "--threads 4" in plan["result"]["command_text"]
    status, _ = result_of(session, 3)
    assert status["result"]["status"] == "succeeded"
    assert status["result"]["log_tail"][-1] == "Done"
    outputs, _ = result_of(session, 4)
    files = outputs["result"]["outputs"][0]["files"]
    assert files[0]["path"].endswith("results/calls.vcf")
    assert outputs["result"]["missing_outputs"] == []

    tools_offered = [t.name for t in provider.calls[0].tools]
    assert tools_offered == CORE_NAMES
    assert provider.calls[0].messages[0].role == "system"
    roles = [m.role for m in session.messages]
    assert roles == ["user", "assistant"] * 6


def test_invalid_arguments_are_returned_and_nothing_runs(env):
    _, _, engine, context = env
    provider = FakeProvider.scripted(
        tool_call("run_pipeline", "c1", domain="demo", params=["oops"], shell="rm -rf /"),
        tool_call("plan_run", "c2", domain="demo", params={"threads": 99}, inputs={"reads": "x"}),
        tool_call("plan_run", "c3", domain="demo", params={"bogus": 1}, inputs={"reads": "x"}),
        tool_call("plan_run", "c4", domain="demo", inputs={"other": "x"}),
        "Sorry, those arguments were invalid.",
    )
    session = Session()
    result = loop_for(provider, context, confirmation=AllowAll()).run("go", session)

    assert result.stop_reason == "completed"
    assert engine.runs == []
    first, is_error = result_of(session, 0)
    assert is_error and first["error"]["type"] == "invalid_arguments"
    locs = {tuple(e["loc"]) for e in first["error"]["errors"]}
    assert ("shell",) in locs and ("params",) in locs
    assert result.tool_calls[0].confirmation is None  # rejected before asking anyone
    assert result_of(session, 1)[0]["error"]["type"] == "invalid_params"
    assert "maximum" in result_of(session, 1)[0]["error"]["message"]
    assert result_of(session, 2)[0]["error"]["type"] == "invalid_params"
    assert result_of(session, 3)[0]["error"]["type"] == "invalid_inputs"
    assert [r.status for r in result.tool_calls] == ["invalid_arguments"] + ["error"] * 3


def test_run_pipeline_without_confirmation_does_not_run(env):
    _, _, engine, context = env
    provider = FakeProvider.scripted(tool_call("run_pipeline", "c1", **RUN_ARGS), "Not run.")
    session = Session()
    result = loop_for(provider, context).run("run it", session)  # default policy: deny

    assert engine.runs == []
    payload, is_error = result_of(session, 0)
    assert is_error and payload["error"]["type"] == "confirmation_declined"
    assert payload["error"]["plan"]["command"][:3] == ["fake-engine", "run", "demo"]
    record = result.tool_calls[0]
    assert record.status == "declined" and record.confirmation == "declined"


def test_confirmation_sees_the_exact_command_and_run_id(env):
    _, _, engine, context = env
    seen = []

    def ask(request):
        seen.append(request)
        return True

    provider = FakeProvider.scripted(tool_call("run_pipeline", "c1", **RUN_ARGS), "Done.")
    loop_for(provider, context, confirmation=CallbackConfirmation(ask)).run("run it")

    assert len(seen) == 1 and len(engine.runs) == 1
    request = seen[0]
    assert request.tool == "run_pipeline"
    assert request.summary.startswith("fake-engine run demo")
    assert "--threads 4" in request.summary
    # The run that executed is the one the user approved.
    assert request.details["run_id"] == engine.runs[0].run_id
    assert request.details["params"] == {"mode": "AUTO", "threads": 4}


def test_existing_run_and_missing_inputs_are_refused(env):
    _, workspace, engine, context = env
    provider = FakeProvider.scripted(
        tool_call("run_pipeline", "c1", **RUN_ARGS, run_id="r1"),
        tool_call("run_pipeline", "c2", **RUN_ARGS, run_id="r1"),
        tool_call("run_pipeline", "c3", domain="demo", inputs={"reads": "nope"}),
        "ok",
    )
    session = Session()
    loop_for(provider, context, confirmation=AllowAll()).run("go", session)
    assert len(engine.runs) == 1
    assert result_of(session, 1)[0]["error"]["type"] == "run_exists"
    assert result_of(session, 2)[0]["error"]["type"] == "invalid_inputs"


def test_loop_stops_at_the_step_limit(env):
    *_, context = env
    provider = FakeProvider.scripted(*[tool_call("list_domains", f"c{i}") for i in range(3)])
    session = Session()
    result = loop_for(provider, context, max_steps=3).run("loop forever", session)

    assert result.stop_reason == "max_steps" and result.steps == 3
    assert "Stopped after 3 steps" in result.text
    assert len(provider.calls) == 3 and len(result.tool_calls) == 3

    # The next user message is merged into the pending tool-result turn (roles alternate).
    provider.extend(["Here are the domains."])
    again = loop_for(provider, context).run("so?", session)
    assert again.stop_reason == "completed"
    assert session.messages[-2].role == "user"
    assert session.messages[-2].tool_results and session.messages[-2].content == "so?"
    assert len(session.tool_calls) == 3


def test_unknown_tool_and_tool_bugs_go_back_to_the_model(env):
    *_, context = env

    class Args(ToolInput):
        pass

    def broken(args, ctx):
        raise RuntimeError("boom")

    tools = build_tool_registry(context.domains)
    tools.register(Tool("broken", "Always fails.", Args, broken))
    provider = FakeProvider.scripted(
        tool_call("rm_rf", "c1"), tool_call("broken", "c2"), "Recovered."
    )
    session = Session()
    result = AgentLoop(provider, tools, context).run("hi", session)

    assert result.stop_reason == "completed" and result.text == "Recovered."
    unknown, _ = result_of(session, 0)
    assert unknown["error"]["type"] == "unknown_tool" and "broken" in unknown["error"]["available"]
    bug, _ = result_of(session, 1)
    assert bug["error"] == {"type": "internal_error", "message": "RuntimeError: boom"}
    assert [r.status for r in result.tool_calls] == ["unknown_tool", "internal_error"]


def test_llm_failure_stops_the_loop_with_a_reason(env):
    *_, context = env
    provider = FakeProvider.scripted(LLMConnectionError("no network"))
    result = loop_for(provider, context).run("hi")
    assert result.stop_reason == "llm_error" and result.error == "no network"
    assert "no network" in result.text


def test_long_results_are_truncated_and_saved(env, tmp_path):
    *_, context = env

    class Args(ToolInput):
        pass

    tools = ToolRegistry([Tool("big", "Large output.", Args, lambda a, c: "x" * 5000)])
    provider = FakeProvider.scripted(tool_call("big", "c/1"), "ok")
    session = Session(directory=tmp_path / "session")
    AgentLoop(provider, tools, context, max_result_chars=1000).run("hi", session)

    content = session.messages[2].tool_results[0].content
    assert len(content) <= 1000
    note = json.loads(content)
    assert note["truncated"] is True and note["total_chars"] > 5000
    saved = Path(note["full_result_path"])
    assert saved.parent == tmp_path / "session" / "tool-results"
    assert json.loads(saved.read_text())["result"] == "x" * 5000

    audit = [json.loads(line) for line in (tmp_path / "session" / "audit.jsonl").open()]
    assert audit[0]["tool"] == "big" and audit[0]["truncated"] is True
    assert audit[0]["result_path"] == str(saved)


def test_status_tools_need_a_workspace_and_a_known_run(env):
    domains, *_ = env
    context = ToolContext(domains=domains)
    provider = FakeProvider.scripted(
        tool_call("get_run_status", "c1", run_id="r1"),
        tool_call("run_pipeline", "c2", **RUN_ARGS),
        tool_call("read_results", "c3", run_id="../etc"),
        "ok",
    )
    session = Session()
    loop_for(provider, context, confirmation=AllowAll()).run("status", session)
    assert result_of(session, 0)[0]["error"]["type"] == "no_workspace"
    assert result_of(session, 1)[0]["error"]["type"] == "no_workspace"
    assert result_of(session, 2)[0]["error"]["type"] == "invalid_arguments"


def test_describe_domain_and_unknown_domain(env):
    *_, context = env
    provider = FakeProvider.scripted(
        tool_call("describe_domain", "c1", domain="demo"),
        tool_call("describe_domain", "c2", domain="nope"),
        "ok",
    )
    session = Session()
    loop_for(provider, context).run("what is demo?", session)
    described, _ = result_of(session, 0)
    assert [p["name"] for p in described["result"]["params"]] == ["mode", "threads"]
    unknown, _ = result_of(session, 1)
    assert unknown["error"]["type"] == "unknown_domain"
    assert unknown["error"]["available"] == ["demo"]


# -- domain tools, agents and tool definitions ----------------------------------------------


class Query(ToolInput):
    sample: str


def test_domains_contribute_prefixed_tools():
    tool = Tool("count_samples", "Count samples.", Query, lambda a, c: {"sample": a.sample})
    domains = DomainRegistry()
    domains.register(make_plugin("demo", tools=[tool]))
    registry = build_tool_registry(domains)
    assert registry.names() == [*CORE_NAMES, "demo__count_samples"]
    assert build_tool_registry(domains, core=False).names() == ["demo__count_samples"]

    provider = FakeProvider.scripted(tool_call("demo__count_samples", "c1", sample="S1"), "ok")
    session = Session()
    AgentLoop(provider, registry, ToolContext(domains=domains)).run("count", session)
    assert result_of(session, 0)[0] == {"result": {"sample": "S1"}}


def test_tool_definition_rules():
    class Loose(ToolInput):
        model_config = {"extra": "allow"}

    with pytest.raises(ValueError, match="forbid"):
        Tool("loose", "x", Loose, lambda a, c: None)
    with pytest.raises(ValueError, match="preview"):
        Tool("act", "x", Query, lambda a, c: None, requires_confirmation=True)
    with pytest.raises(ValueError, match="invalid tool name"):
        Tool("bad name", "x", Query, lambda a, c: None)
    with pytest.raises(ValueError, match="already registered"):
        ToolRegistry([CORE_TOOLS[0], CORE_TOOLS[0]])
    schema = CORE_TOOLS[2].spec().input_schema
    assert schema["additionalProperties"] is False and "domain" in schema["required"]


def test_tool_error_details_reach_the_model(env):
    *_, context = env

    def picky(args, ctx):
        raise ToolError("sample not found", kind="not_found", known=["S1", "S2"])

    tools = ToolRegistry([Tool("picky", "x", Query, picky)])
    provider = FakeProvider.scripted(tool_call("picky", "c1", sample="S9"), "ok")
    session = Session()
    AgentLoop(provider, tools, context).run("hi", session)
    assert result_of(session, 0) == (
        {"error": {"type": "not_found", "message": "sample not found", "known": ["S1", "S2"]}},
        True,
    )


def test_llm_agent_uses_spec_instructions_and_tool_subset(env):
    *_, context = env
    spec = AgentSpec(
        "demo_executor",
        "execution",
        instructions="Only run the demo domain.",
        tools=("plan_run", "run_pipeline"),
    )
    provider = FakeProvider.scripted(tool_call("list_domains", "c1"), "Planned.")
    agent = LLMAgent.from_spec(spec, provider, build_tool_registry(context.domains), context)
    response = agent.handle(AgentRequest(message="plan it"))

    assert response.text == "Planned."
    assert [t.name for t in provider.calls[0].tools] == ["plan_run", "run_pipeline"]
    assert provider.calls[0].messages[0].content.endswith("Only run the demo domain.")
    assert response.data["tool_calls"][0]["status"] == "unknown_tool"  # not offered to it
    assert response.data["stop_reason"] == "completed"


def test_loop_options_are_checked(env):
    *_, context = env
    tools = build_tool_registry(context.domains)
    with pytest.raises(ValueError):
        AgentLoop(FakeProvider(), tools, context, max_steps=0)
    with pytest.raises(ValueError, match="unknown tools"):
        AgentLoop(FakeProvider(), tools, context, allowed_tools=["nope"])


# -- routing --------------------------------------------------------------------------------


def two_domains():
    domains = DomainRegistry()
    domains.register(make_plugin("genomics", ("leishmania", "vcf")))
    domains.register(make_plugin("rnaseq", ("rna-seq", "expression")))
    return domains


def choose(domain, confidence=0.9, reason="fits"):
    return tool_call("choose_domain", "r1", domain=domain, confidence=confidence, reason=reason)


def test_llm_router_picks_a_domain():
    provider = FakeProvider.scripted(choose("rnaseq", 0.8, "expression study"))
    decision = Supervisor(two_domains(), LLMRouter(provider)).route("quantify my samples")
    assert (decision.domain, decision.confidence, decision.reason) == (
        "rnaseq",
        0.8,
        "expression study",
    )
    spec = provider.calls[0].tools[0]
    assert spec.input_schema["properties"]["domain"]["enum"] == ["genomics", "rnaseq", "none"]
    assert "leishmania" in provider.calls[0].messages[0].content


def test_llm_router_can_choose_no_domain():
    provider = FakeProvider.scripted(choose("none", 7, "small talk"))
    decision = LLMRouter(provider).route("hello", two_domains())
    assert decision.domain is None and decision.confidence == 1.0


@pytest.mark.parametrize(
    "reply, why",
    [
        (LLMConnectionError("offline"), "LLM routing failed (offline)"),
        ("I think genomics.", "LLM gave no routing decision"),
        (choose("proteomics"), "LLM chose unknown domain 'proteomics'"),
    ],
)
def test_llm_router_falls_back_to_keywords(reply, why):
    provider = FakeProvider.scripted(reply)
    decision = LLMRouter(provider).route("Leishmania VCF please", two_domains())
    assert decision.domain == "genomics"
    assert decision.reason.startswith(why) and "keyword fallback" in decision.reason


def test_llm_router_without_domains_does_not_call_the_model():
    provider = FakeProvider()
    assert LLMRouter(provider).route("hi", DomainRegistry()).domain is None
    assert provider.calls == []
