"""Agent runtime with a scripted LLM (FakeProvider) and a fake engine: no network, no Nextflow."""

import inspect
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from xcalibur.agents.base import AgentRequest, AgentSpec
from xcalibur.agents.confirmation import AllowAll, CallbackConfirmation
from xcalibur.agents.core_tools import (
    CORE_TOOLS,
    RunArgs,
    RunIdArgs,
    build_tool_registry,
    read_results,
    run_pipeline,
)
from xcalibur.agents.runtime import EMPTY_ANSWER, AgentLoop, LLMAgent
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


def test_truncated_note_fits_even_when_escaping_grows_it(env, tmp_path):
    *_, context = env

    class Args(ToolInput):
        pass

    nasty = '"\\' * 4000  # every character needs escaping in JSON
    tools = ToolRegistry([Tool("nasty", "Escapes.", Args, lambda a, c: nasty)])
    provider = FakeProvider.scripted(tool_call("nasty", "c1"), "ok")
    session = Session(directory=tmp_path / "s")
    AgentLoop(provider, tools, context, max_result_chars=1000).run("hi", session)
    content = session.messages[2].tool_results[0].content
    assert len(content) <= 1000
    note = json.loads(content)
    assert note["truncated"] is True and nasty.startswith(note["preview"])
    assert json.loads(Path(note["full_result_path"]).read_text())["result"] == nasty


def test_saved_results_are_never_overwritten(env, tmp_path):
    *_, context = env

    class Args(ToolInput):
        n: int

    tools = ToolRegistry([Tool("big", "Large.", Args, lambda a, c: str(a.n) * 3000)])
    provider = FakeProvider.scripted(
        tool_call("big", "same", n=1), "first", tool_call("big", "same", n=2), "second"
    )
    session = Session(directory=tmp_path / "s")
    loop = AgentLoop(provider, tools, context, max_result_chars=500)
    loop.run("one", session)
    loop.run("two", session)
    paths = [r.result_path for r in session.tool_calls]
    assert len(set(paths)) == 2
    assert [json.loads(Path(p).read_text())["result"][0] for p in paths] == ["1", "2"]


def test_interruption_leaves_a_valid_history(env, tmp_path):
    *_, context = env

    class Args(ToolInput):
        pass

    def stop(args, ctx):
        raise KeyboardInterrupt

    tools = build_tool_registry(context.domains)
    tools.register(Tool("stop", "Ctrl+C.", Args, stop))
    provider = FakeProvider.scripted(
        Completion(
            text="",
            tool_calls=[
                ToolCall("c1", "list_domains", {}),
                ToolCall("c2", "stop", {}),
                ToolCall("c3", "list_domains", {}),
            ],
        ),
        "Recovered.",
    )
    session = Session(directory=tmp_path / "s")
    loop = AgentLoop(provider, tools, context)
    with pytest.raises(KeyboardInterrupt):
        loop.run("go", session)

    last = session.messages[-1]
    assert last.role == "user" and [r.tool_call_id for r in last.tool_results] == [
        "c1",
        "c2",
        "c3",
    ]
    assert [r.is_error for r in last.tool_results] == [False, True, True]
    assert json.loads(last.tool_results[1].content)["error"]["type"] == "interrupted"
    assert [r.status for r in session.tool_calls] == ["ok", "interrupted", "interrupted"]
    assert len((tmp_path / "s" / "audit.jsonl").read_text().splitlines()) == 3

    result = loop.run("still there?", session)
    assert result.text == "Recovered."
    assert [m.role for m in session.messages] == ["user", "assistant", "user", "assistant"]


