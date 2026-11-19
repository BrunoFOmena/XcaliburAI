import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from xcalibur.execution.base import EngineNotFoundError, RunSpec
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
