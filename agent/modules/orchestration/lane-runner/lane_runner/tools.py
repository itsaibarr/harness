"""Tools offered to the model, with guardrails enforced here (not in the prompt)."""
import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

MAX_OUT = 20000
SCRUBBED_ENV = ("MIMO_API_KEY", "MIMO_BASE_URL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
                "SSH_AUTH_SOCK")
DENY = [(re.compile(p, re.I), name) for p, name in [
    (r"\bgit\s+(?:-C\s+\S+\s+)?push\b", "git push"),
    (r"\bgit\s+(?:-C\s+\S+\s+)?reset\s+.*--hard", "git reset --hard"),
    (r"\bgit\s+(?:-C\s+\S+\s+)?branch\s+.*-D\b", "git branch -D"),
    (r"\bgit\s+(?:-C\s+\S+\s+)?checkout\s+--(?:\s|$)", "git checkout --"),
    (r"\bgit\s+(?:-C\s+\S+\s+)?clean\s+.*-\w*f", "git clean -f"),
    (r"\bgit\s+(?:-C\s+\S+\s+)?commit\s+.*--amend", "git commit --amend"),
    (r"--force", "--force"),
    (r"\brm\s+-rf\s+/", "rm -rf /"),
    (r"\bsudo\b", "sudo"),
]]

SCHEMAS = [{"type": "function", "function": f} for f in [
    {"name": "read_file", "description": "Read a text file with line numbers.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "offset": {"type": "integer", "description": "1-based first line"},
         "limit": {"type": "integer", "description": "max lines (default 2000)"}}, "required": ["path"]}},
    {"name": "list_dir", "description": "List a directory (dirs end with /).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "grep", "description": "Regex search in files; returns path:line:text.",
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "write_file", "description": "Create or overwrite a file (only paths in the allowed scope).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace an exact string in a file. Fails if not found, or not unique unless replace_all.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
         "replace_all": {"type": "boolean"}}, "required": ["path", "old_string", "new_string"]}},
    {"name": "run", "description": "Run a shell command in the work dir. Returns exit code and combined output.",
     "parameters": {"type": "object", "properties": {
         "cmd": {"type": "string"}, "timeout_s": {"type": "integer", "description": "default 600, max 1800"}},
         "required": ["cmd"]}},
    {"name": "finish", "description": "End the task. report: files changed, tests run, pass count, deviations.",
     "parameters": {"type": "object", "properties": {"report": {"type": "string"}}, "required": ["report"]}},
]]


class ToolError(Exception):
    """Refused or failed call: returned to the model as an error result, never crashes the run."""


def truncate(text, limit=MAX_OUT):
    if len(text) <= limit:
        return text
    h = limit // 2
    return f"{text[:h]}\n...[{len(text) - limit} chars truncated]...\n{text[-h:]}"


def allowed(rel, patterns):
    """fnmatch on the repo-relative path; `**` matches across directories (fnmatch '*' already crosses '/')."""
    return any(fnmatch.fnmatchcase(rel, c) for p in patterns for c in {p, p.replace("**/", "")})


