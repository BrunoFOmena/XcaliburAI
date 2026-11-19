import re
import tomllib
from pathlib import Path

from xcalibur.version import __version__

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def test_version_is_plain_semver():
    assert SEMVER.match(__version__), f"{__version__!r} is not MAJOR.MINOR.PATCH"


def test_changelog_has_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in changelog


def test_domain_packages_match_core_version_in_0x():
    major = int(__version__.split(".")[0])
    if major != 0:
        return  # lockstep only enforced during the 0.x phase
    pkg = ROOT / "packages" / "xcalibur-echolas"
    data = tomllib.loads((pkg / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__
    manifest = (pkg / "src/xcalibur_echolas/manifest.yaml").read_text(encoding="utf-8")
    assert re.search(rf"^version: {re.escape(__version__)}$", manifest, re.MULTILINE)