def test_unserializable_result_is_an_internal_error(env):
    *_, context = env

    class Args(ToolInput):
        pass

    def loop_back(args, ctx):
        data = []
        data.append(data)
        return data

    tools = ToolRegistry([Tool("circular", "x", Args, loop_back)])
    provider = FakeProvider.scripted(tool_call("circular", "c1"), "ok")
    session = Session()
    AgentLoop(provider, tools, context).run("hi", session)
    payload, is_error = result_of(session, 0)
    assert is_error and payload["error"]["type"] == "internal_error"
    assert session.tool_calls[0].status == "internal_error"


def test_empty_answer_is_never_stored_as_an_empty_turn(env):
    *_, context = env
    provider = FakeProvider.scripted("   ")
    session = Session()
    result = loop_for(provider, context).run("hi", session)
    assert result.text == EMPTY_ANSWER
    assert session.messages[-1].content == EMPTY_ANSWER


def test_run_pipeline_refuses_a_command_that_changed_after_approval(env):
    domains, workspace, engine, _ = env

    class DriftingEngine(FakeEngine):
        calls = 0

        def build_command(self, spec):
            DriftingEngine.calls += 1
            return [*super().build_command(spec), f"--drift{DriftingEngine.calls}"]

    drifting = DriftingEngine()
    context = ToolContext(domains=domains, workspace=workspace, engine=drifting)
    provider = FakeProvider.scripted(tool_call("run_pipeline", "c1", **RUN_ARGS), "ok")
    session = Session()
    loop_for(provider, context, confirmation=AllowAll()).run("run", session)
    payload, _ = result_of(session, 0)
    assert payload["error"]["type"] == "plan_changed"
    assert drifting.runs == [] and session.tool_calls[0].confirmation == "approved"


def test_run_pipeline_called_directly_without_approval_refuses(env):
    *_, engine, context = env
    with pytest.raises(ToolError) as info:
        run_pipeline(RunArgs(**RUN_ARGS), context)
    assert info.value.kind == "not_confirmed" and engine.runs == []


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


def test_read_results_refuses_a_run_still_running(env):
    _, workspace, _, context = env
    record = RunRecord(
        run_id="r1", domain="demo", domain_version="1", engine="nextflow", command=[], params={}
    )
    record.reserve(workspace.run_dir("r1"))
    with pytest.raises(ToolError) as info:
        read_results(RunIdArgs(run_id="r1"), context)
    assert info.value.kind == "run_not_finished"


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


def test_llm_router_missing_confidence_is_neutral():
    reply = tool_call("choose_domain", "r1", domain="genomics", reason="variants")
    decision = LLMRouter(FakeProvider.scripted(reply)).route("x", two_domains())
    assert decision.domain == "genomics" and decision.confidence == 0.5


def test_llm_router_without_domains_does_not_call_the_model():
    provider = FakeProvider()
    assert LLMRouter(provider).route("hi", DomainRegistry()).domain is None
    assert provider.calls == []


# -- edge cases of the audit, truncation and interruption (#43) ---------------------------


def test_failed_audit_write_still_sends_the_real_result(env, tmp_path):
    *_, engine, context = env
    session_dir = tmp_path / "s"
    (session_dir / "audit.jsonl").mkdir(parents=True)  # the audit file cannot be written
    provider = FakeProvider.scripted(
        Completion(
            text="",
            tool_calls=[
                ToolCall("c1", "run_pipeline", RUN_ARGS),
                ToolCall("c2", "list_domains", {}),
            ],
        ),
        "Recovered.",
    )
    session = Session(directory=session_dir)
    loop = loop_for(provider, context, confirmation=AllowAll())
    with pytest.raises(OSError):  # the failure still reaches the caller
        loop.run("run", session)

    assert len(engine.runs) == 1
    ran, ran_error = result_of(session, 0)
    assert not ran_error and ran["result"]["status"] == "succeeded"
    skipped, skipped_error = result_of(session, 1)
    assert skipped_error and skipped["error"]["type"] == "interrupted"
    assert skipped["error"]["started"] is False
    assert [r.status for r in session.tool_calls] == ["ok", "interrupted"]
    assert "returned no result" not in json.dumps(ran)


