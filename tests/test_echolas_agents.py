"""ECHOLAS agents (issue #9) and the grounding check, with a scripted model (no network)."""

import gzip

import pytest

from test_echolas_results import make_run
from xcalibur.agents.base import AgentRequest
from xcalibur.agents.confirmation import AllowAll
from xcalibur.agents.core_tools import build_tool_registry
from xcalibur.agents.grounding import numbers_in_text, ungrounded_numbers
from xcalibur.agents.runtime import AgentLoop, LLMAgent
from xcalibur.agents.tools import Tool, ToolContext, ToolInput, ToolRegistry
from xcalibur.chat import chat_system_prompt
from xcalibur.llm.fake import FakeProvider, tool_call
from xcalibur.registry import DomainRegistry
from xcalibur.workspace import Workspace
from xcalibur_echolas import EXECUTOR_TOOLS, INTERPRETER_TOOLS, plugin, prompt
from xcalibur_echolas.tools import RunArgs, run_summary


@pytest.fixture
def registry():
    domains = DomainRegistry()
    domains.register(plugin())
    return domains


def agent(registry, ctx, role, *replies, **options):
    spec = next(a for a in registry.get("echolas").agents if a.role == role)
    provider = FakeProvider.scripted(*replies)
    ctx = ToolContext(domains=registry, workspace=ctx.workspace, engine=ctx.engine)
    return LLMAgent.from_spec(spec, provider, build_tool_registry(registry), ctx,
                              **options), provider


# -- grounding ----------------------------------------------------------------------------

def test_numbers_in_text_skip_identifiers_and_list_markers():
    text = "1. PC1 explains 78,2% of SA1 and R1; run_3 has 27 variants\n2) SH-aLRT 100/100"
    assert numbers_in_text(text) == ["78,2", "27", "100"]


@pytest.mark.parametrize(
    "text, missing",
    [
        ("PC1: 78.18% (78,2% rounded, 78%)", []),
        ("27 variants, 25 SNPs and 2 indels", []),
        ("PC1: 81%", ["81"]),
        ("1.000 samples", ["1.000"]),
        ("PC2 coordinate -0.41", []),
    ],
)
def test_ungrounded_numbers(text, missing):
    source = {"percent": [78.18], "variants": 27, "snp": 25, "indel": 2, "pc2": -0.4082}
    assert ungrounded_numbers(text, [source]) == missing


class Args(ToolInput):
    pass


def facts(args, ctx):
    return {"samples": 6, "pc1_percent": 78.18}


def loop_with(provider, grounded=True):
    tools = ToolRegistry([Tool("facts", "Facts.", Args, facts, grounded=grounded)])
    return AgentLoop(provider, tools, ToolContext(domains=DomainRegistry()))


def test_grounded_answer_passes_unchanged():
    provider = FakeProvider.scripted(tool_call("facts"), "6 samples; PC1 has 78.2%.")
    result = loop_with(provider).run("summarise")
    assert result.text == "6 samples; PC1 has 78.2%." and result.ungrounded_numbers == []
    assert len(provider.calls) == 2


def test_invented_number_gets_one_correction():
    provider = FakeProvider.scripted(tool_call("facts"), "6 samples; PC1 has 91%.",
                                     "6 samples; PC1 has 78.18%.")
    result = loop_with(provider).run("summarise")
    assert result.text == "6 samples; PC1 has 78.18%." and result.ungrounded_numbers == []
    retry = provider.calls[2].messages[-1].content
    assert "[grounding check]" in retry and "91" in retry


def test_persisting_invented_number_is_flagged():
    provider = FakeProvider.scripted(tool_call("facts"), "PC1 has 91%.", "PC1 has 91%.")
    result = loop_with(provider).run("summarise")
    assert result.ungrounded_numbers == ["91"]
    assert result.text.startswith("PC1 has 91%.") and "could not be traced" in result.text


def test_numbers_from_the_user_message_are_allowed():
    provider = FakeProvider.scripted(tool_call("facts"), "Run 42 has 6 samples.")
    assert loop_with(provider).run("explain run 42").ungrounded_numbers == []


