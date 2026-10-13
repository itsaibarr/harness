import tempfile
import unittest
from pathlib import Path

from route_task import config, events, run, task
from tests.fakes import Agents, ScriptedAgent
from tests.gitrepo import git, make_repo

OK = {"writes": {"out.txt": "good\n"}}
BAD = {"writes": {"out.txt": "bad\n"}}


def make_task(tier="economy", gate=("grep -q good out.txt",), privacy="open"):
    g = "".join(f"  - {c}\n" for c in gate)
    return task.parse(f"---\ntitle: Make out.txt good\ntier: {tier}\nprivacy: {privacy}\ngate:\n{g}acceptance:\n  - out.txt says good\n---\nWrite good into out.txt.")


class RunBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root, with_remote=True)
        self.cfg = config.load(None)
        self.cfg.state_dir = self.root / "state"
        self.cfg.economy.trains_on_data = False  # privacy guard is covered in its own tests

    def tearDown(self):
        self._tmp.cleanup()

    def go(self, t, agents, **kw):
        return run.run_task(t, self.cfg, agents, repo=self.repo, **kw)

    def remote_refs(self):
        return git(self.root, "ls-remote", str(self.root / "repo-remote.git"))


class WorkerPathTest(RunBase):
    def test_gate_passes_first_try_no_premium(self):
        oc, cl = ScriptedAgent(OK), ScriptedAgent()
        r = self.go(make_task(), Agents(oc, cl))
        s = r.state
        self.assertEqual(s["status"], "done")
        self.assertEqual(len(oc.calls), 1)
        self.assertEqual(len(cl.calls), 0)
        self.assertEqual(oc.calls[0]["model"], self.cfg.economy.model)
        self.assertEqual(git(Path(s["worktree"]), "show", "HEAD:out.txt"), "good")
        self.assertIn("Premium used: no", (r.path / "report.md").read_text())

    def test_retry_with_gate_feedback_then_pass(self):
        oc = ScriptedAgent(BAD, OK)
        r = self.go(make_task(), Agents(oc, ScriptedAgent()))
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(oc.calls), 2)
        handoff = (Path(r.state["worktree"]) / ".route-task" / "handoff.md").read_text()
        self.assertIn("grep -q good out.txt", handoff)  # second attempt saw the failing gate
        self.assertIn("gate failed", handoff)
        self.assertEqual(r.state["attempts"]["worker"], 2)

    def test_agent_error_counts_as_an_attempt(self):
        oc = ScriptedAgent({"ok": False, "error": "provider down"}, OK)
        r = self.go(make_task(), Agents(oc, ScriptedAgent()))
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(oc.calls), 2)
        self.assertIn("provider down", str(r.state["history"][0]["outcome"]))

    def test_every_run_has_prompt_pointing_at_handoff(self):
        oc = ScriptedAgent(OK)
        self.go(make_task(), Agents(oc, ScriptedAgent()))
        self.assertIn(".route-task/handoff.md", oc.calls[0]["prompt"])


class EscalationTest(RunBase):
    def test_two_worker_failures_trigger_exactly_one_premium_that_fixes(self):
        oc, cl = ScriptedAgent(BAD, BAD), ScriptedAgent(OK)
        r = self.go(make_task(), Agents(oc, cl))
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(oc.calls), 2)
        self.assertEqual(len(cl.calls), 1)
        c = cl.calls[0]
        self.assertEqual(c["model"], "opus")
        self.assertEqual(c["mode"], "full")
        self.assertIn(".route-task/handoff.md", c["prompt"])
        self.assertIn("Premium used: yes", (r.path / "report.md").read_text())

    def test_premium_failure_ends_needs_human_without_second_premium(self):
        oc, cl = ScriptedAgent(BAD, BAD), ScriptedAgent(BAD, OK)
        r = self.go(make_task(), Agents(oc, cl))
        self.assertEqual(r.state["status"], "needs_human")
        self.assertEqual(len(cl.calls), 1)
        self.assertIn("needs a human", (r.path / "report.md").read_text().lower())

    def test_premium_tier_skips_the_worker(self):
        oc, cl = ScriptedAgent(), ScriptedAgent(OK)
        r = self.go(make_task(tier="premium"), Agents(oc, cl))
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(oc.calls), 0)
        self.assertEqual(len(cl.calls), 1)

    def test_premium_cap_zero_means_never_escalate(self):
        self.cfg.limits.premium_per_task = 0
        cl = ScriptedAgent(OK)
        r = self.go(make_task(), Agents(ScriptedAgent(BAD, BAD), cl))
        self.assertEqual(r.state["status"], "needs_human")
        self.assertEqual(len(cl.calls), 0)


class SafetyAndStateTest(RunBase):
    def test_main_checkout_untouched_and_nothing_pushed(self):
        (self.repo / "sentinel.txt").write_text("keep")
        before = git(self.repo, "status", "--porcelain")
        self.go(make_task(), Agents(ScriptedAgent(OK), ScriptedAgent()))
        self.assertEqual(git(self.repo, "status", "--porcelain"), before)
        self.assertFalse((self.repo / "out.txt").exists())
        self.assertEqual(self.remote_refs(), "")  # no push, no new branch on the remote

    def test_stop_request_ends_run_as_stopped_before_next_stage(self):
        oc = ScriptedAgent(BAD, OK)
        calls = {"n": 0}

        def stop():
            calls["n"] += 1
            return len(oc.calls) >= 1

        r = self.go(make_task(), Agents(oc, ScriptedAgent()), should_stop=stop)
        self.assertEqual(r.state["status"], "stopped")
        self.assertEqual(len(oc.calls), 1)

    def test_events_log_every_stage(self):
        r = self.go(make_task(), Agents(ScriptedAgent(OK), ScriptedAgent()))
        types = [l.split('"type": "')[1].split('"')[0] for l in (r.path / "events.jsonl").read_text().splitlines()]
        self.assertEqual(types[0], "run_created")
        self.assertIn("stage_start", types)
        self.assertIn("stage_end", types)
        self.assertEqual(types[-1], "run_finished")

    def test_no_gate_commands_is_marked_unverified_in_report(self):
        r = self.go(make_task(gate=()), Agents(ScriptedAgent(OK), ScriptedAgent()))
        self.assertEqual(r.state["status"], "done")
        self.assertIn("no gate", (r.path / "report.md").read_text().lower())

    def test_not_a_git_repo_fails_cleanly(self):
        plain = self.root / "plain"
        plain.mkdir()
        r = run.run_task(make_task(), self.cfg, Agents(), repo=plain)
        self.assertEqual(r.state["status"], "failed")


if __name__ == "__main__":
    unittest.main()
