"""Post-run scope audit: closes the shell-write gap by diffing the git work tree against the starting state."""
import subprocess

from .tools import allowed


def _git(root, *args):
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    return r.stdout if r.returncode == 0 else None


def _status_paths(root):
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    parts, paths, i = (out or "").split("\0"), set(), 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) > 3:
            paths.add(e[3:])
            if e[0] in "RC":  # rename/copy: next entry is the origin path
                paths.add(parts[i])
                i += 1
    return paths


def snapshot(root):
    """None if root is not a git work tree; else the starting HEAD and already-dirty paths."""
    if (_git(root, "rev-parse", "--is-inside-work-tree") or "").strip() != "true":
        return None
    head = (_git(root, "rev-parse", "--verify", "-q", "HEAD") or "").strip() or None
    return {"head": head, "dirty": _status_paths(root)}


def audit(root, start, allow, ignore=()):
    """Paths changed since `start` (uncommitted + committed during the run) that match no --allow glob."""
    changed = _status_paths(root) - start["dirty"]
    if start["head"]:
        out = _git(root, "diff", "--name-only", "--no-renames", "-z", f"{start['head']}..HEAD") or ""
        changed |= {p for p in out.split("\0") if p}
    changed = {p for p in changed if not p.startswith(".lane-runner/") and p not in ignore}
    return sorted(p for p in changed if not allowed(p, allow))
