import tempfile
import unittest
from pathlib import Path

from route_task import gate


class GateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_commands_passes_trivially_and_is_marked_empty(self):
        r = gate.run([], self.cwd, {}, timeout=5)
        self.assertTrue(r.ok)
        self.assertEqual(r.checks, [])

    def test_all_pass(self):
        r = gate.run(["true", "echo hi"], self.cwd, {}, timeout=5)
        self.assertTrue(r.ok)
        self.assertEqual([c.ok for c in r.checks], [True, True])
        self.assertIn("hi", r.checks[1].output)

    def test_failure_is_reported_with_exit_code_and_output_tail(self):
        r = gate.run(["echo a", "echo oops >&2; exit 3", "echo never"], self.cwd, {}, timeout=5)
        self.assertFalse(r.ok)
        self.assertEqual(len(r.checks), 2)  # stops at first failure
        self.assertEqual(r.checks[1].returncode, 3)
        self.assertIn("oops", r.checks[1].output)

    def test_runs_in_cwd_with_given_env(self):
        r = gate.run(["pwd; echo $RT_X"], self.cwd, {"PATH": "/usr/bin:/bin", "RT_X": "v1"}, timeout=5)
        out = r.checks[0].output
        self.assertIn(str(self.cwd.resolve()), out)
        self.assertIn("v1", out)

    def test_timeout_counts_as_failure(self):
        r = gate.run(["sleep 30"], self.cwd, {"PATH": "/usr/bin:/bin"}, timeout=1)
        self.assertFalse(r.ok)
        self.assertTrue(r.checks[0].timed_out)

    def test_output_is_truncated_to_tail(self):
        r = gate.run(["seq 1 5000"], self.cwd, {"PATH": "/usr/bin:/bin"}, timeout=5, tail_lines=10)
        lines = r.checks[0].output.splitlines()
        self.assertEqual(len(lines), 10)
        self.assertEqual(lines[-1], "5000")

    def test_summary_text_lists_each_check(self):
        r = gate.run(["true", "exit 1"], self.cwd, {}, timeout=5)
        text = r.summary()
        self.assertIn("PASS", text)
        self.assertIn("FAIL", text)
        self.assertIn("exit 1", text)


if __name__ == "__main__":
    unittest.main()
