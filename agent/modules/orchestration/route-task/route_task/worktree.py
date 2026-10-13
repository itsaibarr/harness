"""One git worktree + branch rt/<run_id> per task. The user's main checkout is never written to."""
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

_CREATE_LOCK = threading.Lock()  # concurrent `git worktree add` on one repo contends for git's config lock
IDENT = ["-c", "user.name=route-task", "-c", "user.email=route-task@localhost"]


class WorktreeError(RuntimeError):
    pass


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", *IDENT, *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise WorktreeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r


@dataclass
class Worktree:
    path: Path
    branch: str
    base_sha: str

    def checkpoint(self, msg):
        """Commit everything (no hooks); return the new sha, or None if nothing changed."""
        _git(self.path, "add", "-A")
        if _git(self.path, "diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        _git(self.path, "commit", "-q", "--no-verify", "-m", msg)
        return _git(self.path, "rev-parse", "HEAD").stdout.strip()

    def diff(self):
        return _git(self.path, "diff", self.base_sha, "HEAD").stdout

    def diff_stat(self):
        return _git(self.path, "diff", "--stat", self.base_sha, "HEAD").stdout

    def files(self):
        return _git(self.path, "diff", "--name-only", self.base_sha, "HEAD").stdout.split()


def create(repo, run_id, wt_root):
    repo = Path(repo)
    if _git(repo, "rev-parse", "--show-toplevel", check=False).returncode:
        raise WorktreeError(f"not a git repository: {repo}")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    path = Path(wt_root) / run_id
    branch = f"rt/{run_id}"
    with _CREATE_LOCK:
        _git(repo, "worktree", "add", "-q", "-b", branch, str(path), "HEAD")
    exclude = _git(path, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    exclude = Path(exclude) if Path(exclude).is_absolute() else path / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    have = exclude.read_text() if exclude.exists() else ""
    if ".route-task/" not in have.splitlines():
        exclude.write_text(have + ("" if have.endswith("\n") or not have else "\n") + ".route-task/\n")
    return Worktree(path, branch, base)


def open_(run_id, wt_root, base_sha):
    path = Path(wt_root) / run_id
    if not path.is_dir():
        raise WorktreeError(f"worktree missing: {path}")
    return Worktree(path, f"rt/{run_id}", base_sha)
