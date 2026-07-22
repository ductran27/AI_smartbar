"""Tests for smartbar.warmup_runner argv/env construction — no subprocesses.

These pin the two launchd bugs that silently broke v1 warmup:
- cswap resolves `claude` itself via PATH, so the subprocess env must
  carry a PATH containing the claude CLI (launchd hands agents a bare one);
- everything after `--` in `cswap run` is passed to claude as ARGUMENTS,
  so the claude binary path must never appear there.
"""
import os
import unittest

from smartbar import warmup_runner


class Env(unittest.TestCase):
    def setUp(self):
        self.saved = {name: os.environ.get(name)
                      for name in ("SMARTBAR_CSWAP", "PATH")}
        os.environ["SMARTBAR_CSWAP"] = "/mock/bin/cswap"

    def tearDown(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class TestPingArgv(Env):
    def test_post_dashdash_is_claude_args_only(self):
        argv = warmup_runner.ping_argv(2, ["--model", "haiku"])
        self.assertEqual(argv, ["/mock/bin/cswap", "run", "2", "--",
                                "--model", "haiku", "-p", ".",
                                "--max-turns", "1"])
        # Regression: the claude binary path must NOT be smuggled in as an
        # argument — cswap resolves the binary itself.
        self.assertFalse(any(token.endswith("/claude") for token in argv))

    def test_plain_retry_has_no_model_flag(self):
        argv = warmup_runner.ping_argv(1, [])
        self.assertEqual(argv[3], "--")
        self.assertNotIn("--model", argv)


class TestEnvWithClaudeOnPath(Env):
    def test_prepends_claude_dir_and_common_bins(self):
        os.environ["PATH"] = "/usr/bin:/bin"  # launchd's bare default
        env = warmup_runner.env_with_claude_on_path("/some/where/claude")
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], "/some/where")
        self.assertIn(os.path.expanduser("~/.local/bin"), parts)
        self.assertIn("/opt/homebrew/bin", parts)
        # The original PATH survives at the tail.
        self.assertIn("/usr/bin", parts)
        self.assertIn("/bin", parts)

    def test_deduplicates_and_keeps_order(self):
        os.environ["PATH"] = "/opt/homebrew/bin:/usr/bin"
        env = warmup_runner.env_with_claude_on_path("/opt/homebrew/bin/claude")
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts.count("/opt/homebrew/bin"), 1)
        self.assertEqual(parts[0], "/opt/homebrew/bin")

    def test_other_env_untouched(self):
        os.environ["PATH"] = "/usr/bin"
        env = warmup_runner.env_with_claude_on_path("/x/claude")
        self.assertEqual(env["SMARTBAR_CSWAP"], "/mock/bin/cswap")


if __name__ == "__main__":
    unittest.main()
