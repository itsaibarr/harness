import tempfile
import unittest
from pathlib import Path

from route_task import config


class ConfigTest(unittest.TestCase):
    def test_defaults_match_spec(self):
        c = config.load(None)
        self.assertEqual(c.economy.model, "opencode/muse-spark-1.3-contributor-free")
        self.assertTrue(c.economy.trains_on_data)
        self.assertEqual(c.evaluator.model, "sonnet")
        self.assertEqual(c.premium.model, "opus")
        self.assertEqual(c.limits.worker_attempts, 2)
        self.assertEqual(c.limits.premium_per_task, 1)
        self.assertEqual(c.quota.evaluator_5h, 0.75)
        self.assertEqual(c.quota.premium_7d, 0.80)
        self.assertEqual(c.quota.pacing_slack, 0.15)

    def test_override_merges_over_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "c.toml")
            p.write_text('[economy]\nmodel = "fireworks-ai/x"\ntrains_on_data = false\n')
            c = config.load(p)
        self.assertEqual(c.economy.model, "fireworks-ai/x")
        self.assertFalse(c.economy.trains_on_data)
        self.assertEqual(c.evaluator.model, "sonnet")  # untouched section keeps defaults

    def test_unknown_key_is_an_error_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "c.toml")
            p.write_text("[quota]\nevaluator_5hh = 0.5\n")
            with self.assertRaises(config.ConfigError):
                config.load(p)

    def test_example_file_loads_and_equals_defaults(self):
        ex = Path(__file__).parent.parent / "route-task.example.toml"
        self.assertEqual(vars(config.load(ex).quota), vars(config.load(None).quota))

    def test_state_dir_expands_home(self):
        c = config.load(None)
        self.assertTrue(str(c.state_dir).startswith("/"))
        self.assertTrue(str(c.state_dir).endswith(".route-task"))


if __name__ == "__main__":
    unittest.main()