class Tools:
    def __init__(self, root, allow):
        self.root = Path(os.path.realpath(root))
        self.allow = list(allow)

    def resolve(self, path):
        """Real path of `path` (symlinks followed, even for not-yet-existing files); must be inside root."""
        if not isinstance(path, str) or not path:
            raise ToolError("path must be a non-empty string")
        real = Path(os.path.realpath(self.root / path))
        if real != self.root and self.root not in real.parents:
            raise ToolError(f"path escapes the work dir: {path}")
        return real

    def writable(self, path):
        real = self.resolve(path)
        rel = real.relative_to(self.root).as_posix()
        if ".git" in real.relative_to(self.root).parts or rel.startswith(".lane-runner"):
            raise ToolError(f"writes to {rel} are never allowed")
        if not allowed(rel, self.allow):
            raise ToolError(f"write refused: {rel} does not match any allowed glob {self.allow}")
        return real

    def call(self, name, args):
        fn = getattr(self, "t_" + name, None) if name in {s["function"]["name"] for s in SCHEMAS} else None
        if fn is None:
            raise ToolError(f"unknown tool: {name}")
        if not isinstance(args, dict):
            raise ToolError("arguments must be a JSON object")
        try:
            return fn(**args)
        except TypeError as e:
            raise ToolError(f"bad arguments for {name}: {e}")

    # -- read-only tools
    def t_read_file(self, path, offset=1, limit=2000):
        p = self.resolve(path)
        if not p.is_file():
            raise ToolError(f"not a file: {path}")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(int(offset or 1), 1)
        out = [f"{i:6}\t{l[:2000]}" for i, l in enumerate(lines[start - 1:start - 1 + int(limit or 2000)], start)]
        return truncate("\n".join(out) or "(empty or past end of file)", 50000), {}

    def t_list_dir(self, path):
        p = self.resolve(path)
        if not p.is_dir():
            raise ToolError(f"not a directory: {path}")
        names = sorted(f"{c.name}/" if c.is_dir() else c.name for c in p.iterdir())
        more = f"\n...[{len(names) - 500} more]" if len(names) > 500 else ""
        return "\n".join(names[:500]) + more or "(empty)", {}

    def t_grep(self, pattern, path=".", glob=None):
        base = self.resolve(path)
        rel = base.relative_to(self.root).as_posix()
        lines = self._rg(pattern, rel, glob) if shutil.which("rg") else self._pygrep(pattern, base, glob)
        out = "\n".join(lines[:200]) + (f"\n...[{len(lines) - 200} more matches]" if len(lines) > 200 else "")
        return truncate(out or "(no matches)"), {}

    def _rg(self, pattern, rel, glob):
        cmd = ["rg", "-n", "--no-heading", "--hidden", "-g", "!.git", "-g", "!.lane-runner"]
        cmd += ["-g", glob] if glob else []
        r = subprocess.run(cmd + ["-e", pattern, "--", rel], cwd=self.root, capture_output=True, text=True, timeout=60)
        if r.returncode > 1:
            raise ToolError(r.stderr.strip()[:500] or "rg failed")
        return r.stdout.splitlines()

    def _pygrep(self, pattern, base, glob):
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"bad regex: {e}")
        files = [base] if base.is_file() else self._walk(base)
        out = []
        for f in files:
            r = f.relative_to(self.root).as_posix()
            if glob and not (fnmatch.fnmatch(f.name, glob) or fnmatch.fnmatch(r, glob)):
                continue
            try:
                for i, l in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if rx.search(l):
                        out.append(f"{r}:{i}:{l[:300]}")
            except (OSError, UnicodeDecodeError):
                continue
            if len(out) > 500:
                break
        return out

    @staticmethod
    def _walk(base):
        for d, dirs, fs in os.walk(base):
            dirs[:] = [x for x in dirs if x not in (".git", ".lane-runner", "node_modules")]
            for f in fs:
                yield Path(d) / f

    # -- write tools
    def t_write_file(self, path, content):
        p = self.writable(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content.encode())} bytes to {p.relative_to(self.root)}", {}

    def t_edit_file(self, path, old_string, new_string, replace_all=False):
        p = self.writable(path)
        if not p.is_file():
            raise ToolError(f"not a file: {path}")
        text = p.read_text(encoding="utf-8")
        n = text.count(old_string) if old_string else 0
        if n == 0:
            raise ToolError("old_string not found (must match exactly, including whitespace)")
        if n > 1 and not replace_all:
            raise ToolError(f"old_string matches {n} places; add context to make it unique or pass replace_all=true")
        p.write_text(text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1),
                     encoding="utf-8")
        return f"replaced {n if replace_all else 1} occurrence(s) in {p.relative_to(self.root)}", {}

    # -- shell
    def t_run(self, cmd, timeout_s=600):
        for rx, name in DENY:
            if rx.search(cmd):
                raise ToolError(f"command refused by lane-runner guardrail ({name})")
        timeout = min(max(int(timeout_s or 600), 1), 1800)
        env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV}
        proc = subprocess.Popen(["bash", "-c", cmd], cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            out, code = f"{out}\n[killed: timeout after {timeout}s]", 124
        return f"exit_code: {code}\n{truncate(out)}", {"exit_code": code}


def parse_args(raw):
    try:
        v = json.loads(raw or "{}")
    except ValueError as e:
        raise ToolError(f"arguments are not valid JSON: {e}")
    return v