def test_interrupted_while_running_is_reported_as_possibly_run(env, tmp_path):
    *_, context = env

    class Args(ToolInput):
        pass

    def stop(args, ctx):
        raise KeyboardInterrupt

    tools = build_tool_registry(context.domains)
    tools.register(Tool("stop", "Ctrl+C.", Args, stop))
    provider = FakeProvider.scripted(
        Completion(
            text="",
            tool_calls=[ToolCall("c1", "stop", {}), ToolCall("c2", "list_domains", {})],
        )
    )
    session = Session(directory=tmp_path / "s")
    with pytest.raises(KeyboardInterrupt):
        AgentLoop(provider, tools, context).run("go", session)
    running, _ = result_of(session, 0)
    assert running["error"]["started"] is True and "may have run" in running["error"]["message"]
    not_started, _ = result_of(session, 1)
    assert not_started["error"]["started"] is False
    audit = [json.loads(line) for line in (tmp_path / "s" / "audit.jsonl").open()]
    assert [a["status"] for a in audit] == ["interrupted", "interrupted"]
    assert "may have run" in audit[0]["summary"]


def test_interrupted_during_confirmation_says_nothing_ran(env):
    *_, engine, context = env

    def ctrl_c(request):
        raise KeyboardInterrupt

    provider = FakeProvider.scripted(tool_call("run_pipeline", "c1", **RUN_ARGS))
    session = Session()
    loop = loop_for(provider, context, confirmation=CallbackConfirmation(ctrl_c))
    with pytest.raises(KeyboardInterrupt):
        loop.run("run", session)
    payload, _ = result_of(session, 0)
    assert payload["error"]["started"] is False and engine.runs == []


def test_repeated_call_ids_in_a_step_are_all_audited(env, tmp_path):
    *_, context = env

    class Args(ToolInput):
        pass

    def stop(args, ctx):
        raise KeyboardInterrupt

    tools = build_tool_registry(context.domains)
    tools.register(Tool("stop", "Ctrl+C.", Args, stop))
    provider = FakeProvider.scripted(
        Completion(
            text="",
            tool_calls=[ToolCall("dup", "list_domains", {}), ToolCall("dup", "stop", {})],
        )
    )
    session = Session(directory=tmp_path / "s")
    with pytest.raises(KeyboardInterrupt):
        AgentLoop(provider, tools, context).run("go", session)
    audit = [json.loads(line) for line in (tmp_path / "s" / "audit.jsonl").open()]
    assert [(a["call_id"], a["status"]) for a in audit] == [
        ("dup", "ok"),
        ("dup", "interrupted"),
    ]


@pytest.mark.parametrize("call_id", ["c1", "x" * 64])
def test_truncation_note_fits_the_minimum_limit_with_a_long_path(env, tmp_path, call_id):
    *_, context = env

    class Args(ToolInput):
        pass

    tools = ToolRegistry([Tool("big", "Large output.", Args, lambda a, c: "y" * 5000)])
    provider = FakeProvider.scripted(tool_call("big", call_id), "ok")
    session_dir = tmp_path / ("long-directory-name-" * 8) / "session"
    session = Session(directory=session_dir)
    AgentLoop(provider, tools, context, max_result_chars=200).run("hi", session)

    content = session.messages[2].tool_results[0].content
    assert len(content) <= 200
    note = json.loads(content)
    assert note["truncated"] is True
    if call_id == "c1":  # the short name fits relative to the session directory
        assert note["relative_to"] == "session directory"
        assert (session_dir / note["full_result_path"]).is_file()
    else:  # not even the relative name fits: left out
        assert note["full_result_path"] is None and "relative_to" not in note
    saved = Path(session.tool_calls[0].result_path)  # the audit keeps the full path
    assert saved.is_absolute() and json.loads(saved.read_text())["result"] == "y" * 5000


