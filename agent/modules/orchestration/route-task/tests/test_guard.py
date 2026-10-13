import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from route_task import config, guard


class ChildEnvTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(None)
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg.state_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_secrets_and_credentials_are_stripped(self):
        dirty = {"ANTHROPIC_API_KEY": "sk-x", "GH_TOKEN": "t", "GITHUB_TOKEN": "t",
                 "SSH_AUTH_SOCK": "/s", "RESEND_API_KEY": "r", "PATH": "/usr/bin:/bin", "HOME": "/h"}
        with mock.patch.dict(os.environ, dirty, clear=True):
            env = guard.child_env(self.cfg)
        for k in ("ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "RESEND_API_KEY"):
            self.assertNotIn(k, env)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertEqual(env["HOME"], "/h")

    def test_forbidden_vars_stay_stripped_even_if_listed_as_extra(self):
        self.cfg.env.extra = ["ANTHROPIC_API_KEY", "MY_FLAG"]
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk", "MY_FLAG": "1"}, clear=True):
            env = guard.child_env(self.cfg)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["MY_FLAG"], "1")

    def test_git_and_gh_credentials_are_starved(self):
        with mock.patch.dict(os.environ, {"PATH": "/bin"}, clear=True):
            env = guard.child_env(self.cfg)
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_ASKPASS"], "/usr/bin/false")
        self.assertEqual(env["GIT_SSH_COMMAND"], "/usr/bin/false")
        self.assertTrue(Path(env["GH_CONFIG_DIR"]).is_dir())
        self.assertEqual(list(Path(env["GH_CONFIG_DIR"]).iterdir()), [])

    def test_explicit_extra_overrides_but_cannot_reintroduce_forbidden(self):
        with mock.patch.dict(os.environ, {"PATH": "/bin"}, clear=True):
            env = guard.child_env(self.cfg, extra={"PATH": "/stubs:/bin", "GH_TOKEN": "x", "STUB_LOG": "/l"})
        self.assertEqual(env["PATH"], "/stubs:/bin")
        self.assertEqual(env["STUB_LOG"], "/l")
        self.assertNotIn("GH_TOKEN", env)

    def test_no_credential_helper_visible_even_through_nested_bash(self):
        # the nested `bash -c` form bypassed the advisory deny rules in spike S1; credentials must be absent anyway
        import subprocess
        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/")}, clear=True):
            env = guard.child_env(self.cfg)
        out = subprocess.run(["bash", "-c", "git config --get credential.helper; git config --get-regexp '^url\\.' ; echo helper-empty"],
                             env=env, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "helper-empty")


class DenyListTest(unittest.TestCase):
    def test_claude_deny_args_cover_push_forms_and_deploys(self):
        arg = guard.claude_deny_arg()
        for needle in ("Bash(git push*)", "Bash(git * push*)", "Bash(gh pr*)", "Bash(vercel*)", "Bash(npm publish*)", "Bash(sudo*)"):
            self.assertIn(needle, arg.split(","))

    def test_opencode_permission_env_is_json_denying_push(self):
        import json
        perm = json.loads(guard.opencode_permission())
        self.assertEqual(perm["bash"]["*"], "allow")
        self.assertEqual(perm["bash"]["git push*"], "deny")
        self.assertEqual(perm["edit"], "allow")


if __name__ == "__main__":
    unittest.main()
