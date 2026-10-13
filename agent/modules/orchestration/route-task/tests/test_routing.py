import tempfile
import unittest
from pathlib import Path

from route_task import config, routing, run, task
from tests.fakes import Agents, ScriptedAgent
from tests.gitrepo import make_repo
from tests.test_run import OK


def t(title="x", body="", tier="auto"):
    return task.parse(f"---\ntitle: {title}\ntier: {tier}\nprivacy: open\ngate:\n  - test -f out.txt\n---\n{body}")


class StaticRouteTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(None)

    def test_explicit_tier_wins_over_rules(self):
        self.assertEqual(routing.static(t("security review", tier="economy"), self.cfg)[0], "economy")
        self.assertEqual(routing.static(t("rename var", tier="premium"), self.cfg)[0], "premium")

    def test_default_rules_send_architecture_and_security_to_premium(self):
        tier, why = routing.static(t("Design the auth architecture"), self.cfg)
        self.assertEqual(tier, "premium")
        self.assertIn("architecture", why)

    def test_keyword_match_is_whole_word_and_case_insensitive(self):
        self.assertEqual(routing.static(t("Fix the AUTHOR field"), self.cfg)[0], None)
        self.assertEqual(routing.static(t("Security hardening"), self.cfg)[0], "premium")

    def test_body_text_is_searched_too(self):
        self.assertEqual(routing.static(t("tidy", body="this changes the data migration plan"), self.cfg)[0], "premium")

    def test_first_matching_rule_wins(self):
        self.cfg.routes = [{"keywords": ["css"], "tier": "economy"}, {"keywords": ["css", "security"], "tier": "premium"}]
        self.assertEqual(routing.static(t("css security"), self.cfg)[0], "economy")

    def test_no_match_returns_undecided(self):
        tier, why = routing.static(t("rename a variable"), self.cfg)
        self.assertIsNone(tier)
        self.assertIn("no rule", why)


class ClassifierTest(unittest.TestCase):
    def test_parse_finds_json_in_noisy_text(self):
        self.assertEqual(routing.parse_classification('Sure!\n{"tier": "premium", "reason": "hard"}\nthanks'), "premium")
        self.assertEqual(routing.parse_classification('{"tier":"economy"}'), "economy")

    def test_parse_rejects_garbage_and_unknown_tiers(self):
        self.assertIsNone(routing.parse_classification("no json here"))
        self.assertIsNone(routing.parse_classification('{"tier": "gold"}'))
        self.assertIsNone(routing.parse_classification('{"tier": '))

    def test_prompt_contains_the_task_and_demands_json(self):
        p = routing.classify_prompt(t("Refactor parser", body="make it faster"))
        self.assertIn("Refactor parser", p)
        self.assertIn("make it faster", p)
        self.assertIn('"tier"', p)


class RouteConfigTest(unittest.TestCase):
    def test_bad_rule_is_a_config_error(self):
        for bad in ('[[routes]]\nkeywords = ["a"]\ntier = "gold"\n', '[[routes]]\nkeywords = ["a"]\ntier = "premium"\nwat = 1\n',
                    '[[routes]]\ntier = "premium"\n'):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d, "c.toml")
                p.write_text(bad)
                with self.assertRaises(config.ConfigError):
                    config.load(p)

    def test_user_routes_replace_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "c.toml")
            p.write_text('[[routes]]\nkeywords = ["css"]\ntier = "economy"\n')
            c = config.load(p)
        self.assertEqual(c.routes, [{"keywords": ["css"], "tier": "economy"}])

    def test_classifier_is_off_by_default(self):
        self.assertFalse(config.load(None).routing.classifier)


class RunRoutingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.cfg = config.load(None)
        self.cfg.state_dir = self.root / "state"
        self.cfg.economy.trains_on_data = False
        self.cfg.evaluator.enabled = False
        self.cfg.quota.enabled = False

    def tearDown(self):
        self._tmp.cleanup()

    def go(self, tk, oc, cl):
        return run.run_task(tk, self.cfg, Agents(oc, cl), repo=self.repo)

    def test_auto_with_keyword_skips_the_worker_and_records_the_route(self):
        oc, cl = ScriptedAgent(), ScriptedAgent(OK)
        r = self.go(t("Architecture decision for the queue"), oc, cl)
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(oc.calls), 0)
        self.assertEqual(r.state["tier"], "premium")
        self.assertIn("architecture", r.state["route_reason"])

    def test_auto_without_match_defaults_to_economy_worker(self):
        oc, cl = ScriptedAgent(OK), ScriptedAgent()
        r = self.go(t("rename a variable"), oc, cl)
        self.assertEqual(r.state["tier"], "economy")
        self.assertEqual(len(oc.calls), 1)
        self.assertEqual(len(cl.calls), 0)

    def test_classifier_decides_when_enabled_and_uses_the_economy_model(self):
        self.cfg.routing.classifier = True
        oc = ScriptedAgent({"text": '{"tier": "premium", "reason": "tricky"}'})
        cl = ScriptedAgent(OK)
        r = self.go(t("rename a variable"), oc, cl)
        self.assertEqual(r.state["tier"], "premium")
        self.assertEqual(len(oc.calls), 1)  # only the classification call
        self.assertEqual(oc.calls[0]["model"], self.cfg.economy.model)
        self.assertEqual(r.state["route_reason"], "classifier")
        self.assertEqual(len(cl.calls), 1)

    def test_garbage_classification_falls_back_to_economy(self):
        self.cfg.routing.classifier = True
        oc = ScriptedAgent({"text": "I cannot decide"}, OK)
        r = self.go(t("rename a variable"), oc, ScriptedAgent())
        self.assertEqual(r.state["tier"], "economy")
        self.assertEqual(r.state["status"], "done")

    def test_classifier_failure_falls_back_to_economy(self):
        self.cfg.routing.classifier = True
        oc = ScriptedAgent({"ok": False, "error": "down"}, OK)
        r = self.go(t("rename a variable"), oc, ScriptedAgent())
        self.assertEqual(r.state["tier"], "economy")

    def test_classifier_is_not_called_for_explicit_tier(self):
        self.cfg.routing.classifier = True
        oc = ScriptedAgent(OK)
        self.go(t("x", tier="economy"), oc, ScriptedAgent())
        self.assertEqual(len(oc.calls), 1)  # the worker, not a classification


if __name__ == "__main__":
    unittest.main()
