import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from xcalibur.execution import nextflow
from xcalibur.execution.base import EngineNotFoundError, RunSpec, UnsupportedPlatformError
from xcalibur.execution.nextflow import NextflowEngine
from xcalibur.manifest import DomainManifest
from xcalibur.registry import DomainPlugin
from xcalibur.workspace import Workspace

FAKE = """#!/bin/sh
if [ "$1" = "-version" ]; then
  printf "      N E X T F L O W\\n      version 24.04.0 build 5917\\n"; exit 0
fi
echo "args: $@"
case "$*" in *--fail*) echo "boom" >&2; exit 3;; esac
case "$*" in *--hang*) sleep 30;; esac
case "$*" in *--leak*) sleep 30 & echo "left $!"; exit 1;; esac
exit 0
"""


def fake_nextflow(tmp_path: Path) -> str:
    path = tmp_path / "bin" / "nextflow"
    path.parent.mkdir()
    path.write_text(FAKE)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def make_plugin(container="img:1"):
    manifest = DomainManifest.model_validate(
        {
            "name": "demo",
            "display_name": "demo",
            "version": "1.2.3",
            "workflow": {"entrypoint": "main.nf", "container": container},
            "params": [{"name": "mode", "default": "AUTO"}],
        }
    )
    return DomainPlugin(manifest=manifest, root=Path("/pkg"))


def spec(tmp_path, **kw):
    kw.setdefault("params", {"mode": "AUTO"})
    return RunSpec(
        plugin=make_plugin(), workspace=Workspace(tmp_path / "ws").ensure(), dry_run=False, **kw
    )


def test_success_creates_run_dir_log_and_record(tmp_path):
    s = spec(tmp_path)
    result = NextflowEngine(fake_nextflow(tmp_path)).run(s)
    assert result.status == "succeeded" and result.returncode == 0
    assert result.run_dir == tmp_path / "ws" / "runs" / s.run_id
    assert "args: run /pkg/main.nf" in result.log_path.read_text()
    record = json.loads(result.record_path.read_text())
    assert record["status"] == "succeeded"
    assert record["run_id"] == s.run_id
    assert record["engine_version"] == "version 24.04.0 build 5917"
    assert record["container"] == "img:1"
    assert record["params"] == {"mode": "AUTO"}
    assert record["command"][1] == "run"


def test_failure_preserves_log_and_returncode(tmp_path):
    result = NextflowEngine(fake_nextflow(tmp_path)).run(spec(tmp_path, params={"fail": True}))
    assert result.status == "failed" and result.returncode == 3
    assert "boom" in result.log_path.read_text()
    assert json.loads(result.record_path.read_text())["returncode"] == 3


def test_missing_binary_gives_clear_error(tmp_path):
    with pytest.raises(EngineNotFoundError, match="not found"):
        NextflowEngine("definitely-not-installed").run(spec(tmp_path))
    assert list((tmp_path / "ws" / "runs").iterdir()) == []


def test_dry_run_touches_nothing(tmp_path):
    s = RunSpec(plugin=make_plugin(), params={}, workspace=Workspace(tmp_path / "ws"))
    result = NextflowEngine("definitely-not-installed").run(s)
    assert result.status == "planned"
    assert not (tmp_path / "ws").exists()


def test_resume_flag(tmp_path):
    s = spec(tmp_path, resume=True)
    assert "-resume" in NextflowEngine().build_command(s)
    assert "-resume" not in NextflowEngine().build_command(spec(tmp_path))


def test_command_is_a_list_with_no_shell_interpretation(tmp_path):
    cmd = NextflowEngine().build_command(spec(tmp_path, params={"x": "a; rm -rf /"}))
    assert "a; rm -rf /" in cmd and isinstance(cmd, list)


def test_execution_requires_workspace():
    s = RunSpec(plugin=make_plugin(), params={}, dry_run=False)
    with pytest.raises(ValueError, match="workspace is required"):
        NextflowEngine("nextflow").run(s)


def test_relative_paths_become_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = RunSpec(
        plugin=make_plugin(),
        params={},
        inputs={"reads": Path("data/r.fq")},
        workspace=Workspace(Path("ws")),
    )
    cmd = NextflowEngine().build_command(s)
    assert cmd[cmd.index("--reads") + 1] == str(tmp_path / "data" / "r.fq")
    assert Path(cmd[cmd.index("-with-report") + 1]).is_absolute()


def test_option_like_value_is_rejected_but_negative_numbers_pass(tmp_path):
    engine = NextflowEngine()
    with pytest.raises(ValueError, match="engine option"):
        engine.build_command(spec(tmp_path, params={"x": "-with-docker"}))
    assert "-1" in engine.build_command(spec(tmp_path, params={"x": -1}))


