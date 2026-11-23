"""Scenarios for .github/scripts/check-homolog-pr.sh (issue #33).

Each test builds a throwaway git repository that mimics the branch model
(work branch -> homolog -> main, promotions as merge commits) and runs the
script the branch-policy workflow runs for PRs into homolog.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "check-homolog-pr.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git and bash",
)


class Repo:
    def __init__(self, path: Path):
        self.path = path
        self.git("init", "-q", "-b", "main")
        self.commit("initial")
        self.git("branch", "homolog")

    def git(self, *args: str) -> str:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        out = subprocess.run(
            ["git", *args], cwd=self.path, env=env, check=True, capture_output=True, text=True
        )
        return out.stdout.strip()

    def commit(self, msg: str) -> str:
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.sha("HEAD")

    def sha(self, ref: str) -> str:
        return self.git("rev-parse", ref)

    def merge(self, branch: str, msg: str) -> None:
        self.git("merge", "-q", "--no-ff", "-m", msg, branch)

    def work_branch(self, name: str) -> None:
        self.git("checkout", "-q", "-b", name, "homolog")
        self.commit(f"work on {name}")

    def land_in_homolog(self, name: str) -> None:
        self.git("checkout", "-q", "homolog")
        self.merge(name, f"Merge {name}")

    def promote(self) -> None:
        self.git("checkout", "-q", "main")
        self.merge("homolog", "Merge homolog (promotion)")

    def check(self, head_ref: str, head: str | None = None) -> subprocess.CompletedProcess:
        head_sha = self.sha(head or head_ref)
        return subprocess.run(
            ["bash", str(SCRIPT), self.sha("homolog"), head_sha, head_ref, "main"],
            cwd=self.path,
            capture_output=True,
            text=True,
        )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    r = Repo(tmp_path)
    # History like the real one: work landed in homolog, then promoted to main.
    r.work_branch("feat/a")
    r.land_in_homolog("feat/a")
    r.promote()
    return r


def test_normal_work_branch_passes(repo: Repo):
    repo.work_branch("feat/b")
    res = repo.check("feat/b")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok:" in res.stdout


def test_work_branch_that_merged_main_fails(repo: Repo):
    repo.work_branch("chore/x")
    promotion = repo.sha("main")
    repo.merge("main", "Merge main into chore/x")
    res = repo.check("chore/x")
    assert res.returncode == 1
    assert "must never be merged into 'homolog'" in res.stdout
    assert f"commit from main: {promotion[:7]}" in res.stdout


def test_branch_based_on_main_fails(repo: Repo):
    repo.git("checkout", "-q", "-b", "fix/from-main", "main")
    repo.commit("fix cut from main by mistake")
    res = repo.check("fix/from-main")
    assert res.returncode == 1
    assert "commit from main:" in res.stdout


def test_main_commit_reached_through_another_branch_fails(repo: Repo):
    # chore/x merged main; feat/y merges chore/x: main still arrives indirectly.
    repo.git("checkout", "-q", "-b", "chore/x", "homolog")
    repo.merge("main", "Merge main into chore/x")
    repo.work_branch("feat/y")
    repo.merge("chore/x", "Merge chore/x")
    res = repo.check("feat/y")
    assert res.returncode == 1
    assert "commit from main:" in res.stdout


@pytest.mark.parametrize("head_ref", ["main", "promote/v0.1.0"])
def test_forbidden_head_names_fail(repo: Repo, head_ref: str):
    repo.git("branch", "promote/v0.1.0", "homolog")
    res = repo.check(head_ref)
    assert res.returncode == 1
    assert f"'{head_ref}' must never be merged into 'homolog'" in res.stdout


def test_main_already_contained_in_homolog_does_not_fail(repo: Repo):
    # After a one-time sync (as in PR #29) main's commits are in homolog, so a
    # work branch cut from homolog carries no commit that homolog lacks.
    repo.git("checkout", "-q", "homolog")
    repo.git("merge", "-q", "-s", "ours", "-m", "One-time sync", "main")
    repo.work_branch("feat/c")
    res = repo.check("feat/c")
    assert res.returncode == 0, res.stdout + res.stderr


def test_unknown_main_ref_fails(repo: Repo):
    repo.work_branch("feat/d")
    res = subprocess.run(
        ["bash", str(SCRIPT), repo.sha("homolog"), repo.sha("feat/d"), "feat/d", "nope"],
        cwd=repo.path,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "Cannot resolve 'nope'" in res.stdout
