"""Test helper: throwaway git repos with a fake bare remote."""
import subprocess
from pathlib import Path

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def git(cwd, *args):
    import os
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, **GIT_ENV})
    if r.returncode:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r.stdout.strip()


def make_repo(root: Path, name="repo", with_remote=False) -> Path:
    repo = root / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "init")
    if with_remote:
        remote = root / f"{name}-remote.git"
        git(root, "init", "-q", "--bare", str(remote))
        git(repo, "remote", "add", "origin", str(remote))
    return repo
