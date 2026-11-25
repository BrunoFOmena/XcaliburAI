"""Tool versions in the run record (issue #70), from task directories Nextflow leaves."""

import json
import stat
from pathlib import Path

from xcalibur.execution import software
from xcalibur.execution.base import RunSpec
from xcalibur.execution.nextflow import NextflowEngine
from xcalibur.execution.software import VERSIONS_LABEL, collect_software
from xcalibur.manifest import DomainManifest
from xcalibur.provenance import RunRecord
from xcalibur.registry import DomainPlugin
from xcalibur.workspace import Workspace

IMAGE = "ghcr.io/x/echolas:1.0.0"


def conda_env(root: Path, name: str, packages: dict[str, str], extra=("zlib", "1.3")) -> Path:
    meta = root / name / "conda-meta"
    meta.mkdir(parents=True)
    for package, version in packages.items():
        (meta / f"{package}-{version}-h0.json").write_text(json.dumps({
            "name": package, "version": version, "build": "h0",
            "requested_spec": f"bioconda::{package}={version}"}))
    # A dependency nobody asked for: not reported.
    (meta / f"{extra[0]}-{extra[1]}-h0.json").write_text(json.dumps(
        {"name": extra[0], "version": extra[1], "requested_spec": ""}))
    return root / name


def task(work: Path, hash_: str, name: str, body: str, quote: str = "'") -> None:
    folder = work / hash_[:2] / hash_[2:]
    folder.mkdir(parents=True)
    (folder / ".command.run").write_text(
        f"#!/bin/bash\n### ---\n### name: {quote}{name}{quote}\n### ...\nset -e\n{body}\n")


def conda_line(env: Path) -> str:
    return f'    eval "$(micromamba shell hook --shell bash)" && micromamba activate {env}'


def docker_line(image: str) -> str:
    return ('    docker run -i --cpu-shares 2048 -v /w:/w -w "$NXF_TASK_WORKDIR" '
            '-u $(id -u):$(id -g) --env PATH=/opt/a:/bin --name $NXF_BOXID '
            f'{image} /bin/bash /w/.command.run nxf_trace')