def test_no_check_without_a_grounded_tool():
    provider = FakeProvider.scripted(tool_call("facts"), "PC1 has 91%.")
    result = loop_with(provider, grounded=False).run("summarise")
    assert result.text == "PC1 has 91%." and len(provider.calls) == 2


def test_failed_tool_results_do_not_ground():
    def broken(args, ctx):
        raise RuntimeError("boom 91")

    tools = ToolRegistry([Tool("facts", "Facts.", Args, broken, grounded=True)])
    provider = FakeProvider.scripted(tool_call("facts"), "PC1 has 91%.")
    result = AgentLoop(provider, tools, ToolContext(domains=DomainRegistry())).run("x")
    assert result.ungrounded_numbers == []  # no grounded result: nothing to check against


# -- the ECHOLAS agents -------------------------------------------------------------------

def test_agents_have_versioned_prompts_and_existing_tools(registry):
    executor, interpreter = registry.get("echolas").agents
    assert executor.instructions == prompt("executor") and "version 1" in executor.instructions
    assert interpreter.instructions == prompt("interpreter")
    names = build_tool_registry(registry).names()
    assert set(EXECUTOR_TOOLS) <= set(names) and set(INTERPRETER_TOOLS) <= set(names)
    assert "run_pipeline" not in INTERPRETER_TOOLS  # the interpreter cannot run anything
    assert not any(t.startswith("echolas__") for t in EXECUTOR_TOOLS)


def test_interpreter_summary_numbers_match_the_tools(tmp_path, registry):
    ctx = make_run(tmp_path)
    facts_ = run_summary(RunArgs(run_id="r1"), ctx)
    split = facts_["tree"]["splits"][0]
    answer = (
        f"Run r1 has {facts_['vcf']['sample_count']} samples and "
        f"{facts_['vcf']['variant_count']} variants after filtering "
        f"({facts_['vcf']['by_type']['snp']} SNPs, {facts_['vcf']['by_type']['indel']} "
        f"indels). PC1 takes {facts_['pca']['percent_of_computed'][0]}% of the computed PCs "
        f"and separates SA1-SA3 from SB1-SB3. The only supported split groups "
        f"{', '.join(split['taxa'])} (SH-aLRT {split['sh_alrt']:g}, UFBoot "
        f"{split['ufboot']:g}); {facts_['tree']['unsupported_splits']} splits are not "
        "supported. With so few samples this describes these strains only."
    )
    bot, provider = agent(registry, ctx, "interpretation",
                          tool_call("echolas__summarize_run", run_id="r1"), answer)
    response = bot.handle(AgentRequest("Explain run r1"))
    assert response.text == answer
    assert response.data["tool_calls"][0]["status"] == "ok"
    # Every number of the answer is one the tools returned.
    assert ungrounded_numbers(answer, [facts_, "Explain run r1"]) == []
    offered = {spec.name for spec in provider.calls[0].tools}
    assert offered == set(INTERPRETER_TOOLS)
    assert "version 2" in provider.calls[0].messages[0].content


def test_interpreter_cannot_pass_an_invented_number(tmp_path, registry):
    ctx = make_run(tmp_path)
    bot, _ = agent(registry, ctx, "interpretation",
                   tool_call("echolas__summarize_run", run_id="r1"),
                   "PC1 explains 64% of the variance.", "PC1 explains 64% of the variance.")
    response = bot.handle(AgentRequest("Explain run r1"))
    assert "could not be traced" in response.text and "64" in response.text


class NoRunEngine:
    name = "nextflow"

    def __init__(self):
        self.runs = 0

    def build_command(self, spec):
        return ["nextflow", "run", str(spec.plugin.workflow_path)]

    def run(self, spec):  # pragma: no cover - must never be reached
        self.runs += 1
        raise AssertionError("the executor ran with invalid inputs")


