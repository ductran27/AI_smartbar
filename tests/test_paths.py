"""Tests for smartbar.core.paths — the one place cache/config layout lives.

Every one of these guards a specific regression: the win32 branch existing
at all (Phase 1 of the Windows port), the `linux/tray.py` bug where the
`SMARTBAR_CACHE_DIR` override got dropped while the expression was copied,
and the `or` (not `is None`) fallback shape that makes an override of the
empty string behave like no override — which is what the three modules
that got this right already did, and what tests/e2e-warmup.sh relies on.
"""

import os
import unittest

from smartbar.core import paths


class Env(unittest.TestCase):
    """Save/restore the env vars and sys.platform each test pokes at.

    Mirrors the base class in tests/test_model.py and the platform-swap
    pattern in tests/test_presence.py: fake the platform by assigning to
    the module's `sys.platform` and always put it back in tearDown, since
    a leaked platform value would silently bend every other test file that
    runs afterward in the same process.
    """

    ENV_VARS = ("SMARTBAR_CACHE_DIR", "SMARTBAR_CONFIG_DIR", "LOCALAPPDATA", "APPDATA")

    def setUp(self):
        self.saved_env = {name: os.environ.pop(name, None) for name in self.ENV_VARS}
        self.saved_platform = paths.sys.platform

    def tearDown(self):
        paths.sys.platform = self.saved_platform
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class TestOverrideWins(Env):
    def test_cache_dir_override_wins_on_every_platform(self):
        os.environ["SMARTBAR_CACHE_DIR"] = "/tmp/wherever-the-user-said"
        for plat in ("darwin", "linux", "win32"):
            paths.sys.platform = plat
            self.assertEqual(paths.cache_dir(), "/tmp/wherever-the-user-said")

    def test_config_dir_override_wins_on_every_platform(self):
        os.environ["SMARTBAR_CONFIG_DIR"] = "/tmp/wherever-else"
        for plat in ("darwin", "linux", "win32"):
            paths.sys.platform = plat
            self.assertEqual(paths.config_dir(), "/tmp/wherever-else")


class TestEmptyOverrideFallsThrough(Env):
    """An override of "" must behave like no override, not like a real path.

    This is the `or os.path.expanduser(...)` shape (not `is None`) that
    presence_runner.py, warmup_runner.py and update_runner.py already used
    — an agent environment that sets `SMARTBAR_CACHE_DIR=` (present but
    empty) must still land in the real cache directory, not silently try
    to read and write the process's current working directory.
    """

    def test_empty_cache_override_falls_through_to_the_posix_default(self):
        os.environ["SMARTBAR_CACHE_DIR"] = ""
        paths.sys.platform = "linux"
        self.assertEqual(paths.cache_dir(), os.path.expanduser("~/.cache/ai-smartbar"))

    def test_empty_config_override_falls_through_to_the_posix_default(self):
        os.environ["SMARTBAR_CONFIG_DIR"] = ""
        paths.sys.platform = "linux"
        self.assertEqual(paths.config_dir(),
                         os.path.expanduser("~/.config/ai-smartbar"))


class TestWin32Defaults(Env):
    def test_cache_dir_uses_localappdata_on_win32(self):
        os.environ["LOCALAPPDATA"] = r"C:\Users\duc\AppData\Local"
        paths.sys.platform = "win32"
        self.assertEqual(paths.cache_dir(),
                         os.path.join(r"C:\Users\duc\AppData\Local", "ai-smartbar"))

    def test_config_dir_uses_appdata_on_win32(self):
        os.environ["APPDATA"] = r"C:\Users\duc\AppData\Roaming"
        paths.sys.platform = "win32"
        self.assertEqual(paths.config_dir(),
                         os.path.join(r"C:\Users\duc\AppData\Roaming", "ai-smartbar"))

    def test_cache_dir_falls_back_to_posix_shape_if_localappdata_is_unset(self):
        paths.sys.platform = "win32"
        self.assertEqual(paths.cache_dir(), os.path.expanduser("~/.cache/ai-smartbar"))

    def test_config_dir_falls_back_to_posix_shape_if_appdata_is_unset(self):
        paths.sys.platform = "win32"
        self.assertEqual(paths.config_dir(),
                         os.path.expanduser("~/.config/ai-smartbar"))


class TestPosixDefaults(Env):
    def test_cache_dir_default_on_darwin_and_linux(self):
        for plat in ("darwin", "linux"):
            paths.sys.platform = plat
            self.assertEqual(paths.cache_dir(),
                             os.path.expanduser("~/.cache/ai-smartbar"))

    def test_config_dir_default_on_darwin_and_linux(self):
        for plat in ("darwin", "linux"):
            paths.sys.platform = plat
            self.assertEqual(paths.config_dir(),
                             os.path.expanduser("~/.config/ai-smartbar"))


class TestTheTwoDirectoriesAreDistinct(Env):
    """CACHE_DIR and CONFIG_DIR must never collapse into the same path.

    Disposable state (locks, logs, the presence snapshot) and durable state
    (the device id, per-device config.env) get wiped by different things —
    a user clearing their cache should not also erase device identity —
    so this holds on every platform, defaults and overrides alike.
    """

    def test_distinct_with_platform_defaults(self):
        for plat in ("darwin", "linux", "win32"):
            paths.sys.platform = plat
            self.assertNotEqual(paths.cache_dir(), paths.config_dir())

    def test_distinct_with_win32_env_vars_set(self):
        os.environ["LOCALAPPDATA"] = r"C:\Users\duc\AppData\Local"
        os.environ["APPDATA"] = r"C:\Users\duc\AppData\Roaming"
        paths.sys.platform = "win32"
        self.assertNotEqual(paths.cache_dir(), paths.config_dir())

    def test_distinct_with_smartbar_overrides_set(self):
        os.environ["SMARTBAR_CACHE_DIR"] = "/tmp/cache-here"
        os.environ["SMARTBAR_CONFIG_DIR"] = "/tmp/config-there"
        for plat in ("darwin", "linux", "win32"):
            paths.sys.platform = plat
            self.assertNotEqual(paths.cache_dir(), paths.config_dir())


if __name__ == "__main__":
    unittest.main()