def fake_docker(tmp_path: Path, labels=None, tools="fastqc\t0.12.1\tFastQC v0.12.1\n", fail=False):
    """A docker executable answering `image inspect` and `run ... cat <file>`."""
    inspect = {"Id": "sha256:abc", "RepoDigests": [f"{IMAGE.split(':')[0]}@sha256:def"],
               "Config": {"Labels": labels if labels is not None
                          else {VERSIONS_LABEL: "/opt/tools.tsv"}}}
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir(exist_ok=True)
    (tmp_path / "inspect.json").write_text(json.dumps(inspect))
    (tmp_path / "tools.tsv").write_text(tools)
    script.write_text(
        "#!/bin/sh\n"
        + ("exit 1\n" if fail else "")
        + f'case "$1 $2" in "image inspect") cat {tmp_path}/inspect.json;; '
        f'"run --rm") cat {tmp_path}/tools.tsv;; esac\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_no_work_directory_means_no_software(tmp_path):
    assert collect_software(tmp_path) is None
    (tmp_path / "work").mkdir()
    assert collect_software(tmp_path) is None


def test_conda_packages_per_process_and_merged(tmp_path):
    work = tmp_path / "work"
    pca = conda_env(tmp_path / "envs", "env-pca", {"plink": "1.9.0b.7.7", "r-base": "4.5.3"})
    bwa = conda_env(tmp_path / "envs", "env-bwa", {"bwa": "0.7.17", "samtools": "1.19.2"})
    task(work, "aabbcc", "PCA_PHYLOGENY:RUN_PCA (1)", conda_line(pca))
    task(work, "aabbdd", "ALIGNMENT_VARIANT:ALIGN_SORT (SA1)", conda_line(bwa))
    task(work, "aabbee", "ALIGNMENT_VARIANT:ALIGN_SORT (SA2)", conda_line(bwa))
    task(work, "ffeedd", "GENOTYPING:MAKE_GVCF_MAP", "echo no environment")
    result = collect_software(tmp_path)
    assert result["tools"] == {"bwa": "0.7.17", "plink": "1.9.0b.7.7", "r-base": "4.5.3",
                               "samtools": "1.19.2"}
    assert result["processes"] == {
        "ALIGNMENT_VARIANT:ALIGN_SORT": {"conda": {"bwa": "0.7.17", "samtools": "1.19.2"}},
        "GENOTYPING:MAKE_GVCF_MAP": {},
        "PCA_PHYLOGENY:RUN_PCA": {"conda": {"plink": "1.9.0b.7.7", "r-base": "4.5.3"}},
    }
    assert result["conda_envs"] == 2 and result["containers"] == [] and result["notes"] == []


def test_both_nextflow_header_styles_are_read(tmp_path):
    # Nextflow 25 quotes the task name with ', Nextflow 26 with ".
    env = conda_env(tmp_path / "envs", "env", {"bwa": "0.7.17"})
    task(tmp_path / "work", "aa1111", "A:OLD (SA1)", conda_line(env), quote="'")
    task(tmp_path / "work", "bb2222", "B:NEW (SA1)", conda_line(env), quote='"')
    assert list(collect_software(tmp_path)["processes"]) == ["A:OLD", "B:NEW"]


def test_two_versions_of_one_tool_are_both_kept(tmp_path):
    work = tmp_path / "work"
    old = conda_env(tmp_path / "envs", "old", {"samtools": "1.19.2"})
    new = conda_env(tmp_path / "envs", "new", {"samtools": "1.21"})
    task(work, "aa1111", "A", conda_line(old))
    task(work, "bb2222", "B", conda_line(new))
    assert collect_software(tmp_path)["tools"] == {"samtools": ["1.19.2", "1.21"]}


def test_missing_environment_is_a_note(tmp_path):
    task(tmp_path / "work", "aa1111", "A", conda_line(tmp_path / "gone"))
    result = collect_software(tmp_path)
    assert result["tools"] == {} and "conda environment not found" in result["notes"][0]


def test_container_image_is_described_with_its_tool_list(tmp_path):
    task(tmp_path / "work", "aa1111", "A (1)", docker_line(IMAGE))
    task(tmp_path / "work", "bb2222", "B", docker_line(IMAGE))
    result = collect_software(tmp_path, docker=fake_docker(tmp_path))
    assert result["processes"] == {"A": {"container": IMAGE}, "B": {"container": IMAGE}}
    (container,) = result["containers"]
    assert container["image"] == IMAGE and container["id"] == "sha256:abc"
    assert container["digests"] == ["ghcr.io/x/echolas@sha256:def"]
    assert container["tools"] == {"fastqc": "0.12.1"}
    assert result["tools"] == {"fastqc": "0.12.1"}


def test_image_without_the_label_has_no_tool_list(tmp_path):
    task(tmp_path / "work", "aa1111", "A", docker_line(IMAGE))
    result = collect_software(tmp_path, docker=fake_docker(tmp_path, labels={}))
    assert "tools" not in result["containers"][0] and result["tools"] == {}


def test_docker_problems_are_notes_not_errors(tmp_path):
    task(tmp_path / "work", "aa1111", "A", docker_line(IMAGE))
    failing = collect_software(tmp_path, docker=fake_docker(tmp_path, fail=True))
    assert failing["containers"] == [{"image": IMAGE, "engine": "docker"}]
    assert "could not inspect" in failing["notes"][0]
    missing = collect_software(tmp_path, docker=str(tmp_path / "no-docker"))
    assert "not found" in missing["notes"][0]


def test_unreadable_metadata_is_skipped(tmp_path):
    env = conda_env(tmp_path / "envs", "env", {"bwa": "0.7.17"})
    (env / "conda-meta" / "broken.json").write_text("{")
    task(tmp_path / "work", "aa1111", "A", conda_line(env))
    result = collect_software(tmp_path)
    assert result["tools"] == {"bwa": "0.7.17"} and "unreadable" in result["notes"][0]


def test_task_limit_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(software, "MAX_TASKS", 1)
    task(tmp_path / "work", "aa1111", "A", "true")
    task(tmp_path / "work", "bb2222", "B", "true")
    result = collect_software(tmp_path)
    assert list(result["processes"]) == ["A"] and "only the first 1 of 2" in result["notes"][0]


# -- written by the engine ----------------------------------------------------------------

FAKE_NEXTFLOW = """#!/bin/sh
if [ "$1" = "-version" ]; then echo "version 24.04.0 build 5917"; exit 0; fi
mkdir -p work/ab/cdef
printf "#!/bin/bash\\n### name: 'QC:FASTQC_RAW (SA1)'\\n    micromamba activate %s\\n" "$ENV" \
  > work/ab/cdef/.command.run
case "$*" in *--fail*) exit 3;; esac
exit 0
"""


def run_with_fake(tmp_path, monkeypatch, params):
    env = conda_env(tmp_path / "envs", "env-qc", {"fastqc": "0.12.1"})
    monkeypatch.setenv("ENV", str(env))
    nextflow = tmp_path / "bin" / "nextflow"
    nextflow.parent.mkdir(exist_ok=True)
    nextflow.write_text(FAKE_NEXTFLOW)
    nextflow.chmod(nextflow.stat().st_mode | stat.S_IEXEC)
    manifest = DomainManifest.model_validate({
        "name": "demo", "display_name": "demo", "version": "1.0.0",
        "workflow": {"entrypoint": "main.nf"}, "params": [{"name": "fail", "default": "no"}]})
    spec = RunSpec(plugin=DomainPlugin(manifest=manifest, root=Path("/pkg")),
                   params=params, workspace=Workspace(tmp_path / "ws").ensure(), dry_run=False)
    return NextflowEngine(str(nextflow)).run(spec)


def test_engine_records_the_software_of_successful_and_failed_runs(tmp_path, monkeypatch):
    for params, status in (({}, "succeeded"), ({"fail": "yes"}, "failed")):
        result = run_with_fake(tmp_path / status, monkeypatch, params)
        record = json.loads(result.record_path.read_text())
        assert record["status"] == status
        assert record["software"]["tools"] == {"fastqc": "0.12.1"}
        assert record["software"]["processes"] == {
            "QC:FASTQC_RAW": {"conda": {"fastqc": "0.12.1"}}}


def test_engine_never_fails_because_of_the_collection(tmp_path, monkeypatch):
    def broken(run_dir):
        raise RuntimeError("boom")

    monkeypatch.setattr("xcalibur.execution.nextflow.collect_software", broken)
    result = run_with_fake(tmp_path, monkeypatch, {})
    record = json.loads(result.record_path.read_text())
    assert result.status == "succeeded"
    assert record["software"] == {"notes": ["software could not be collected: RuntimeError: boom"]}


def test_old_records_without_software_still_load():
    record = RunRecord.model_validate({
        "run_id": "r1", "domain": "demo", "domain_version": "1", "engine": "nextflow",
        "command": ["nextflow"], "params": {}})
    assert record.software is None