def test_resume_reuses_run_dir_appends_log_and_replaces_report(tmp_path):
    engine = NextflowEngine(fake_nextflow(tmp_path))
    first = spec(tmp_path)
    r1 = engine.run(first)
    report = r1.run_dir / "nextflow_report.html"
    report.write_text("old")
    r2 = engine.run(spec(tmp_path, run_id=first.run_id, resume=True))
    assert r2.run_dir == r1.run_dir and r2.status == "succeeded"
    assert r2.log_path.read_text().count("args: run") == 2
    assert not report.exists()
    assert json.loads(r2.record_path.read_text())["resumed"] is True


@pytest.mark.skipif(sys.platform == "win32", reason="posix signals")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_signal_cancels_run(tmp_path, sig):
    script = f"""
import sys
sys.path.insert(0, {str(Path(__file__).parent.parent / "src")!r})
from pathlib import Path
from xcalibur.execution.base import RunSpec
from xcalibur.execution.nextflow import NextflowEngine
from xcalibur.manifest import DomainManifest
from xcalibur.registry import DomainPlugin
from xcalibur.workspace import Workspace
wf = {{"entrypoint": "m.nf"}}
m = DomainManifest.model_validate(
    {{"name": "d", "display_name": "d", "version": "1", "workflow": wf}}
)
s = RunSpec(plugin=DomainPlugin(manifest=m, root=Path("/p")), params={{"hang": True}},
            workspace=Workspace(Path({str(tmp_path / "ws")!r})).ensure(), dry_run=False)
print(NextflowEngine({fake_nextflow(tmp_path)!r}).run(s).status, flush=True)
"""
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    deadline = time.time() + 15
    while time.time() < deadline and not list((tmp_path / "ws" / "runs").glob("*/nextflow.log")):
        time.sleep(0.1)
    time.sleep(0.5)
    os.kill(proc.pid, sig)
    out, _ = proc.communicate(timeout=20)
    assert out.strip() == "cancelled"
    record = next((tmp_path / "ws" / "runs").glob("*/run-*.json"))
    assert json.loads(record.read_text())["status"] == "cancelled"


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "a b", "x\n", ".."])
def test_invalid_run_id_is_rejected_without_touching_disk(tmp_path, bad):
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(ValueError, match="Invalid run_id"):
        RunSpec(plugin=make_plugin(), params={}, workspace=ws, run_id=bad)
    with pytest.raises(ValueError, match="Invalid run_id"):
        ws.run_dir(bad)
    assert not (tmp_path / "ws").exists()


def test_resume_keeps_one_record_per_attempt(tmp_path):
    engine = NextflowEngine(fake_nextflow(tmp_path))
    first = spec(tmp_path, params={"fail": True})
    r1 = engine.run(first)
    r2 = engine.run(spec(tmp_path, run_id=first.run_id, resume=True))
    r3 = engine.run(spec(tmp_path, run_id=first.run_id, resume=True))
    assert r1.record_path.name == f"run-{first.run_id}.json"
    assert r2.record_path.name == f"run-{first.run_id}.attempt-2.json"
    assert r3.record_path.name == f"run-{first.run_id}.attempt-3.json"
    records = [json.loads(r.record_path.read_text()) for r in (r1, r2, r3)]
    assert [r["attempt"] for r in records] == [1, 2, 3]
    assert records[0]["status"] == "failed" and records[0]["resumed"] is False
    assert records[1]["resumed"] is True


def test_report_kept_when_resume_cannot_start(tmp_path):
    engine = NextflowEngine(fake_nextflow(tmp_path))
    first = spec(tmp_path)
    run_dir = engine.run(first).run_dir
    report = run_dir / "nextflow_report.html"
    report.write_text("old")
    with pytest.raises(EngineNotFoundError):
        NextflowEngine("definitely-not-installed").run(
            spec(tmp_path, run_id=first.run_id, resume=True)
        )
    assert report.read_text() == "old"


def test_record_inputs_are_absolute_and_stdin_is_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = spec(tmp_path, inputs={"reads": Path("data/r.fq")})
    result = NextflowEngine(fake_nextflow(tmp_path)).run(s)
    inputs = json.loads(result.record_path.read_text())["inputs"]
    assert inputs == {"reads": str(tmp_path / "data" / "r.fq")}


def test_profile_overrides_the_manifest_default(tmp_path):
    plugin = make_plugin()
    plugin.manifest.workflow.profile = "standard"
    base = {"plugin": plugin, "params": {}}
    assert NextflowEngine().build_command(RunSpec(**base))[3:5] == ["-profile", "standard"]
    cmd = NextflowEngine().build_command(RunSpec(**base, profile="test"))
    assert cmd[3:5] == ["-profile", "test"] and "standard" not in cmd


