"""The application's name and logo, and the wiring that carries them.

Three platforms put this app's identity on screen through three unrelated
mechanisms — a macOS .icns inside the bundle, a freedesktop icon-theme entry
named by a .desktop file, a Windows .ico on a Start-up shortcut — and each
is installed by a different script in a different language. Nothing in a
normal test run executes any of them, so what is pinned here is that they
all still name the SAME asset, and that the asset is still the thing
app_icon draws.

The installer assertions are source-scraped, the way tests/test_update.py
reads the Swift: these files cannot be imported or (on this machine) run,
but they can be read, so a rename that breaks one of them fails the ordinary
Python suite instead of a user's install.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest

from smartbar.core import branding

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET = os.path.join(REPO, "assets", "ai-smartbar.png")
LINUX = os.path.join(REPO, "install", "linux.sh")
MACOS = os.path.join(REPO, "install", "macos-swift.sh")
WINDOWS = os.path.join(REPO, "install", "windows.ps1")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TestTheAssetExists(unittest.TestCase):
    def test_the_committed_logo_is_where_branding_says_it_is(self):
        # Every installer hardcodes this path in its own syntax; if the file
        # moves, all three degrade silently to a generic icon.
        self.assertTrue(os.path.isfile(ASSET), ASSET)
        self.assertEqual(branding.icon_path(), ASSET)

    def test_it_is_a_square_png_at_the_size_app_icon_draws(self):
        from smartbar.paint import app_icon

        with open(ASSET, "rb") as handle:
            header = handle.read(24)
        self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        self.assertEqual((width, height), (app_icon.SIZE, app_icon.SIZE))

    def test_a_missing_asset_reports_absence_rather_than_guessing(self):
        # The callers branch on "", because an icon flag aimed at a file that
        # is not there costs more than passing no icon flag at all.
        real = branding.os.path.isfile
        try:
            branding.os.path.isfile = lambda _: False
            self.assertEqual(branding.icon_path(), "")
        finally:
            branding.os.path.isfile = real


class TestTheLogoIsStillTheMenuBarMark(unittest.TestCase):
    """The whole point of the icon is that it is the badge you already have
    in your menu bar. These are the numbers that make it the same object."""

    def test_pill_proportions_come_from_the_macos_badge(self):
        from smartbar.paint import app_icon

        swift = read(os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                                  "MenuBarIcon.swift"))
        width = float(re.search(r"pillWidth: CGFloat = ([\d.]+)", swift).group(1))
        height = float(re.search(r"pillHeight: CGFloat = ([\d.]+)", swift).group(1))
        gap = float(re.search(r"gap: CGFloat = ([\d.]+)", swift).group(1))
        self.assertAlmostEqual(app_icon.PILL_RATIO, width / height)
        self.assertAlmostEqual(app_icon.GAP_RATIO, gap / height)

    def test_the_fills_use_the_shared_ramp_and_stop_short_of_critical(self):
        from smartbar.core.model import RGB
        from smartbar.paint import app_icon

        names = [name for _, name in app_icon.STATES]
        for name in names:
            self.assertIn(name, RGB)
        # A logo is permanent. "critical" would mean this app's icon always
        # says the user is out of budget.
        self.assertNotIn("critical", names)
        fractions = [fraction for fraction, _ in app_icon.STATES]
        self.assertEqual(fractions, sorted(fractions))
        self.assertLess(max(fractions), 1.0)


class TestRenderingIsReproducible(unittest.TestCase):
    """The asset is a generated artifact, like Version.swift. If the painter
    and the committed PNG disagree, one of them was edited by hand."""

    def test_the_committed_png_is_what_the_painter_draws_today(self):
        try:
            import cairo  # noqa: F401
        except ImportError:
            self.skipTest("pycairo not installed")
        import io

        from smartbar.paint import app_icon

        buffer = io.BytesIO()
        app_icon.render(buffer)
        with open(ASSET, "rb") as handle:
            self.assertEqual(buffer.getvalue(), handle.read(),
                             "assets/ai-smartbar.png is stale — regenerate it "
                             "with: python3 -m smartbar.paint.app_icon "
                             "assets/ai-smartbar.png")

    def test_render_honours_the_size_the_linux_installer_asks_for(self):
        try:
            import cairo  # noqa: F401
        except ImportError:
            self.skipTest("pycairo not installed")
        import io

        from smartbar.paint import app_icon

        buffer = io.BytesIO()
        app_icon.render(buffer, 64)
        header = buffer.getvalue()[16:24]
        self.assertEqual(int.from_bytes(header[:4], "big"), 64)
        self.assertEqual(int.from_bytes(header[4:], "big"), 64)

    def test_the_module_is_runnable_the_way_linux_sh_runs_it(self):
        try:
            import cairo  # noqa: F401
        except ImportError:
            self.skipTest("pycairo not installed")
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "ai-smartbar.png")
            # Exactly install/linux.sh's install_icon invocation.
            done = subprocess.run(
                [sys.executable, "-m", "smartbar.paint.app_icon", out, "512"],
                cwd=REPO, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue(os.path.isfile(out))


class TestLinuxCarriesTheNameAndTheLogo(unittest.TestCase):
    def test_the_desktop_entry_names_the_icon_the_installer_publishes(self):
        script = read(LINUX)
        name = re.search(r'^ICON_NAME="([^"]+)"', script, re.M).group(1)
        self.assertEqual(name, branding.ICON_NAME)
        # The .desktop Icon= is a theme name, so it only resolves if the
        # installer actually put a file under that name in the theme.
        self.assertIn("Icon=${ICON_NAME}", script)
        self.assertIn('"$ICON_DIR/$ICON_NAME.png"', script)
        self.assertIn("hicolor", script)

    def test_the_icon_is_installed_before_the_entry_that_references_it(self):
        script = read(LINUX)
        self.assertLess(script.index("\ninstall_icon\n"),
                        script.index('cat > "$AUTOSTART"'))

    def test_uninstall_takes_the_published_icon_with_it(self):
        self.assertIn('"$ICON_DIR/$ICON_NAME.png"',
                      read(LINUX).split("--uninstall)")[1].split(";;")[0])

    def test_notify_send_is_told_who_it_is_from(self):
        from smartbar import update_runner, warmup_runner

        for module in (update_runner, warmup_runner):
            source = read(module.__file__)
            self.assertIn('"-a", branding.APP_NAME, "-i", branding.ICON_NAME,',
                          source, module.__name__)


class TestMacOSCarriesTheNameAndTheLogo(unittest.TestCase):
    def test_the_bundle_declares_an_icon_built_from_the_shared_asset(self):
        script = read(MACOS)
        self.assertIn("assets/ai-smartbar.png", script)
        self.assertIn("<key>CFBundleIconFile</key><string>AppIcon</string>",
                      script)
        # CFBundleIconFile names AppIcon; iconutil must produce AppIcon.icns
        # in Resources/ or the key points at nothing.
        self.assertIn('iconutil -c icns "$ICONSET" '
                      '-o "$APP_DIR/Contents/Resources/AppIcon.icns"', script)

    def test_the_login_item_is_named_for_humans(self):
        # Without CFBundleDisplayName macOS falls back to CFBundleExecutable,
        # which is why "App Background Activity" said "AISmartbar".
        self.assertIn("<key>CFBundleDisplayName</key><string>AI smartbar</string>",
                      read(MACOS))

    def test_the_iconset_covers_the_small_sizes_the_finder_actually_uses(self):
        script = read(MACOS)
        # `.*?; do` rather than a run of pairs: the list wraps with a shell
        # line-continuation, which a pair-by-pair pattern stops dead at.
        specs = re.search(r"for spec in (.*?); do", script, re.S).group(1)
        pairs = [(int(nominal), int(pixels))
                 for nominal, pixels in re.findall(r"(\d+):(\d+)", specs)]
        nominals = {nominal for nominal, _ in pairs}
        self.assertEqual(nominals, {16, 32, 128, 256, 512}, specs)
        # Every nominal size needs its @2x twin, or Retina renders the 1x.
        retina = {nominal for nominal, pixels in pairs if pixels == nominal * 2}
        self.assertEqual(retina, nominals, specs)
        self.assertEqual({nominal for nominal, pixels in pairs
                          if pixels == nominal}, nominals, specs)

    def test_a_missing_asset_costs_the_icon_and_not_the_install(self):
        # This installer is also the update APPLY step; aborting here would
        # leave a device with no menu bar over a cosmetic failure.
        script = read(MACOS)
        block = script.split('ICON_SRC="$REPO/assets/ai-smartbar.png"')[1]
        block = block.split("cat > \"$APP_DIR/Contents/Info.plist\"")[0]
        self.assertIn("WARNING", block)
        self.assertNotIn("exit 1", block)


class TestWindowsCarriesTheNameAndTheLogo(unittest.TestCase):
    def test_the_shortcut_points_at_an_ico_converted_from_the_asset(self):
        script = read(WINDOWS)
        self.assertIn("assets\\ai-smartbar.png", script)
        self.assertIn('$link.IconLocation = "$IconIco,0"', script)
        # A .lnk cannot read a PNG, so the conversion must happen first.
        self.assertLess(script.index("[System.Drawing.Icon]::FromHandle"),
                        script.index("$link.IconLocation"))

    def test_the_generated_ico_is_not_committed(self):
        self.assertIn("assets/*.ico", read(os.path.join(REPO, ".gitignore")))

    def test_uninstall_removes_what_it_generated_and_keeps_what_it_did_not(self):
        block = read(WINDOWS).split("if ($Uninstall) {")[1].split("exit 0")[0]
        self.assertIn("$IconIco", block)
        self.assertNotIn("$IconPng", block)

    def test_the_conversion_cannot_take_the_install_down_with_it(self):
        script = read(WINDOWS)
        block = script.split("# The shortcut's icon.")[1].split("$wscript =")[0]
        self.assertIn("try {", block)
        self.assertIn("} catch {", block)


class TestMacOSNotificationsAreKnowinglyLeftAlone(unittest.TestCase):
    """A guard on a deliberate NON-fix.

    Both replacements for osascript were measured against this ad-hoc-signed
    bundle on macOS 26.5 and neither delivers, so the wrong-sender wart
    stays. What must not happen is someone "fixing" it from memory, shipping
    a path that silently posts nothing, and losing update notifications on
    every Mac. The reasoning has to survive in the file that would be edited.
    """

    def test_the_swift_notifier_records_why_it_still_uses_osascript(self):
        swift = read(os.path.join(REPO, "macos-swift", "Sources", "AISmartbar",
                                  "UsageStore.swift"))
        block = swift[:swift.index("static func notify(")]
        self.assertIn("UNUserNotificationCenter", block)
        self.assertIn("NSUserNotificationCenter", block)
        self.assertIn("Developer ID", block)

    def test_branding_does_not_offer_macos_a_notification_identity(self):
        # icon_path/APP_NAME are for Linux and Windows. A darwin helper here
        # would be dead code that reads like a working feature.
        self.assertFalse(hasattr(branding, "darwin"))


if __name__ == "__main__":
    unittest.main()
