"""Which software a Nextflow run actually used, for its provenance record (issue #70).

Read after the run from the task directories (``<run_dir>/work/*/*/.command.run``), which
Nextflow writes for every task:

* the task's process name (the ``### name:`` header);
* the conda environment it activated (``conda|mamba|micromamba activate <path>``); the
  packages the process asked for are read from that environment's ``conda-meta``
  (entries with a ``requested_spec``), with their installed version;
* or the container it ran in (``docker|podman run ... --name <box> <image> /bin/...``);
  the image is described with ``docker image inspect`` (id, digests) and, when the image
  has the label :data:`VERSIONS_LABEL`, its tool list is read from the file it names.

Nothing here can fail a run: problems become ``notes`` in the result. Task directories
left by earlier attempts of the same run (``-resume``) are read too.

Time (issue #97): the whole collection stops at :data:`TOTAL_TIMEOUT` seconds, and what
was left out becomes a note. A cancelled run is collected with ``describe_images=False``:
its images are listed but Docker is not called, so cancelling is not delayed.

Limits: only Docker and Podman containers are recognized (not Singularity/Apptainer),
and a conda environment path with spaces is read only when Nextflow quotes it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

VERSIONS_LABEL = "org.xcalibur.tool-versions"
"""Image label naming a TSV file inside the image: ``tool<TAB>version[<TAB>...]``."""
MAX_TASKS = 20000
DOCKER_TIMEOUT = 60
"""Seconds for one Docker call."""
TOTAL_TIMEOUT = 120
"""Seconds for the whole collection."""

# "### name: 'TRIMMING_QC:FASTQC_RAW (SA1)'" (Nextflow 25; 26 uses double quotes): the
# tag in parentheses is the task's, not the process's.
_NAME = re.compile(r"""^### name: (['"])(.+?)(?: \(.*\))?\1\s*$""")
_PATH = r"""('[^']+'|"[^"]+"|\S+)"""
_CONDA = re.compile(r"\b(?:conda|mamba|micromamba)\s+activate\s+" + _PATH)
_SOURCE = re.compile(r"\bsource\s+activate\s+" + _PATH)
_CONTAINER = re.compile(r"\b(docker|podman)\s+run\b.*?--name\s+\S+\s+(\S+)\s+/bin/(?:ba)?sh\b")


def collect_software(run_dir: Path, docker: str = "docker", *, describe_images: bool = True,
                     timeout: float | None = None) -> dict[str, Any] | None:
    """Software used by the tasks of a run, or None when no task directory exists.

    ``describe_images=False`` lists the images without calling Docker (cancelled runs).
    ``timeout`` (default :data:`TOTAL_TIMEOUT`) bounds the whole collection.
    """
    deadline = time.monotonic() + (TOTAL_TIMEOUT if timeout is None else timeout)
    work = run_dir / "work"
    if not work.is_dir():
        return None
    notes: list[str] = []
    processes: dict[str, dict[str, Any]] = {}
    envs: dict[str, dict[str, str]] = {}
    images: dict[str, str] = {}  # image -> engine (docker/podman)
    scripts = sorted(work.glob("*/*/.command.run"))
    if len(scripts) > MAX_TASKS:
        notes.append(f"only the first {MAX_TASKS} of {len(scripts)} task directories read")
        scripts = scripts[:MAX_TASKS]
    for index, script in enumerate(scripts):
        if time.monotonic() >= deadline:
            notes.append(f"time limit reached: only {index} of {len(scripts)} task "
                         "directories read")
            break
        task = _read_task(script)
        if task is None or task["process"] is None:
            continue
        entry = processes.setdefault(task["process"], {})
        if task["conda"]:
            path = task["conda"]
            if path not in envs:
                envs[path] = _requested_packages(Path(path), notes)
            entry["conda"] = envs[path]
        if task["container"]:
            engine, image = task["container"]
            images.setdefault(image, engine)
            entry["container"] = image
    if not processes:
        return None

    if images and not describe_images:
        notes.append("run cancelled: images listed but not described (Docker not called)")
    containers = [_describe_image(image, engine, docker, notes, deadline) if describe_images
                  else {"image": image, "engine": engine}
                  for image, engine in sorted(images.items())]
    tools: dict[str, set[str]] = {}
    for packages in envs.values():
        for name, version in packages.items():
            tools.setdefault(name, set()).add(version)
    for container in containers:
        for name, version in container.get("tools", {}).items():
            tools.setdefault(name, set()).add(version)
    return {
        "collected_from": "work/*/*/.command.run",
        "tools": {name: sorted(v)[0] if len(v) == 1 else sorted(v)
                  for name, v in sorted(tools.items())},
        "processes": dict(sorted(processes.items())),
        "conda_envs": len(envs),
        "containers": containers,
        "notes": notes,
    }