# -- cancellation lifecycle (#20) --------------------------------------------------------

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="posix signals")


def group_is_gone(pgid: int, timeout: float = 5.0) -> bool:
    """True once no process (Nextflow or a child it started) is left in the group."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def track_start(monkeypatch, interrupt: bool) -> list[int]:
    """Record the run's pid; with ``interrupt``, send SIGINT to ourselves right after the
    run's Popen returns, which was the race window before #20."""
    started: list[int] = []
    real_popen = subprocess.Popen

    def popen(cmd, *args, **kwargs):
        proc = real_popen(cmd, *args, **kwargs)
        if list(cmd[1:2]) == ["run"]:
            started.append(proc.pid)
            if interrupt:
                os.kill(os.getpid(), signal.SIGINT)
        return proc

    monkeypatch.setattr(nextflow.subprocess, "Popen", popen)
    return started


@posix_only
@pytest.mark.parametrize("attempt", range(5))
def test_signal_right_after_start_cancels_and_leaves_no_orphan(tmp_path, monkeypatch, attempt):
    before = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    started = track_start(monkeypatch, interrupt=True)
    result = NextflowEngine(fake_nextflow(tmp_path)).run(spec(tmp_path, params={"hang": True}))
    assert result.status == "cancelled"
    assert json.loads(result.record_path.read_text())["status"] == "cancelled"
    assert "run cancelled" in result.log_path.read_text()
    assert len(started) == 1 and group_is_gone(started[0])
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before


@posix_only
def test_signal_before_start_cancels_without_starting_nextflow(tmp_path, monkeypatch):
    engine = NextflowEngine(fake_nextflow(tmp_path))
    started = track_start(monkeypatch, interrupt=False)

    def version():  # Ctrl+C while the engine version is read (a JVM start-up)
        os.kill(os.getpid(), signal.SIGINT)
        return "version 24.04.0"

    monkeypatch.setattr(engine, "version", version)
    result = engine.run(spec(tmp_path, params={"hang": True}))
    assert result.status == "cancelled" and result.returncode is None
    assert started == []
    assert "args: run" not in result.log_path.read_text()
    assert json.loads(result.record_path.read_text())["status"] == "cancelled"


@posix_only
def test_second_interrupt_does_not_stop_the_cleanup(tmp_path, monkeypatch):
    started = track_start(monkeypatch, interrupt=True)
    real_signal_group = nextflow._signal_group

    def impatient(proc, sig):  # Ctrl+C again while xcalibur stops Nextflow
        os.kill(os.getpid(), signal.SIGINT)
        real_signal_group(proc, sig)

    monkeypatch.setattr(nextflow, "_signal_group", impatient)
    result = NextflowEngine(fake_nextflow(tmp_path)).run(spec(tmp_path, params={"hang": True}))
    assert result.status == "cancelled"
    assert json.loads(result.record_path.read_text())["status"] == "cancelled"
    assert group_is_gone(started[0])


@posix_only
def test_unexpected_error_while_waiting_still_stops_the_group(tmp_path, monkeypatch):
    started = track_start(monkeypatch, interrupt=False)

    def broken_wait(proc, cancel):
        raise RuntimeError("boom")

    monkeypatch.setattr(nextflow, "_wait", broken_wait)
    with pytest.raises(RuntimeError, match="boom"):
        NextflowEngine(fake_nextflow(tmp_path)).run(spec(tmp_path, params={"hang": True}))
    assert group_is_gone(started[0])


@posix_only
def test_processes_left_behind_by_nextflow_are_stopped(tmp_path, monkeypatch):
    """Nextflow can fail and exit while a task it started (a conda build) still runs."""
    started = track_start(monkeypatch, interrupt=False)
    result = NextflowEngine(fake_nextflow(tmp_path)).run(spec(tmp_path, params={"leak": True}))
    assert result.status == "failed" and result.returncode == 1
    assert "left " in result.log_path.read_text()
    assert group_is_gone(started[0], timeout=1.0)


def test_platform_without_process_groups_gets_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delattr(nextflow.os, "killpg", raising=False)
    with pytest.raises(UnsupportedPlatformError, match="Linux and macOS only"):
        NextflowEngine(fake_nextflow(tmp_path)).run(spec(tmp_path))
    assert list((tmp_path / "ws" / "runs").iterdir()) == []
    # Planning does not need process groups.
    planned = RunSpec(plugin=make_plugin(), params={}, workspace=Workspace(tmp_path / "ws"))
    assert NextflowEngine("nextflow").run(planned).status == "planned"
