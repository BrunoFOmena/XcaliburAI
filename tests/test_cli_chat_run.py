"""`xcalibur run` and `xcalibur chat` with a simulated Nextflow and a scripted LLM."""

import json
import stat
import threading
import time
from pathlib import Path

import pytest

from xcalibur import cli
from xcalibur.agents.confirmation import ConfirmationRequest
from xcalibur.chat import ChatApp
from xcalibur.llm.fake import FakeProvider, tool_call
from xcalibur.manifest import DomainManifest
from xcalibur.registry import DomainPlugin, DomainRegistry
from xcalibur.terminal import (
    LogFollower,
    Style,
    color_enabled,
    printable,
    stderr_printer,
    strip_ansi,
)

FAKE_NEXTFLOW = """#!/bin/sh
if [ "$1" = "-version" ]; then
  printf "      N E X T F L O W\\n      version 24.04.0 build 5917\\n"; exit 0
fi
echo "N E X T F L O W  ~  fake run"
echo "[ab/12cd34] process > QC (1) [100%] 1 of 1"
case "$*" in *"--mode FAIL"*) echo "boom" >&2; exit 3;; esac
mkdir -p results && echo "##fileformat=VCFv4.2" > results/calls.vcf
echo "Done"
exit 0
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A workspace, a reads directory, a fake nextflow binary and a one-domain registry."""
    nextflow = tmp_path / "bin" / "nextflow"
    nextflow.parent.mkdir()
    nextflow.write_text(FAKE_NEXTFLOW)
    nextflow.chmod(nextflow.stat().st_mode | stat.S_IEXEC)
    reads = tmp_path / "reads"
    reads.mkdir()
    manifest = DomainManifest.model_validate(
        {
            "name": "demo",
            "display_name": "demo pipeline",
            "version": "1.0.0",
            "workflow": {"entrypoint": "main.nf"},
            "inputs": [{"name": "reads", "kind": "fastq"}],
            "params": [
                {"name": "mode", "type": "enum", "choices": ["AUTO", "FAIL"], "default": "AUTO"},
                {"name": "threads", "type": "integer", "default": 2, "minimum": 1, "maximum": 8},
            ],
            "outputs": [{"name": "vcf", "kind": "vcf", "pattern": "results/*.vcf"}],
        }
    )
    registry = DomainRegistry()
    registry.register(DomainPlugin(manifest=manifest, root=tmp_path / "pkg"))
    monkeypatch.setattr(cli, "_discover", lambda: registry)
    monkeypatch.delenv("XCALIBUR_DEBUG", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    return {"ws": ws, "reads": reads, "nextflow": str(nextflow)}


def run(env, *extra):
    return cli.main(
        ["run", "demo", "-i", f"reads={env['reads']}", "--workspace", str(env["ws"]),
         "--nextflow", env["nextflow"], *extra]
    )


# -- general ------------------------------------------------------------------------------


def test_no_arguments_shows_help(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "usage: xcalibur" in out and "chat" in out and "run" in out and "Exit codes" in out


def test_unexpected_errors_never_show_a_traceback(monkeypatch, capsys):
    def broken():
        raise RuntimeError("entry point exploded")

    monkeypatch.delenv("XCALIBUR_DEBUG", raising=False)
    monkeypatch.setattr(cli, "_discover", broken)
    assert cli.main(["domains"]) == 1
    err = capsys.readouterr().err
    assert "entry point exploded" in err and "Traceback" not in err


# -- run ----------------------------------------------------------------------------------


def test_run_json_reports_run_id_status_and_paths(env, capsys):
    assert run(env, "--run-id", "r1", "--json") == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["run_id"] == "r1" and data["status"] == "succeeded" and data["returncode"] == 0
    run_dir = env["ws"] / "runs" / "r1"
    assert data["run_dir"] == str(run_dir)
    assert Path(data["log_path"]).is_file() and Path(data["record_path"]).is_file()
    assert data["outputs"][0]["files"][0]["path"] == str(run_dir / "results" / "calls.vcf")
    assert data["missing_outputs"] == []
    record = json.loads(Path(data["record_path"]).read_text())
    assert record["status"] == "succeeded" and record["params"] == {"mode": "AUTO", "threads": 2}
    # progress streamed from the run log, on stderr
    assert "process > QC" in captured.err and "Done" in captured.err


def test_run_human_output_without_colour_when_not_a_terminal(env, capsys):
    assert run(env, "--run-id", "r2", "-p", "threads=4") == 0
    out = capsys.readouterr().out
    assert "✓ run r2 succeeded (exit 0)" in out and "calls.vcf" in out
    assert "\033[" not in out


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["-p", "colour=red"], "unknown parameters"),
        (["-p", "mode=SLOW"], "not in"),
        (["-p", "threads=99"], "maximum"),
        (["-p", "threads"], "expected KEY=VALUE"),
        (["--run-id", "../escape"], "run_id"),
        (["--run-id=-x"], "run_id"),  # "=" so argparse hands "-x" to the validation
        (["--run-id", "_x"], "run_id"),
        (["--resume"], "--resume needs"),
    ],
)
def test_run_invalid_parameters_exit_2_with_a_clear_message(env, capsys, extra, message):
    assert run(env, *extra) == 2
    err = capsys.readouterr().err
    assert message in err and "Traceback" not in err
    assert not (env["ws"] / "runs").exists()


