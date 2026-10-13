import tempfile
import unittest
from pathlib import Path

from route_task import config, guard, run, task
from tests.fakes import Agents, ScriptedAgent
from tests.gitrepo import make_repo
from tests.test_run import OK, make_task


def cfg(trains=True, private_model=""):
    c = config.load(None)
    c.economy.trains_on_data = trains
    c.economy.private_model = private_model
    return c


def t(privacy="private", tier="economy"):
    return task.parse(f"---\ntitle: x\nprivacy: {privacy}\ntier: {tier}\n---\n")


class EconomyModelTest(unittest.TestCase):
    def test_non_training_model_is_always_fine(self):
        self.assertEqual(guard.economy_model(cfg(trains=False), t("private"), lambda: False), cfg().economy.model)

    def test_open_task_may_use_training_model(self):
        self.assertEqual(guard.economy_model(cfg(), t("open"), lambda: False), cfg().economy.model)

    def test_private_task_uses_private_model_when_configured(self):
        self.assertEqual(guard.economy_model(cfg(private_model="fw/x"), t("private"), lambda: False), "fw/x")

    def test_private_task_on_public_repo_may_use_training_model(self):
        self.assertEqual(guard.economy_model(cfg(), t("private"), lambda: True), cfg().economy.model)

    def test_private_task_on_private_repo_is_refused_with_the_override_named(self):
        with self.assertRaises(guard.PrivacyRefused) as cm:
            guard.economy_model(cfg(), t("private"), lambda: False)
        msg = str(cm.exception)
        self.assertIn("privacy: open", msg)
        self.assertIn("economy.private_model", msg)

    def test_public_check_is_not_called_unless_needed(self):
        def boom():
            raise AssertionError("must not be called")
        guard.economy_model(cfg(trains=False), t("private"), boom)
        guard.economy_model(cfg(), t("open"), boom)
        guard.economy_model(cfg(private_model="m"), t("private"), boom)

    def test_repo_is_public_fails_closed_outside_a_github_repo(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(guard.repo_is_public(Path(d)))


class PublicCheckCacheTest(unittest.TestCase):
    def test_one_gh_call_per_repo_not_per_task(self):
        from unittest import mock
        guard.repo_is_public.cache_clear()
        fake = mock.Mock(returncode=0, stdout="true\n")
        with tempfile.TemporaryDirectory() as d, mock.patch("route_task.guard.subprocess.run", return_value=fake) as m:
            self.assertTrue(guard.repo_is_public(Path(d)))
            self.assertTrue(guard.repo_is_public(Path(d)))
        self.assertEqual(m.call_count, 1)
        guard.repo_is_public.cache_clear()


class RunPrivacyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.cfg = cfg()
        self.cfg.state_dir = self.root / "state"
        self.cfg.evaluator.enabled = False
        self.cfg.quota.enabled = False

    def tearDown(self):
        self._tmp.cleanup()

    def test_refused_private_task_never_calls_the_training_model_and_is_logged(self):
        oc = ScriptedAgent(OK)
        r = run.run_task(make_task(privacy="private"), self.cfg, Agents(oc, ScriptedAgent()), repo=self.repo, is_public=lambda: False)
        self.assertEqual(r.state["status"], "failed")
        self.assertEqual(len(oc.calls), 0)
        self.assertIn("privacy", r.state["error"].lower())
        self.assertIn("privacy_guard", (r.path / "events.jsonl").read_text())
        self.assertFalse((self.root / "state" / "worktrees").exists())  # refused before any worktree

    def test_private_model_is_used_for_private_task(self):
        self.cfg.economy.private_model = "fw/zero-retention"
        oc = ScriptedAgent(OK)
        r = run.run_task(make_task(privacy="private"), self.cfg, Agents(oc, ScriptedAgent()), repo=self.repo, is_public=lambda: False)
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(oc.calls[0]["model"], "fw/zero-retention")

    def test_open_task_uses_the_configured_free_model(self):
        oc = ScriptedAgent(OK)
        run.run_task(make_task(privacy="open"), self.cfg, Agents(oc, ScriptedAgent()), repo=self.repo, is_public=lambda: False)
        self.assertEqual(oc.calls[0]["model"], self.cfg.economy.model)

    def test_premium_tier_is_not_subject_to_the_economy_privacy_guard(self):
        cl = ScriptedAgent(OK)
        r = run.run_task(make_task(tier="premium", privacy="private"), self.cfg, Agents(ScriptedAgent(), cl), repo=self.repo, is_public=lambda: False)
        self.assertEqual(r.state["status"], "done")

    def test_premium_escalation_from_private_task_is_allowed(self):
        self.cfg.economy.private_model = "fw/x"
        oc, cl = ScriptedAgent(), ScriptedAgent(OK)
        r = run.run_task(make_task(privacy="private"), self.cfg, Agents(oc, cl), repo=self.repo, is_public=lambda: False)
        self.assertEqual(r.state["status"], "done")  # worker wrote nothing x2, premium fixed it


if __name__ == "__main__":
    unittest.main()