def _read_task(script: Path) -> dict[str, Any] | None:
    task: dict[str, Any] = {"process": None, "conda": None, "container": None}
    try:
        with script.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if task["process"] is None and (match := _NAME.match(line)):
                    task["process"] = match.group(2)
                elif match := (_CONDA.search(line) or _SOURCE.search(line)):
                    task["conda"] = match.group(1).strip("'\"")
                elif match := _CONTAINER.search(line):
                    task["container"] = (match.group(1), match.group(2).strip("'\""))
    except OSError:
        return None
    return task


def _requested_packages(env: Path, notes: list[str]) -> dict[str, str]:
    """``name -> version`` of the packages explicitly requested for a conda environment."""
    meta = env / "conda-meta"
    if not meta.is_dir():
        notes.append(f"conda environment not found: {env}")
        return {}
    packages: dict[str, str] = {}
    for path in sorted(meta.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            notes.append(f"unreadable {path}")
            continue
        if data.get("requested_spec") and data.get("name") and data.get("version"):
            packages[data["name"]] = str(data["version"])
    if not packages:
        notes.append(f"no requested packages recorded in {meta}")
    return packages


def _remaining(deadline: float) -> float:
    return min(DOCKER_TIMEOUT, deadline - time.monotonic())


def _describe_image(image: str, engine: str, docker: str, notes: list[str],
                    deadline: float) -> dict[str, Any]:
    out: dict[str, Any] = {"image": image, "engine": engine}
    executable = docker if engine == "docker" else engine
    if image.startswith("-"):  # would be read as an option of the engine
        notes.append(f"image not described, its name starts with '-': {image}")
        return out
    if shutil.which(executable) is None:
        notes.append(f"{executable} not found: image {image} not described")
        return out
    if _remaining(deadline) <= 0:
        notes.append(f"time limit reached: image {image} not described")
        return out
    try:
        inspected = subprocess.run(  # noqa: S603 - image name comes from Nextflow's own script
            [executable, "image", "inspect", "--format", "{{json .}}", image],
            capture_output=True, text=True, timeout=_remaining(deadline), check=True,
        )
        data = json.loads(inspected.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        notes.append(f"could not inspect {image}: {exc}")
        return out
    out["id"] = data.get("Id")
    out["digests"] = data.get("RepoDigests") or []
    labels = (data.get("Config") or {}).get("Labels") or {}
    versions_file = labels.get(VERSIONS_LABEL)
    if versions_file and not versions_file.startswith("/"):
        notes.append(f"{VERSIONS_LABEL} of {image} is not an absolute path: {versions_file}")
    elif versions_file and _remaining(deadline) <= 0:
        notes.append(f"time limit reached: tool list of {image} not read")
    elif versions_file:
        try:
            listed = subprocess.run(  # noqa: S603
                [executable, "run", "--rm", "--entrypoint", "cat", image, versions_file],
                capture_output=True, text=True, timeout=_remaining(deadline), check=True,
            )
            out["tools"] = _parse_versions(listed.stdout)
        except (OSError, subprocess.SubprocessError) as exc:
            notes.append(f"could not read {versions_file} from {image}: {exc}")
    return out


def _parse_versions(text: str) -> dict[str, str]:
    tools: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] and parts[1]:
            tools[parts[0]] = parts[1]
    return tools
