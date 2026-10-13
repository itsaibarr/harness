import unittest

from route_task import task

FULL = """---
title: Add retry to fetch client
repo: /tmp/some/repo   # trailing comment
tier: economy
privacy: open
gate:
  - python3 -m unittest discover -s tests
  - ruff check .
acceptance:
  - fetch() retries 3x on 5xx
  - existing tests pass
---
Free-form description.
Second line.
"""


class TaskParseTest(unittest.TestCase):
    def test_parses_frontmatter_and_body(self):
        t = task.parse(FULL)
        self.assertEqual(t.title, "Add retry to fetch client")
        self.assertEqual(t.repo, "/tmp/some/repo")
        self.assertEqual(t.tier, "economy")
        self.assertEqual(t.privacy, "open")
        self.assertEqual(t.gate, ["python3 -m unittest discover -s tests", "ruff check ."])
        self.assertEqual(t.acceptance, ["fetch() retries 3x on 5xx", "existing tests pass"])
        self.assertEqual(t.body, "Free-form description.\nSecond line.")

    def test_defaults(self):
        t = task.parse("---\ntitle: x\n---\nbody")
        self.assertEqual(t.tier, "economy")
        self.assertEqual(t.privacy, "private")
        self.assertEqual(t.gate, [])
        self.assertEqual(t.acceptance, [])
        self.assertIsNone(t.repo)

    def test_missing_title_is_error(self):
        with self.assertRaises(task.TaskError):
            task.parse("---\ntier: economy\n---\nbody")

    def test_no_frontmatter_is_error(self):
        with self.assertRaises(task.TaskError):
            task.parse("just text")

    def test_bad_tier_and_privacy_values_are_errors(self):
        with self.assertRaises(task.TaskError):
            task.parse("---\ntitle: x\ntier: gold\n---\n")
        with self.assertRaises(task.TaskError):
            task.parse("---\ntitle: x\nprivacy: maybe\n---\n")

    def test_unknown_key_is_error(self):
        with self.assertRaises(task.TaskError):
            task.parse("---\ntitle: x\ngates:\n  - a\n---\n")


if __name__ == "__main__":
    unittest.main()