def test_result_that_cannot_be_saved_is_still_sent(env, tmp_path):
    *_, context = env

    class Args(ToolInput):
        pass

    session_dir = tmp_path / "s"
    session_dir.mkdir()
    (session_dir / "tool-results").write_text("not a folder")
    tools = ToolRegistry([Tool("big", "Large output.", Args, lambda a, c: "z" * 5000)])
    provider = FakeProvider.scripted(tool_call("big", "c1"), "ok")
    session = Session(directory=session_dir)
    result = AgentLoop(provider, tools, context, max_result_chars=1000).run("hi", session)
    assert result.text == "ok"
    content = session.messages[2].tool_results[0].content
    note = json.loads(content)
    assert len(content) <= 1000 and note["full_result_path"] is None
    assert note["save_error"] and note["preview"].startswith('{"result": "zzz')
    assert session.tool_calls[0].status == "ok" and session.tool_calls[0].result_path is None


class _BadStr:
    def __str__(self):
        raise TypeError("no text form")


def _deep():
    data: list = []
    for _ in range(100_000):
        data = [data]
    return data


@pytest.mark.parametrize("value", [_deep, _BadStr], ids=["too-deep", "bad-type"])
def test_results_that_break_serialization_are_internal_errors(env, value):
    *_, context = env

    class Args(ToolInput):
        pass

    tools = ToolRegistry([Tool("odd", "x", Args, lambda a, c: value())])
    provider = FakeProvider.scripted(tool_call("odd", "c1"), "Carried on.")
    session = Session()
    result = AgentLoop(provider, tools, context).run("hi", session)
    assert result.text == "Carried on." and result.stop_reason == "completed"
    payload, is_error = result_of(session, 0)
    assert is_error and payload["error"]["type"] == "internal_error"
    assert session.tool_calls[0].status == "internal_error"


def test_confirmation_callback_cannot_change_the_compared_plan(env):
    *_, engine, context = env

    def meddle(request):
        request.details["command"].append("--evil")
        request.details["command_text"] = "something else"
        return True

    provider = FakeProvider.scripted(tool_call("run_pipeline", "c1", **RUN_ARGS), "ok")
    session = Session()
    loop_for(provider, context, confirmation=CallbackConfirmation(meddle)).run("run", session)
    payload, is_error = result_of(session, 0)
    assert not is_error and payload["result"]["status"] == "succeeded"
    assert "--evil" not in engine.build_command(engine.runs[0])


def test_save_result_never_overwrites_a_file_created_concurrently(tmp_path, monkeypatch):
    session = Session(directory=tmp_path / "s")
    folder = tmp_path / "s" / "tool-results"
    competitor = folder / "0001-step001-c1.json"
    real_open = Path.open

    def racing_open(self, mode="r", *args, **kwargs):
        # Another session creates the same name between choosing it and writing it.
        if mode == "x" and self == competitor and not competitor.exists():
            competitor.write_text("other session")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    path = session.save_result(1, "c1", "mine")
    assert competitor.read_text() == "other session"
    assert path.name == "0002-step001-c1.json" and path.read_text() == "mine"


def test_interruption_right_after_a_call_keeps_its_result_and_record(env, tmp_path):
    *_, context = env

    class InterruptedSession(Session):
        """Ctrl+C right after the call returned, before it is recorded."""

        def remember(self, entry):
            if not self.tool_calls:
                self.__class__ = Session  # interrupt only once
                raise KeyboardInterrupt
            super().remember(entry)

    provider = FakeProvider.scripted(
        Completion(
            text="",
            tool_calls=[ToolCall("c1", "list_domains", {}), ToolCall("c2", "list_domains", {})],
        )
    )
    session = InterruptedSession(directory=tmp_path / "s")
    with pytest.raises(KeyboardInterrupt):
        loop_for(provider, context).run("go", session)
    first, is_error = result_of(session, 0)
    assert not is_error and "result" in first
    second, _ = result_of(session, 1)
    assert second["error"]["started"] is False
    assert [(r.call_id, r.status) for r in session.tool_calls] == [
        ("c1", "ok"),
        ("c2", "interrupted"),
    ]
    audit = [json.loads(line) for line in (tmp_path / "s" / "audit.jsonl").open()]
    assert [(a["call_id"], a["status"]) for a in audit] == [("c1", "ok"), ("c2", "interrupted")]