def test_run_missing_input_and_unknown_domain_exit_2(env, capsys):
    ws = ["--workspace", str(env["ws"]), "--nextflow", env["nextflow"]]
    assert cli.main(["run", "demo", *ws]) == 2
    assert "missing required inputs" in capsys.readouterr().err
    assert cli.main(["run", "demo", "-i", "reads=/no/such/dir", *ws]) == 2
    assert "do not exist" in capsys.readouterr().err
    assert cli.main(["run", "nope", *ws, "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "error" and data["error"]["type"] == "unknown_domain"


def test_run_existing_id_needs_resume(env, capsys):
    assert run(env, "--run-id", "again") == 0
    assert run(env, "--run-id", "again") == 2
    assert "already exists" in capsys.readouterr().err
    assert run(env, "--run-id", "again", "--resume") == 0
    assert (env["ws"] / "runs" / "again" / "run-again.attempt-2.json").is_file()


def test_run_failure_exits_1_and_keeps_the_record(env, capsys):
    assert run(env, "--run-id", "bad", "-p", "mode=FAIL", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "failed" and data["returncode"] == 3
    assert data["missing_outputs"] == ["vcf"]
    assert "boom" in Path(data["log_path"]).read_text()


def test_run_without_nextflow_exits_1_before_printing_or_creating_anything(env, capsys):
    args = ["run", "demo", "-i", f"reads={env['reads']}", "--workspace", str(env["ws"]),
            "--nextflow", "definitely-not-installed"]
    assert cli.main(args) == 1
    err = capsys.readouterr().err
    assert "was not found" in err and "Running" not in err
    assert list(env["ws"].iterdir()) == []  # no runs/, data/ or reports/
    assert cli.main([*args, "--json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["type"] == "engine_not_found"
    assert "Running" not in captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "demo", "-p", "bad"],
        ["run", "demo", "-i", "noequals"],
        ["run", "demo", "--resume"],
        ["run", "demo", "--bogus"],
        ["plan", "demo", "-p", "=x"],
        ["chat", "--max-steps", "0"],
        ["chat", "--max-steps", "many"],
    ],
)
def test_usage_errors_are_json_with_json(env, capsys, argv):
    assert cli.main([*argv, "--json"]) == 2
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "error" and data["error"]["type"] == "usage_error"
    assert data["error"]["message"] in captured.err
    # Without --json, stdout stays empty and the error is one line on stderr.
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("error: ")


def test_usage_error_after_double_dash_is_not_json(capsys):
    assert cli.main(["run", "--bogus", "--", "--json"]) == 2
    assert capsys.readouterr().out == ""


def test_plan_without_run_id_names_the_generated_id(env, capsys):
    args = ["plan", "demo", "-i", f"reads={env['reads']}", "--nextflow", env["nextflow"]]
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    command = captured.out.strip()
    assert "\n" not in command  # stdout is the command alone
    run_id = captured.err.split("run id: ")[1].split()[0]
    assert f"runs/{run_id}/" in command and f"--run-id {run_id}" in captured.err
    assert cli.main([*args, "--run-id", run_id]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == command and "run id:" not in captured.err


def test_input_paths_are_normalised_in_the_command_and_the_record(env, capsys):
    relative = "../reads/../reads"  # from the workspace: <tmp>/ws/../reads/../reads
    assert cli.main(["run", "demo", "-i", f"reads={relative}", "--workspace", str(env["ws"]),
                     "--nextflow", env["nextflow"], "--run-id", "n1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    expected = str(env["reads"])
    assert expected in data["command"] and ".." not in " ".join(data["command"])
    record = json.loads((env["ws"] / "runs" / "n1" / "run-n1.json").read_text())
    assert expected in record["command"]
    assert all(".." not in str(v) for v in record["inputs"].values())


def test_dry_run_matches_plan_and_touches_nothing(env, capsys):
    assert run(env, "--run-id", "p1", "--dry-run") == 0
    dry = capsys.readouterr().out
    args = ["plan", "demo", "-i", f"reads={env['reads']}", "--workspace", str(env["ws"]),
            "--nextflow", env["nextflow"], "--run-id", "p1"]
    assert cli.main(args) == 0
    assert capsys.readouterr().out == dry
    assert dry.startswith(env["nextflow"] + " run ")
    assert not (env["ws"] / "runs").exists()
    assert cli.main([*args, "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "planned" and plan["run_id"] == "p1"


# -- chat ---------------------------------------------------------------------------------


@pytest.fixture
def chat(env, monkeypatch):
    """Returns a function that runs `xcalibur chat` with scripted replies and user input."""
    (env["ws"] / "xcalibur.yaml").write_text("llm:\n  provider: anthropic\n  model: test-model\n")

    def start(replies, lines, *extra):
        provider = FakeProvider.scripted(*replies)
        monkeypatch.setattr(cli, "create_provider", lambda config: provider)
        answers = iter(lines)
        prompts: list[str] = []

        def fake_input(prompt=""):
            prompts.append(prompt)
            try:
                return next(answers)
            except StopIteration:
                raise EOFError from None

        monkeypatch.setattr("builtins.input", fake_input)
        code = cli.main(["chat", "--workspace", str(env["ws"]), "--nextflow", env["nextflow"],
                         *extra])
        return code, provider, prompts

    return start


def run_args(env, run_id="c1"):
    return {"domain": "demo", "inputs": {"reads": str(env["reads"])}, "run_id": run_id}


def test_chat_plans_confirms_runs_and_interprets(env, chat, capsys):
    replies = [
        tool_call("plan_run", **run_args(env)),
        "I will run demo with mode AUTO on your reads. Shall I start?",
        tool_call("run_pipeline", **run_args(env)),
        tool_call("read_results", run_id="c1"),
        "The run finished: calls.vcf holds the variant calls.",
    ]
    lines = ["Call variants on my reads", "yes, go", "y", "/runs", "/status", "/exit"]
    code, provider, prompts = chat(replies, lines)
    assert code == 0
    out = capsys.readouterr().out
    assert "I will run demo" in out
    assert "The assistant wants to run demo" in out and "command" in out
    assert env["nextflow"] in out  # the exact command was shown before running
    assert any("Run it?" in p for p in prompts)
    assert "calls.vcf holds the variant calls" in out
    assert "c1" in out and "succeeded" in out  # /runs
    assert "tool calls 3" in out  # /status
    record = json.loads((env["ws"] / "runs" / "c1" / "run-c1.json").read_text())
    assert record["status"] == "succeeded"
    audits = list((env["ws"] / "sessions").glob("*/audit.jsonl"))
    assert len(audits) == 1
    entries = [json.loads(line) for line in audits[0].read_text().splitlines()]
    assert [e["tool"] for e in entries] == ["plan_run", "run_pipeline", "read_results"]
    assert entries[1]["confirmation"] == "approved"
    assert not provider.replies


def test_chat_confirmation_shows_the_profile(env, chat, capsys):
    replies = [tool_call("run_pipeline", **run_args(env), profile="test"), "Done."]
    code, _, prompts = chat(replies, ["run it", "y"])
    assert code == 0 and any("Run it?" in p for p in prompts)
    out = capsys.readouterr().out
    assert "profile  test" in out
    record = json.loads((env["ws"] / "runs" / "c1" / "run-c1.json").read_text())
    assert record["command"][record["command"].index("-profile") + 1] == "test"


def test_chat_shows_the_run_status_after_run_pipeline(env, chat, capsys):
    replies = [tool_call("run_pipeline", **run_args(env), params={"mode": "FAIL"}),
               "The run failed."]
    code, _, _ = chat(replies, ["run it", "y"])
    assert code == 0
    out = capsys.readouterr().out
    assert "✗ run_pipeline: run c1 failed" in out
    assert "run_pipeline (ok)" not in out


def test_chat_confirmation_escapes_control_characters_in_file_names(env, chat, capsys):
    hostile = env["reads"].parent / "reads\x1b[2K\rclean"
    hostile.mkdir()
    replies = [tool_call("run_pipeline", domain="demo", inputs={"reads": str(hostile)},
                         run_id="c1"), "Started.\x1b[2J\nDone."]
    code, _, _ = chat(replies, ["run it", "n"])
    assert code == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out  # nothing raw reaches the terminal
    assert "reads\\x1b[2K\\rclean" in out  # shown escaped in the inputs and the command
    command_line = next(line for line in out.splitlines() if line.startswith("  command  "))
    assert env["nextflow"] in command_line and "reads\\x1b[2K\\rclean" in command_line
    assert "Started.\\x1b[2J\nDone." in out  # the answer keeps its line break


def test_confirmation_escapes_control_characters_in_parameters():
    request = ConfirmationRequest(
        tool="run_pipeline",
        arguments={},
        summary="nextflow run main.nf --label 'ok\x1b[2K\rnextflow run harmless.nf'",
        details={"domain": "demo", "params": {"label": "evil\x1b[2K\rlooks fine"},
                 "inputs": {"reads": "/data/\u202ereads"}},
    )
    app = ChatApp.__new__(ChatApp)  # only the formatting is under test
    app.style = Style(False)
    text = app._plan_text(request)
    assert "\x1b" not in text and "\r" not in text and "\u202e" not in text
    assert "label = evil\\x1b[2K\\rlooks fine" in text
    assert "/data/\\u202ereads" in text
    assert ("command  nextflow run main.nf --label 'ok\\x1b[2K\\rnextflow run harmless.nf'"
            in text)


def test_printable_escapes_control_and_format_characters():
    assert printable("a\x1b[31mb\rc\x07") == "a\\x1b[31mb\\rc\\x07"
    assert printable("x\u202ey\u200bz") == "x\\u202ey\\u200bz"
    assert printable("line 1\nline 2\tok", keep_newlines=True) == "line 1\nline 2\tok"
    assert printable("line 1\nline 2") == "line 1\\nline 2"
    assert printable("ação · ✓ 測試") == "ação · ✓ 測試"


def test_chat_help_explains_ctrl_c(env, chat, capsys):
    chat([], ["/help"])
    out = capsys.readouterr().out
    assert "empty prompt" in out and "130" in out


def test_chat_declined_confirmation_runs_nothing(env, chat, capsys):
    replies = [tool_call("run_pipeline", **run_args(env)), "Understood, nothing was run."]
    code, provider, _ = chat(replies, ["run it", "n"])
    assert code == 0
    out = capsys.readouterr().out
    assert "not run" in out and "nothing was run" in out
    assert not (env["ws"] / "runs" / "c1").exists()
    result = provider.calls[-1].messages[-1].tool_results[0]
    assert result.is_error and "confirmation_declined" in result.content


def test_chat_yes_runs_without_asking(env, chat, capsys):
    replies = [tool_call("run_pipeline", **run_args(env, "auto")), "Done."]
    code, _, prompts = chat(replies, ["run it"], "--yes")
    assert code == 0
    assert not any("Run it?" in p for p in prompts)
    assert (env["ws"] / "runs" / "auto" / "run-auto.json").is_file()


def test_chat_json_prints_one_object_per_turn(env, chat, capsys):
    code, _, _ = chat(["Hello!"], ["hi", "/exit"], "--json")
    assert code == 0
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1
    turn = json.loads(lines[0])
    assert turn["text"] == "Hello!" and turn["stop_reason"] == "completed"
    assert "XcaliburAI" in captured.err  # banner kept off stdout


def test_chat_session_commands_and_end_of_input(env, chat, capsys):
    code, provider, _ = chat([], ["/help", "/bogus", "/runs"])
    assert code == 0  # EOF (Ctrl+D) ends the session
    out = capsys.readouterr().out
    assert "/status" in out and "unknown command /bogus" in out and "no runs in" in out
    assert provider.calls == []  # session commands never reach the model


def test_chat_names_its_configuration_and_warns_when_it_replaces_the_user_one(
    env, chat, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    code, _, _ = chat([], ["/status"])
    assert code == 0
    captured = capsys.readouterr()
    local = env["ws"] / "xcalibur.yaml"
    assert f"config     {local} (workspace file, not trusted" in captured.out
    assert "replaces your user configuration" not in captured.err

    user = tmp_path / "cfg" / "xcalibur" / "xcalibur.yaml"
    user.parent.mkdir(parents=True)
    user.write_text("llm:\n  provider: anthropic\n  model: user-model\n")
    chat([], ["/exit"])
    err = capsys.readouterr().err
    assert f"warning: {local} replaces your user configuration {user}" in err


def test_chat_ctrl_c_stops_the_answer_but_not_the_session(env, chat, capsys):
    def interrupt(messages, tools):
        raise KeyboardInterrupt

    code, provider, _ = chat([interrupt, "Still here."], ["first", "second", "/exit"])
    assert code == 0
    out = capsys.readouterr().out
    assert "interrupted; the session continues" in out and "Still here." in out


def test_chat_llm_error_is_shown_and_the_session_goes_on(env, chat, capsys):
    code, _, _ = chat([], ["hello", "/exit"])  # no scripted reply -> LLMError
    assert code == 0
    assert "The language model failed" in capsys.readouterr().out


def test_chat_without_api_key_fails_before_the_session(env, monkeypatch, capsys):
    (env["ws"] / "xcalibur.yaml").write_text("llm:\n  provider: anthropic\n  model: test-model\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("prompted"))
    assert cli.main(["chat", "--workspace", str(env["ws"])]) == 2
    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY is not set" in captured.err and "you ›" not in captured.out


def test_chat_without_configuration_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert cli.main(["chat", "--workspace", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "xcalibur init" in err and "Traceback" not in err


# -- terminal -----------------------------------------------------------------------------


class _Tty:
    def isatty(self):
        return True


def test_colour_rules(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert color_enabled(_Tty()) is True
    assert color_enabled(_Tty(), no_color=True) is False
    monkeypatch.setenv("NO_COLOR", "1")
    assert color_enabled(_Tty()) is False
    assert Style(True)("brand", "x").startswith("\033[") and Style(False)("brand", "x") == "x"


def test_progress_drops_the_logs_own_escape_codes(capsys):
    line = "\033[33mNextflow 26.10.7 is available\033(B\033[m"
    assert strip_ansi(line) == "Nextflow 26.10.7 is available"
    stderr_printer(Style(False))(line)
    assert capsys.readouterr().err == "  │ Nextflow 26.10.7 is available\n"


def test_log_follower_streams_new_lines(tmp_path):
    log = tmp_path / "nextflow.log"
    log.write_text("old line\n")
    seen: list[str] = []
    follower = LogFollower(log, seen.append, from_end=True, poll=0.01).start()
    with log.open("a") as fh:
        fh.write("step 1\nstep 2\npartial")
    deadline = time.time() + 2
    while "step 2" not in seen and time.time() < deadline:
        time.sleep(0.01)
    follower.stop()
    assert seen == ["step 1", "step 2", "partial"]


def test_log_follower_final_read_waits_for_a_read_in_progress(tmp_path):
    """stop() reads under the same lock as the thread, so the two never interleave."""
    log = tmp_path / "nextflow.log"
    log.write_text("a\nb\n")
    seen: list[str] = []
    follower = LogFollower(log, seen.append, poll=0.01)
    follower._lock.acquire()  # a read "in progress" that the final read must wait for
    done = threading.Event()
    threading.Thread(target=lambda: (follower._read(final=True), done.set())).start()
    assert not done.wait(0.1)
    follower._lock.release()
    assert done.wait(2)
    assert seen == ["a", "b"]


def test_profile_reaches_the_command_and_is_validated(env, capsys):
    assert cli.main(["plan", "demo", "-i", f"reads={env['reads']}", "--profile", "test,docker",
                     "--json"]) == 0  # fmt: skip
    plan = json.loads(capsys.readouterr().out)
    assert plan["profile"] == "test,docker"
    assert plan["command"][plan["command"].index("-profile") + 1] == "test,docker"
    # "--profile=VALUE" so argparse hands even "-resume" to the profile validation.
    for bad in ("x; rm -rf /", "-resume", "test,-x", "test,", ""):
        assert cli.main(["plan", "demo", f"--profile={bad}"]) == 2, bad
