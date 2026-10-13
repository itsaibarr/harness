import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from route_task import agents, config, guard

STUBS = str(Path(__file__).parent / "stubs")


class AgentTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cwd = self.root / "wt"
        self.cwd.mkdir()
        self.log = self.root / "stub.log"
        self.cfg = config.load(None)
        self.cfg.state_dir = self.root / "state"
        self.extra = {"PATH": f"{STUBS}:{os.environ['PATH']}", "STUB_LOG": str(self.log)}

    def tearDown(self):
        self._tmp.cleanup()

    def env(self, **more):
        return guard.child_env(self.cfg, extra={**self.extra, **more})

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()]


class OpenCodeAgentTest(AgentTestBase):
    def agent(self, **more):
        return agents.OpenCodeAgent(env=self.env(**more))

    def test_argv_model_dir_format_and_auto(self):
        r = self.agent().run("do it", self.cwd, model="opencode/x", timeout=20)
        self.assertTrue(r.ok, r)
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[0], "run")
        self.assertEqual(argv[argv.index("-m") + 1], "opencode/x")
        self.assertEqual(argv[argv.index("--format") + 1], "json")
        self.assertEqual(argv[argv.index("--dir") + 1], str(self.cwd))
        self.assertIn("--auto", argv)
        self.assertEqual(argv[-1], "do it")
        self.assertEqual(Path(self.calls()[0]["cwd"]).resolve(), self.cwd.resolve())

    def test_parses_text_session_and_tokens(self):
        r = self.agent(STUB_TEXT="hello world").run("p", self.cwd, model="m", timeout=20)
        self.assertEqual(r.text, "hello world")
        self.assertEqual(r.session_id, "ses_stub1")
        self.assertEqual(r.tokens, 110)

    def test_resume_session_flag(self):
        self.agent().run("p", self.cwd, model="m", session="ses_old", timeout=20)
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("-s") + 1], "ses_old")

    def test_child_env_is_scrubbed_and_denies_push(self):
        os.environ["ANTHROPIC_API_KEY"] = "leak"
        try:
            self.agent().run("p", self.cwd, model="m", timeout=20)
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
        env = self.calls()[0]["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(json.loads(env["OPENCODE_PERMISSION"])["bash"]["git push*"], "deny")

    def test_nonzero_exit_is_failure_with_stderr(self):
        r = self.agent(STUB_OC="fail").run("p", self.cwd, model="m", timeout=20)
        self.assertFalse(r.ok)
        self.assertIn("provider unavailable", r.error)

    def test_error_event_is_failure(self):
        r = self.agent(STUB_OC="error_event").run("p", self.cwd, model="m", timeout=20)
        self.assertFalse(r.ok)
        self.assertIn("rate limited", r.error)

    def test_timeout_kills_process_group(self):
        t0 = time.time()
        r = self.agent(STUB_OC="hang").run("p", self.cwd, model="m", timeout=1)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)
        self.assertLess(time.time() - t0, 15)


class ClaudeAgentTest(AgentTestBase):
    def agent(self, **more):
        return agents.ClaudeAgent(env=self.env(**more))

    def test_full_mode_argv(self):
        r = self.agent().run("do it", self.cwd, model="opus", mode="full", timeout=20)
        self.assertTrue(r.ok, r)
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[:2], ["-p", "do it"])
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", argv)
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertIn("Bash(git push*)", argv[argv.index("--disallowedTools") + 1].split(","))
        self.assertNotIn("--bare", argv)

    def test_readonly_mode_has_no_bypass_and_only_read_tools(self):
        self.agent().run("judge", self.cwd, model="sonnet", mode="readonly", timeout=20)
        argv = self.calls()[0]["argv"]
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        allowed = argv[argv.index("--allowedTools") + 1].split(",")
        self.assertIn("Read", allowed)
        self.assertIn("Bash(git diff*)", allowed)
        self.assertNotIn("Edit", allowed)
        self.assertNotIn("Write", allowed)

    def test_schema_and_structured_output(self):
        schema = {"type": "object", "properties": {"pass": {"type": "boolean"}}, "required": ["pass"]}
        r = self.agent(STUB_STRUCT='{"pass": true}').run("j", self.cwd, model="sonnet", mode="readonly", schema=schema, timeout=20)
        argv = self.calls()[0]["argv"]
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), schema)
        self.assertEqual(r.structured, {"pass": True})

    def test_parses_session_cost_and_rate_limit(self):
        r = self.agent(STUB_5H="0.42", STUB_7D="0.17").run("p", self.cwd, model="opus", timeout=20)
        self.assertEqual(r.session_id, "claude-sess-1")
        self.assertEqual(r.cost_usd, 0.01)
        self.assertEqual(r.rate_limit["five_hour"]["utilization"], 0.42)
        self.assertEqual(r.rate_limit["seven_day"]["utilization"], 0.17)

    def test_resume_flag(self):
        self.agent().run("p", self.cwd, model="opus", session="abc", timeout=20)
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--resume") + 1], "abc")

    def test_api_key_never_reaches_claude(self):
        os.environ["ANTHROPIC_API_KEY"] = "leak"
        try:
            self.agent().run("p", self.cwd, model="opus", timeout=20)
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
        self.assertNotIn("ANTHROPIC_API_KEY", self.calls()[0]["env"])

    def test_error_result_is_failure(self):
        r = self.agent(STUB_CL="error").run("p", self.cwd, model="opus", timeout=20)
        self.assertFalse(r.ok)
        self.assertIn("api error", r.error)
        self.assertIsNotNone(r.rate_limit)  # still learned quota from the failed call

    def test_timeout(self):
        r = self.agent(STUB_CL="hang").run("p", self.cwd, model="opus", timeout=1)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)


class RunnerStopTest(AgentTestBase):
    def test_stop_callback_kills_running_child(self):
        t0 = time.time()
        r = agents.OpenCodeAgent(env=self.env(STUB_OC="hang")).run(
            "p", self.cwd, model="m", timeout=60, should_stop=lambda: time.time() - t0 > 1)
        self.assertTrue(r.stopped)
        self.assertFalse(r.ok)
        self.assertLess(time.time() - t0, 15)


if __name__ == "__main__":
    unittest.main()