def test_interrupted_approved_run_keeps_the_confirmation(env, tmp_path):
    domains, workspace, _, _ = env

    class CtrlCEngine(FakeEngine):
        def run(self, spec):
            raise KeyboardInterrupt

    context = ToolContext(domains=domains, workspace=workspace, engine=CtrlCEngine())
    provider = FakeProvider.scripted(tool_call("run_pipeline", "c1", **RUN_ARGS))
    session = Session(directory=tmp_path / "s")
    with pytest.raises(KeyboardInterrupt):
        loop_for(provider, context, confirmation=AllowAll()).run("run", session)
    payload, _ = result_of(session, 0)
    assert payload["error"]["started"] is True
    record = session.tool_calls[0]
    assert record.status == "interrupted" and record.confirmation == "approved"
    audit = json.loads((tmp_path / "s" / "audit.jsonl").read_text())
    assert audit["confirmation"] == "approved"


@contextmanager
def ctrl_c_before(function, marker, occurrence=0):
    """Raise KeyboardInterrupt once, just before the line of ``function`` holding the
    ``occurrence``-th ``marker`` runs (where CPython could deliver a real SIGINT)."""
    lines, first = inspect.getsourcelines(function)
    target = first + [i for i, line in enumerate(lines) if marker in line][occurrence]
    code = function.__code__
    fired = False

    def local(frame, event, arg):
        nonlocal fired
        if event == "line" and frame.f_lineno == target and not fired:
            fired = True
            raise KeyboardInterrupt
        return local

    def tracer(frame, event, arg):
        return local if frame.f_code is code else None

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        yield
    finally:
        sys.settrace(previous)
    assert fired, f"never reached {marker!r}"


ONCE = [("c1", "ok"), ("c2", "interrupted")]


@pytest.mark.parametrize(
    ("marker", "occurrence", "expected_audit"),
    [
        ("outcomes.append(current)", 0, ONCE),
        ("session.remember(current[1])", 0, ONCE),
        ("session.write_audit(current[1])", 0, ONCE),
        # The line was written but not yet marked: written again (twice, never zero).
        ("written.add(id(current[1]))", 1, [("c1", "ok"), ("c1", "ok"), ("c2", "interrupted")]),
    ],
    ids=["before-append", "before-remember", "before-write", "after-write"],
)
def test_ctrl_c_anywhere_after_a_call_returned_keeps_result_and_audit(
    env, tmp_path, marker, occurrence, expected_audit
):
    *_, context = env
    provider = FakeProvider.scripted(
        Completion(
            text="",
            tool_calls=[ToolCall("c1", "list_domains", {}), ToolCall("c2", "list_domains", {})],
        )
    )
    session = Session(directory=tmp_path / "s")
    loop = loop_for(provider, context)
    with (
        pytest.raises(KeyboardInterrupt),
        ctrl_c_before(AgentLoop._run_calls, marker, occurrence),
    ):
        loop.run("go", session)

    first, is_error = result_of(session, 0)
    assert not is_error and "result" in first
    second, _ = result_of(session, 1)
    assert second["error"]["type"] == "interrupted" and second["error"]["started"] is False
    assert [(r.call_id, r.status) for r in session.tool_calls] == [
        ("c1", "ok"),
        ("c2", "interrupted"),
    ]
    audit = [json.loads(line) for line in (tmp_path / "s" / "audit.jsonl").open()]
    assert [(a["call_id"], a["status"]) for a in audit] == expected_audit