def test_executor_refuses_invalid_inputs_and_says_why(tmp_path, registry):
    raw = tmp_path / "raw_data"
    raw.mkdir()
    with gzip.open(raw / "A_R1.fastq.gz", "wt") as handle:
        handle.write("@r\nACGT\n+\nIIII\n")
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference" / "ref.fa").write_text(">c\nACGT\n")
    engine = NoRunEngine()
    ctx = ToolContext(domains=registry, workspace=Workspace(tmp_path), engine=engine)
    inputs = {"raw_data": str(raw), "reference": str(tmp_path / "reference")}
    bot, provider = agent(
        registry, ctx, "execution",
        tool_call("run_pipeline", domain="echolas", inputs=inputs),
        "I cannot run it: A_R1.fastq.gz has no mate. Add A_R2.fastq.gz.",
        confirmation=AllowAll(),
    )
    response = bot.handle(AgentRequest("run echolas"))
    call = response.data["tool_calls"][0]
    assert call["status"] == "error" and engine.runs == 0
    assert "has no mate" in provider.calls[1].messages[-1].tool_results[0].content
    assert "invalid_inputs" in provider.calls[1].messages[-1].tool_results[0].content
    assert not (tmp_path / "runs").exists()


def test_chat_prompt_carries_the_agents_instructions(registry):
    text = chat_system_prompt(registry)
    assert "echolas_interpreter (interpretation)" in text
    assert prompt("interpreter") in text and prompt("executor") in text
    assert chat_system_prompt(DomainRegistry()).startswith("You are XcaliburAI")


# -- grounding rules of #68 ---------------------------------------------------------------

def synthetic_summary(tmp_path):
    return {"result": run_summary(RunArgs(run_id="r1"), make_run(tmp_path))}


def test_a_percentage_needs_a_percentage_field(tmp_path):
    facts_ = synthetic_summary(tmp_path)
    # 95 appears as a number (the UFBoot threshold) but not as a percentage.
    assert ungrounded_numbers("PC1 explica 95% da variância.", [facts_]) == ["95"]
    assert ungrounded_numbers("PC1 explains 95 percent.", [facts_]) == ["95"]
    pc1 = facts_["result"]["pca"]["percent_of_computed"][0]
    assert ungrounded_numbers(f"PC1 explica {pc1}% (cerca de {round(pc1)} por cento).",
                              [facts_]) == []
    assert ungrounded_numbers("Splits need UFBoot >= 95 and SH-aLRT >= 80.", [facts_]) == []


def test_numbers_inside_result_strings_do_not_ground():
    data = {"note": "computed with --pca 10 on 2024 data", "count": 3}
    assert ungrounded_numbers("There are 10 PCs from 2024.", [data]) == ["10", "2024"]
    assert ungrounded_numbers("There are 3 samples.", [data]) == []


@pytest.mark.parametrize(
    "text, samples, missing",
    [
        ("Foram analisadas seis amostras.", 6, []),
        ("Foram analisadas seis amostras.", 5, ["seis"]),
        ("Six samples were analysed.", 5, ["Six"]),
        ("Há vinte e sete variantes.", 27, []),
        ("There are twenty-seven variants.", 27, []),
        ("There are twenty seven variants.", 28, ["twenty seven"]),
        ("Encontramos três splits e um grupo com suporte.", 3, []),  # "um" is an article
        ("Cem por cento das amostras.", 6, ["Cem"]),
    ],
)
def test_numbers_written_as_words(text, samples, missing):
    data = {"samples": samples, "splits": 3}
    assert ungrounded_numbers(text, [data]) == missing


def test_issue_references_and_list_markers_are_not_data():
    assert numbers_in_text("1. See #52 for PC1 and SA2.\n2) Done") == []


def test_the_user_message_grounds_any_kind_of_number():
    assert ungrounded_numbers("You asked about 30% and seven runs.",
                              ["is 30% enough for 7 runs?", {"x": 1}]) == []


def test_percent_keys_are_found_at_any_depth():
    data = {"samples": [{"missing_percent": 12.5}], "pca": {"pct": [40.0]}}
    assert ungrounded_numbers("12.5% missing; PC1 40%.", [data]) == []
    assert ungrounded_numbers("12.5 samples", [data]) == []  # a plain number may match too


def test_tools_return_their_thresholds_as_numbers(tmp_path):
    summary = synthetic_summary(tmp_path)["result"]
    assert summary["tree"]["support_thresholds"] == {"sh_alrt": 80.0, "ufboot": 95.0}
    assert summary["caveat_thresholds"] == {"few_samples": 10, "high_missing_percent": 20.0}
