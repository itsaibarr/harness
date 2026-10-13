"""The harness wiring: installer, symlink-safe launcher, skill/command masters, router docs."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).parent.parent
ROOT = MODULE.parents[3]  # harness repo root
INSTALL = MODULE / "install.sh"


class InstallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.env = {**os.environ, "HOME": str(self.home)}
        self.env.pop("ROUTE_TASK_BIN_DIR", None)
        self.env.pop("ROUTE_TASK_CONFIG", None)

    def tearDown(self):
        self._tmp.cleanup()

    def install(self):
        return subprocess.run(["bash", str(INSTALL)], capture_output=True, text=True, env=self.env)

    def test_installs_launcher_skill_command_and_config(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        link = self.home / ".local/bin/route-task"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (MODULE / "route-task").resolve())
        self.assertIn("name: route-task", (self.home / ".claude/skills/route-task/SKILL.md").read_text())
        self.assertIn('skill="route-task"', (self.home / ".claude/commands/route.md").read_text())
        self.assertTrue((self.home / ".route-task/config.toml").exists())
        self.assertTrue((self.home / ".route-task/tasks").is_dir())

    def test_launcher_works_through_the_symlink_from_any_directory(self):
        self.install()
        with tempfile.TemporaryDirectory() as elsewhere:
            r = subprocess.run([str(self.home / ".local/bin/route-task"), "status"], capture_output=True, text=True,
                               env=self.env, cwd=elsewhere)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no runs yet", r.stdout)

    def test_rerun_is_idempotent_and_never_overwrites_the_users_config(self):
        self.install()
        cfg = self.home / ".route-task/config.toml"
        cfg.write_text("[economy]\naccept_training_on_private = true\n")
        skill = self.home / ".claude/skills/route-task/SKILL.md"
        skill.write_text("stale")
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(cfg.read_text(), "[economy]\naccept_training_on_private = true\n")
        self.assertIn("name: route-task", skill.read_text())  # master copy refreshed

    def test_users_config_is_picked_up_by_the_installed_launcher(self):
        self.install()
        (self.home / ".route-task/config.toml").write_text("[limits]\nworker_attempts = \"bad\"\n")
        r = subprocess.run([str(self.home / ".local/bin/route-task"), "status"], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("config error", r.stderr)


class WiringDocsTest(unittest.TestCase):
    def read(self, rel):
        return (ROOT / rel).read_text()

    def test_skill_master_has_frontmatter_triggers_and_safety_rules(self):
        s = self.read("agent/core/framework/skills/route-task/SKILL.md")
        head = re.match(r"---\n(.*?)\n---\n", s, re.S).group(1)
        self.assertIn("name: route-task", head)
        for trigger in ("delegate", "overnight", "while I'm away", "several independent"):
            self.assertIn(trigger, head)
        for must in ("route-task run", "route-task report", "Never push or merge", "high-risk", "run_in_background", "~/.route-task/tasks"):
            self.assertIn(must, s)

    def test_command_master_invokes_the_skill_by_exact_name(self):
        c = self.read("agent/core/framework/commands/route.md")
        self.assertIn('skill="route-task"', c)
        self.assertIn("$ARGUMENTS", c)

    def test_router_and_inventory_know_the_route(self):
        router = self.read("agent/core/task-router.md")
        self.assertRegex(router, r"\| 11 \| .*route-task")
        self.assertIn("D4", router.split("| 11 |")[1].split("\n")[0])  # high-risk precedence stated on the row
        self.assertIn("route-task", self.read("docs/agent-harness/skill-inventory.md"))
        self.assertIn("orchestration", self.read("README.md"))


if __name__ == "__main__":
    unittest.main()
