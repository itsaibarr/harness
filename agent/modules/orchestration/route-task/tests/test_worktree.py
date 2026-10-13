import tempfile
import unittest
from pathlib import Path

from route_task import worktree
from tests.gitrepo import git, make_repo


class WorktreeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.wt_root = self.root / "worktrees"

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_makes_branch_and_dir_and_excludes_run_dir(self):
        wt = worktree.create(self.repo, "run1", self.wt_root)
        self.assertTrue((wt.path / "a.txt").exists())
        self.assertEqual(git(wt.path, "rev-parse", "--abbrev-ref", "HEAD"), "rt/run1")
        self.assertEqual(wt.base_sha, git(self.repo, "rev-parse", "HEAD"))
        (wt.path / ".route-task").mkdir()
        (wt.path / ".route-task" / "handoff.md").write_text("x")
        self.assertEqual(git(wt.path, "status", "--porcelain"), "")

    def test_main_checkout_is_untouched(self):
        (self.repo / "sentinel.txt").write_text("keep")
        before = git(self.repo, "status", "--porcelain")
        wt = worktree.create(self.repo, "run1", self.wt_root)
        (wt.path / "new.txt").write_text("worker output")
        self.assertEqual(git(self.repo, "status", "--porcelain"), before)
        self.assertFalse((self.repo / "new.txt").exists())
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "main")

    def test_checkpoint_commits_changes_incl_new_files_and_returns_sha(self):
        wt = worktree.create(self.repo, "run1", self.wt_root)
        (wt.path / "new.txt").write_text("hello\n")
        (wt.path / "a.txt").write_text("changed\n")
        sha = wt.checkpoint("attempt 1")
        self.assertEqual(sha, git(wt.path, "rev-parse", "HEAD"))
        diff = wt.diff()
        self.assertIn("new.txt", diff)
        self.assertIn("+changed", diff)

    def test_checkpoint_with_no_changes_returns_none(self):
        wt = worktree.create(self.repo, "run1", self.wt_root)
        self.assertIsNone(wt.checkpoint("nothing"))
        self.assertEqual(wt.diff(), "")

    def test_checkpoint_skips_repo_hooks(self):
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        wt = worktree.create(self.repo, "run1", self.wt_root)
        (wt.path / "x.txt").write_text("x")
        self.assertIsNotNone(wt.checkpoint("hook must not run"))

    def test_open_existing_worktree_by_run_id(self):
        wt = worktree.create(self.repo, "run1", self.wt_root)
        again = worktree.open_(self.repo, "run1", self.wt_root, wt.base_sha)
        self.assertEqual(again.path, wt.path)

    def test_non_git_dir_is_error(self):
        plain = self.root / "plain"
        plain.mkdir()
        with self.assertRaises(worktree.WorktreeError):
            worktree.create(plain, "run1", self.wt_root)


if __name__ == "__main__":
    unittest.main()
