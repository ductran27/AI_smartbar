"""Tests for the cswap fresh-primer plumbing (venv resolution + fallback)."""
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

from smartbar.core import cswap

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "cswap_list.json")


def write_script(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, "w") as handle:
        handle.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {name: os.environ.pop(name, None)
                      for name in ("SMARTBAR_CSWAP", "SMARTBAR_CSWAP_PYTHON")}
        cswap._combined_unsupported = False  # reset the per-process latch

    def tearDown(self):
        self.tmp.cleanup()
        cswap._combined_unsupported = False
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class TestVenvPython(Env):
    def test_parses_pipx_launcher_exec_line(self):
        # sys.executable stands in for the venv python: the parsed path must
        # exist, and a real interpreter always does.
        launcher = write_script(self.tmp.name, "cswap",
                                "#!/bin/sh\n"
                                f"'''exec' '{sys.executable}' \"$0\" \"$@\"\n"
                                "' '''\n")
        os.environ["SMARTBAR_CSWAP"] = launcher
        self.assertEqual(cswap.venv_python(), sys.executable)

    def test_no_python_line_is_none(self):
        plain = write_script(self.tmp.name, "cswap", "#!/bin/sh\nexit 0\n")
        os.environ["SMARTBAR_CSWAP"] = plain
        self.assertIsNone(cswap.venv_python())

    def test_env_override_wins(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = "/custom/python3"
        self.assertEqual(cswap.venv_python(), "/custom/python3")

    def test_missing_binary_is_none(self):
        os.environ["SMARTBAR_CSWAP"] = os.path.join(self.tmp.name, "absent")
        self.assertIsNone(cswap.venv_python())


class TestPrimer(Env):
    def test_primer_code_compiles(self):
        compile(cswap.PRIMER_CODE, "<primer>", "exec")

    def test_prime_fresh_false_without_interpreter(self):
        os.environ["SMARTBAR_CSWAP"] = write_script(self.tmp.name, "cswap",
                                                    "#!/bin/sh\nexit 0\n")
        self.assertFalse(cswap.prime_fresh())

    def test_prime_fresh_false_on_broken_interpreter(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = os.path.join(self.tmp.name, "nope")
        self.assertFalse(cswap.prime_fresh())

    def test_prime_fresh_true_on_clean_run(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        with mock.patch.object(cswap, "PRIMER_CODE", "import sys; sys.exit(0)"):
            self.assertTrue(cswap.prime_fresh())

    def test_fetch_fresh_survives_missing_primer(self):
        with open(FIXTURE) as handle:
            payload = handle.read().replace("'", "'\\''")
        mock_cswap = write_script(self.tmp.name, "cswap",
                                  "#!/bin/sh\n"
                                  f"echo '{payload}'\n")
        os.environ["SMARTBAR_CSWAP"] = mock_cswap
        snap = cswap.fetch(fresh=True)   # combined+primer skipped: no venv python
        self.assertTrue(snap.accounts)


class TestCombined(Env):
    def test_combined_code_compiles(self):
        compile(cswap.COMBINED_CODE, "<combined>", "exec")

    def test_fetch_fresh_uses_combined_output(self):
        # The combined program's stdout IS the snapshot: no binary list run.
        with open(FIXTURE) as handle:
            payload = handle.read()
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        os.environ["SMARTBAR_CSWAP"] = os.path.join(self.tmp.name, "absent")
        fake = ("import sys, json\n"
                f"sys.stdout.write({payload!r})\n")
        with mock.patch.object(cswap, "COMBINED_CODE", fake):
            snap = cswap.fetch(fresh=True)
        self.assertTrue(snap.accounts)  # absent binary proves no fallback ran

    def test_exit_97_latches_unsupported(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        with mock.patch.object(cswap, "COMBINED_CODE", "import sys; sys.exit(97)"):
            self.assertIsNone(cswap.fetch_combined())
        self.assertTrue(cswap._combined_unsupported)
        # Latched: not even attempted again (a crashing program would throw
        # if it ran — the latch means we never get that far).
        with mock.patch.object(cswap, "COMBINED_CODE", "import sys; sys.exit(0)"):
            self.assertIsNone(cswap.fetch_combined())

    def test_nonzero_exit_falls_back_without_latching(self):
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        with mock.patch.object(cswap, "COMBINED_CODE", "import sys; sys.exit(1)"):
            self.assertIsNone(cswap.fetch_combined())
        self.assertFalse(cswap._combined_unsupported)

    def test_fetch_fresh_falls_back_to_binary_on_combined_failure(self):
        with open(FIXTURE) as handle:
            payload = handle.read().replace("'", "'\\''")
        mock_cswap = write_script(self.tmp.name, "cswap",
                                  "#!/bin/sh\n"
                                  f"echo '{payload}'\n")
        os.environ["SMARTBAR_CSWAP"] = mock_cswap
        os.environ["SMARTBAR_CSWAP_PYTHON"] = sys.executable
        with mock.patch.object(cswap, "COMBINED_CODE", "import sys; sys.exit(97)"), \
                mock.patch.object(cswap, "PRIMER_CODE", "import sys; sys.exit(0)"):
            snap = cswap.fetch(fresh=True)
        self.assertTrue(snap.accounts)


if __name__ == "__main__":
    unittest.main()
