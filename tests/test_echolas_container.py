"""ECHOLAS reproducible environment (issue #4): pins, locks, image and profile agree."""

import re
import tomllib
from pathlib import Path

import yaml

from xcalibur_echolas import plugin

PKG = Path(__file__).resolve().parents[1] / "packages" / "xcalibur-echolas"
WORKFLOW = PKG / "src" / "xcalibur_echolas" / "workflow"
ENVS = WORKFLOW / "envs"
DOCKER = PKG / "docker"
ENV_NAMES = ("echolas", "echolas-pca")
IMAGE = "ghcr.io/brunofomena/xcalibur-echolas"


def env_pins(name: str) -> dict[str, str]:
    data = yaml.safe_load((ENVS / f"{name}.yml").read_text(encoding="utf-8"))
    assert data["name"] == name
    pins = {}
    for dep in data["dependencies"]:
        package, _, version = dep.partition("=")
        if version:
            pins[package] = version
    return pins


def directives() -> dict[str, list[tuple[str, str]]]:
    """Process name -> [(package, version)] from every `conda` directive."""
    out: dict[str, list[tuple[str, str]]] = {}
    for module in (WORKFLOW / "modules").glob("*.nf"):
        text = module.read_text(encoding="utf-8")
        for match in re.finditer(r'process\s+(\w+)\s*\{(.*?)\n\}', text, re.S):
            conda = re.search(r'conda\s+"([^"]+)"', match.group(2))
            if conda:
                specs = [s.split("::")[-1] for s in conda.group(1).split()]
                out[match.group(1)] = [tuple(s.partition("=")[::2]) for s in specs]
    return out


def test_every_conda_directive_is_pinned_exactly():
    found = directives()
    assert found, "no conda directives found"
    for process, specs in found.items():
        for package, version in specs:
            assert re.fullmatch(r"[0-9][\w.]*", version), f"{process}: {package} is not pinned"


def test_directives_and_environment_files_use_the_same_versions():
    main, pca = env_pins("echolas"), env_pins("echolas-pca")
    assert not set(main) & set(pca), "a package is pinned in both environments"
    used: set[str] = set()
    for process, specs in directives().items():
        env = pca if process == "RUN_PCA" else main
        for package, version in specs:
            assert env.get(package) == version, (
                f"{process} pins {package}={version}, the environment has {env.get(package)}")
            used.add(package)
    assert used == set(main) | set(pca), "environment files pin tools no process uses"


def test_locks_are_explicit_and_hold_the_pinned_versions():
    for name in ENV_NAMES:
        lock = (ENVS / f"{name}.linux-64.lock").read_text(encoding="utf-8")
        urls = [line for line in lock.splitlines() if line and not line.startswith(("#", "@"))]
        assert "@EXPLICIT" in lock and urls
        assert all(re.search(r"#[0-9a-f]{32}$", url) for url in urls), "every URL needs an md5"
        for package, version in env_pins(name).items():
            assert any(f"/{package}-{version}-" in url for url in urls), (
                f"{name} lock has no {package}={version}; run docker/lock.sh")


def test_image_tag_is_the_package_version_in_manifest_and_profile():
    version = tomllib.loads((PKG / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"]
    expected = f"{IMAGE}:{version}"
    assert plugin().manifest.workflow.container == expected
    config = (WORKFLOW / "nextflow.config").read_text(encoding="utf-8")
    docker = re.search(r"^    docker \{(.*?)^    \}", config, re.S | re.M)
    assert docker, "nextflow.config has no docker profile"
    assert f"container = '{expected}'" in docker.group(1)
    assert "conda.enabled = false" in docker.group(1)
    assert "docker.enabled = true" in docker.group(1)
    assert "withName: 'RUN_PCA'" in docker.group(1)
    assert "/opt/conda/envs/echolas-pca/bin" in docker.group(1)


def test_dockerfile_installs_both_locks_and_checks_the_tools():
    dockerfile = (DOCKER / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM \S+:\S+", dockerfile, re.M), "base image needs a fixed tag"
    for name in ENV_NAMES:
        assert f"micromamba create -y -n {name} -f ${{ECHOLAS_HOME}}/envs/{name}.linux-64.lock" \
            in dockerfile
    assert "check-tools.sh" in dockerfile


def test_check_tools_covers_every_pinned_package():
    script = (DOCKER / "check-tools.sh").read_text(encoding="utf-8")
    cases = set(re.findall(r"^\s+([\w-]+)\)\s", script, re.M))
    for name in ENV_NAMES:
        missing = set(env_pins(name)) - cases
        assert not missing, f"check-tools.sh has no version check for {sorted(missing)}"


def test_environment_files_ship_with_the_package():
    data = tomllib.loads((PKG / "pyproject.toml").read_text(encoding="utf-8"))
    globs = data["tool"]["setuptools"]["package-data"]["xcalibur_echolas"]
    assert "workflow/**/*" in globs  # covers workflow/envs/*
