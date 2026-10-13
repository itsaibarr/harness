"""Safety invariants for unattended runs. These are regression guards for the guarantees the README makes."""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from route_task import agents, config, guard, report

PKG = Path(__file__).parent.parent / "route_task"
STUBS = str(Path(__file__).parent / "stubs")


class SourceInvariantsTest(unittest.TestCase):
    def sources(self):
        return {p.name: p.read_text() for p in PKG.glob("*.py")}

    def test_orchestrator_never_reads_credential_stores(self):
        for name, src in self.sources().items():
            low = src.lower()
            for token in ("auth.json", ".credentials", "keychain", "find-generic-password", "oauth"):
                self.assertNotIn(token, low, f"{name} mentions {token!r}")

    def test_orchestrator_never_invokes_git_push_or_merge(self):
        for name, src in self.sources().items():
            self.assertIsNone(re.search(r'"(push|merge)"', src), f"{name} passes push/merge to git")

    def test_claude_is_never_invoked_with_bare(self):
        for name, src in self.sources().items():
            self.assertNotIn('"--bare"', src, name)


class ChildStarvationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cwd = self.root / "wt"
        self.cwd.mkdir()
        self.cfg = config.load(None)
        self.cfg.state_dir = self.root / "state"
        self.log = self.root / "log"
        self.extra = {"PATH": f"{STUBS}:{os.environ['PATH']}", "STUB_LOG": str(self.log)}

    def tearDown(self):
        self._tmp.cleanup()

    def logged_env(self):
        return json.loads(self.log.read_text().splitlines()[-1])["env"]

    def check_starved(self, env):
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(env["GIT_SSH_COMMAND"], "/usr/bin/false")
        self.assertEqual(os.listdir(env["GH_CONFIG_DIR"]), [])
        for k in guard.FORBIDDEN:
            self.assertNotIn(k, env)

    def test_opencode_child_is_starved_even_when_the_parent_has_credentials(self):
        os.environ.update({"GH_TOKEN": "t", "SSH_AUTH_SOCK": "/s", "ANTHROPIC_API_KEY": "k"})
        try:
            agents.OpenCodeAgent(guard.child_env(self.cfg, extra=self.extra)).run("p", self.cwd, model="m", timeout=20)
        finally:
            for k in ("GH_TOKEN", "SSH_AUTH_SOCK", "ANTHROPIC_API_KEY"):
                os.environ.pop(k)
        self.check_starved(self.logged_env())

    def test_claude_child_is_starved_in_full_and_readonly_modes(self):
        os.environ.update({"GITHUB_TOKEN": "t", "ANTHROPIC_API_KEY": "k"})
        try:
            a = agents.ClaudeAgent(guard.child_env(self.cfg, extra=self.extra))
            for mode in ("full", "readonly"):
                a.run("p", self.cwd, model="sonnet", mode=mode, timeout=20)
                self.check_starved(self.logged_env())
        finally:
            for k in ("GITHUB_TOKEN", "ANTHROPIC_API_KEY"):
                os.environ.pop(k)

    def test_gate_commands_run_in_the_starved_env_too(self):
        from route_task import gate
        env = guard.child_env(self.cfg)
        r = gate.run(['echo "$GIT_TERMINAL_PROMPT $GIT_CONFIG_GLOBAL"'], self.cwd, env, timeout=10)
        self.assertIn("0 /dev/null", r.checks[0].output)


class ReportFromDiskTest(unittest.TestCase):
    def test_report_is_a_pure_function_of_state_json(self):
        state = {"run_id": "r", "status": "needs_human", "task": {"title": "T", "gate": ["true"]}, "branch": "rt/r",
                 "worktree": "/w", "error": "evaluator unavailable: x",
                 "history": [{"stage": "worker", "agent": "opencode", "model": "m", "duration_s": 1.0, "outcome": "gate failed", "gate": "FAIL  true"},
                             {"stage": "premium", "agent": "claude", "model": "opus", "duration_s": 2.0, "outcome": "gate failed", "gate": "FAIL  true"}]}
        text = report.render(state)
        for needle in ("opencode / m", "claude / opus", "Premium used: yes", "needs a human", "evaluator unavailable", "rt/r"):
            self.assertIn(needle, text)
        self.assertEqual(text, report.render(json.loads(json.dumps(state))))


if __name__ == "__main__":
    unittest.main()
