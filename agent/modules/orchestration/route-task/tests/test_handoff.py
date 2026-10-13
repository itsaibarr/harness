import tempfile
import unittest
from pathlib import Path

from route_task import handoff, task


def sample_task():
    return task.parse("---\ntitle: Add retry\nacceptance:\n  - retries 3x\n  - tests pass\n---\nDo the thing carefully.")


class HandoffTest(unittest.TestCase):
    def test_render_contains_task_acceptance_attempts_gate_issues_and_next(self):
        attempts = [
            {"stage": "worker", "agent": "opencode", "model": "m1", "duration_s": 12.3, "outcome": "gate failed"},
            {"stage": "eval", "agent": "claude", "model": "sonnet", "duration_s": 5.0, "outcome": "fail"},
        ]
        text = handoff.render(
            sample_task(), attempts, diff_stat=" a.py | 2 +-\n 1 file changed",
            gate_output="FAIL  python3 -m unittest\nAssertionError: x", issues=[{"severity": "high", "file": "a.py", "note": "no backoff"}],
            files=["a.py"], next_step="Fix the failing test; keep the diff small.")
        for needle in ("Add retry", "Do the thing carefully.", "retries 3x", "opencode", "sonnet", "gate failed",
                       "a.py | 2", "AssertionError", "high", "no backoff", "Fix the failing test"):
            self.assertIn(needle, text)

    def test_gate_output_is_capped_to_last_60_lines(self):
        out = "\n".join(f"line{i}" for i in range(200))
        text = handoff.render(sample_task(), [], "", out, [], [], "n")
        self.assertIn("line199", text)
        self.assertIn("line140", text)
        self.assertNotIn("line139", text)

    def test_write_creates_dot_route_task_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = handoff.write(Path(d), "hello")
            self.assertEqual(p, Path(d) / ".route-task" / "handoff.md")
            self.assertEqual(p.read_text(), "hello")

    def test_empty_sections_render_placeholders_not_crashes(self):
        text = handoff.render(sample_task(), [], "", "", [], [], "go")
        self.assertIn("(none yet)", text)


if __name__ == "__main__":
    unittest.main()
