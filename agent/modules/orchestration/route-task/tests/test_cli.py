import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.gitrepo import make_repo

MODULE = Path(__file__).parent.parent
STUBS = MODULE / "tests" / "stubs"


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.state = self.root / "state"
        self.cfg = self.root / "cfg.toml"
        self.cfg.write_text(f'state_dir = "{self.state}"\n[economy]\ntrains_on_data = false\n[evaluator]\nenabled = false\n'
                            '[env]\nextra = ["STUB_WRITE", "STUB_LOG", "STUB_OC", "STUB_CL"]\n')
        self.task = self.root / "task.md"
        self.task.write_text(f"---\ntitle: Make out\nrepo: {self.repo}\nprivacy: open\ngate:\n  - test -f out.txt\n---\nCreate out.txt.")
        self.env = {**os.environ, "PATH": f"{STUBS}:{os.environ['PATH']}", "PYTHONPATH": str(MODULE), "STUB_WRITE": "out.txt"}

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *args, **env):
        return subprocess.run([sys.executable, "-m", "route_task", *args], capture_output=True, text=True,
                              env={**self.env, **env}, cwd=self.root)

    def test_run_done_prints_run_id_status_and_report_path(self):
        r = self.cli("run", str(self.task), "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("done", r.stdout)
        runs = list((self.state / "runs").iterdir())
        self.assertEqual(len(runs), 1)
        self.assertIn(runs[0].name, r.stdout)
        self.assertTrue((runs[0] / "report.md").exists())

    def test_run_that_cannot_pass_exits_nonzero(self):
        r = self.cli("run", str(self.task), "--config", str(self.cfg), STUB_WRITE="nothing.txt", STUB_CL="ok")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("needs_human", r.stdout)

    def test_status_and_report_commands(self):
        self.cli("run", str(self.task), "--config", str(self.cfg))
        st = self.cli("status", "--config", str(self.cfg))
        self.assertEqual(st.returncode, 0)
        self.assertIn("done", st.stdout)
        run_id = next((self.state / "runs").iterdir()).name
        rep = self.cli("report", run_id, "--config", str(self.cfg))
        self.assertIn("Premium used: no", rep.stdout)

    def test_status_with_no_runs_is_not_an_error(self):
        st = self.cli("status", "--config", str(self.cfg))
        self.assertEqual(st.returncode, 0)

    def test_stop_creates_kill_switch_and_blocks_runs(self):
        s = self.cli("stop", "--config", str(self.cfg))
        self.assertEqual(s.returncode, 0)
        self.assertTrue((self.state / "STOP").exists())
        r = self.cli("run", str(self.task), "--config", str(self.cfg))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("stopped", r.stdout)

    def test_bad_task_file_is_a_clear_error_not_a_traceback(self):
        bad = self.root / "bad.md"
        bad.write_text("no front matter")
        r = self.cli("run", str(bad), "--config", str(self.cfg))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("front matter", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
