"""The DMG distribution channel, pinned the way the rest of the installers are.

A DMG-installed copy and a checkout-installed copy are the SAME app with two
different updaters — the checkout pulls git, the DMG follows a Sparkle appcast
(see macos-swift/Sources/AISmartbar/Distribution.swift). None of this runs in a
normal test (no Xcode, no Developer ID, no Apple), so what is pinned here is the
wiring that keeps the two installs identical where they must be and correctly
different where they must be:

  * the DMG carries the same icon, built from the same asset, as the checkout;
  * both bundles link Sparkle and therefore both must embed its framework;
  * the DMG bundle — and only it — declares itself to Sparkle;
  * the DMG is hardened-runtime signed, notarized and stapled.

Source-scraped, exactly like tests/test_branding.py: these scripts cannot be
run here, but a rename or a dropped step fails the ordinary Python suite instead
of a user's download.
"""
from __future__ import annotations

import base64
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MACOS = os.path.join(REPO, "install", "macos-swift.sh")
PKGDMG = os.path.join(REPO, "install", "package-dmg.sh")
ENTITLEMENTS = os.path.join(REPO, "install", "entitlements.plist")
PACKAGE_SWIFT = os.path.join(REPO, "macos-swift", "Package.swift")
PACKAGE_RESOLVED = os.path.join(REPO, "macos-swift", "Package.resolved")
WORKFLOW = os.path.join(REPO, ".github", "workflows", "release-dmg.yml")
SRC = os.path.join(REPO, "macos-swift", "Sources", "AISmartbar")

BUNDLE_ID = "com.ductran.ai-smartbar"


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def iconset_sizes(script: str) -> list[tuple[int, int]]:
    """The (nominal, pixels) pairs from a `for spec in … ; do` iconset loop."""
    specs = re.search(r"for spec in (.*?); do", script, re.S).group(1)
    return [(int(n), int(p)) for n, p in re.findall(r"(\d+):(\d+)", specs)]


class TestBothInstallsLinkAndEmbedSparkle(unittest.TestCase):
    def test_package_declares_sparkle_and_the_bundle_rpath(self):
        pkg = read(PACKAGE_SWIFT)
        self.assertIn("github.com/sparkle-project/Sparkle", pkg)
        # Without this rpath the bundled app cannot find the framework Finder
        # launches place in Contents/Frameworks, and dyld kills it.
        self.assertIn("@executable_path/../Frameworks", pkg)

    def test_the_dependency_is_pinned(self):
        # A binary-artifact dependency must resolve to the same build every
        # time, on every runner — otherwise CI and a maintainer's Mac can ship
        # different Sparkle versions from one tag.
        self.assertIn('"identity" : "sparkle"', read(PACKAGE_RESOLVED))

    def test_checkout_install_embeds_the_framework_before_signing(self):
        script = read(MACOS)
        self.assertIn(
            'ditto "$BUILD_DIR/Sparkle.framework" \\', script)
        self.assertIn('"$APP_DIR/Contents/Frameworks/Sparkle.framework"', script)
        # The framework must be in place before the deep signature covers it.
        self.assertLess(script.index("Sparkle.framework"),
                        script.index("codesign --force --deep --sign -"))

    def test_missing_framework_warns_rather_than_failing_the_update(self):
        # macos-swift.sh is also the update-apply step; a cosmetic-looking
        # abort here is a device that stops running the app.
        script = read(MACOS)
        block = script.split("BUILD_DIR=\"$(dirname \"$BIN\")\"")[1]
        block = block.split("The app icon")[0]
        self.assertIn("WARNING", block)
        self.assertNotIn("exit 1", block)


class TestTheDmgBundleDeclaresItself(unittest.TestCase):
    """Only the DMG copy sets these — absence is what makes a checkout a
    checkout (Distribution.current defaults to .source)."""

    def setUp(self):
        self.script = read(PKGDMG)

    def test_it_marks_itself_a_dmg_install(self):
        self.assertIn(
            "<key>SMARTBARDistribution</key><string>dmg</string>", self.script)

    def test_it_points_sparkle_at_a_feed_and_a_verifying_key(self):
        self.assertIn("<key>SUFeedURL</key>", self.script)
        self.assertIn("<key>SUPublicEDKey</key>", self.script)

    def test_it_carries_a_bundle_version_for_sparkle_to_compare(self):
        # Sparkle compares the appcast's sparkle:version against CFBundleVersion;
        # without it every check looks like "no update".
        self.assertIn("<key>CFBundleVersion</key>", self.script)

    def test_it_is_the_same_app_identity_as_the_checkout_install(self):
        # A different bundle id would make the DMG a *second* app, not an
        # alternative install of the one in the menu bar.
        self.assertIn(BUNDLE_ID, self.script)
        self.assertIn(BUNDLE_ID, read(MACOS))

    def test_the_public_key_is_a_32_byte_ed25519_key(self):
        key = re.search(r'SPARKLE_PUBLIC_ED_KEY="([^"]+)"', self.script).group(1)
        self.assertEqual(len(base64.b64decode(key)), 32, key)


class TestTheIconIsIdenticalToTheCheckoutInstall(unittest.TestCase):
    """The whole point is that the DMG app is visually the checkout app. If the
    two iconset recipes drift, the two installs stop looking alike."""

    def test_both_build_the_icon_from_the_one_committed_asset(self):
        for path in (MACOS, PKGDMG):
            self.assertIn("assets/ai-smartbar.png", read(path), path)
            self.assertIn("iconutil -c icns", read(path), path)

    def test_both_iconsets_cover_exactly_the_same_sizes(self):
        self.assertEqual(iconset_sizes(read(MACOS)), iconset_sizes(read(PKGDMG)))


class TestTheDmgIsSignedNotarizedAndStapled(unittest.TestCase):
    def setUp(self):
        self.script = read(PKGDMG)

    def test_it_signs_with_the_hardened_runtime_and_our_entitlements(self):
        self.assertIn("--options runtime", self.script)
        self.assertIn('--entitlements "$ENTITLEMENTS"', self.script)

    def test_it_signs_inside_out_not_with_deep(self):
        # Apple discourages --deep for distribution; the nested Sparkle
        # executables must be signed before the framework and the app.
        self.assertIn("Autoupdate", self.script)
        self.assertLess(self.script.index('sign "$FW"'),
                        self.script.index('--sign "$SIGN_IDENTITY" "$APP_DIR"'))

    def test_it_notarizes_and_staples_both_the_app_and_the_dmg(self):
        self.assertIn("xcrun notarytool submit", self.script)
        self.assertIn('xcrun stapler staple "$APP_DIR"', self.script)
        self.assertIn("notarize_and_staple \"$DMG\"", self.script)

    def test_no_identity_degrades_to_ad_hoc_rather_than_failing(self):
        # A credential-less local run must still produce an openable bundle to
        # test the assembly with; it just warns it is not distributable.
        self.assertIn("AD-HOC", self.script)
        self.assertIn('SIGN_IDENTITY="-"', self.script)


class TestEntitlementsAreMinimal(unittest.TestCase):
    def test_the_only_relaxation_is_the_one_sparkle_needs(self):
        plist = read(ENTITLEMENTS)
        self.assertIn(
            "com.apple.security.cs.disable-library-validation", plist)
        # Not sandboxed: a sandbox entitlement here would silently cut the app
        # off from the Python launcher it shells out to.
        self.assertNotIn("com.apple.security.app-sandbox", plist)

    def test_the_entitlements_carry_no_xml_comments(self):
        # codesign validates the --entitlements file with AMFIUnserializeXML, a
        # strict parser that rejects XML comments outright ("syntax error"),
        # failing the signing step on the runner — even though local ad-hoc
        # signing (CFPropertyList) parses comments fine. This exact comment block
        # broke the first v1.3.4 DMG build, so keep the file comment-free.
        self.assertNotIn("<!--", read(ENTITLEMENTS))

    def test_the_entitlements_are_a_valid_plist_with_only_that_key(self):
        import plistlib

        parsed = plistlib.loads(read(ENTITLEMENTS).encode("utf-8"))
        self.assertEqual(
            parsed, {"com.apple.security.cs.disable-library-validation": True})


class TestSwiftPicksTheRightUpdater(unittest.TestCase):
    def test_distribution_defaults_to_source_when_the_key_is_absent(self):
        swift = read(os.path.join(SRC, "Distribution.swift"))
        self.assertIn("?? .source", swift)
        self.assertIn('static let infoKey = "SMARTBARDistribution"', swift)

    def test_sparkle_only_starts_on_a_dmg_copy(self):
        swift = read(os.path.join(SRC, "SparkleUpdater.swift"))
        self.assertIn("guard Distribution.usesSparkle", swift)

    def test_the_menu_routes_dmg_checks_to_sparkle(self):
        swift = read(os.path.join(SRC, "AppOptionsMenu.swift"))
        self.assertIn("SparkleUpdater.shared.isActive", swift)
        self.assertIn("SparkleUpdater.shared.checkForUpdates()", swift)
        self.assertIn("updates.checkNow()", swift)

    def test_the_app_builds_the_updater_at_launch(self):
        swift = read(os.path.join(SRC, "AISmartbarApp.swift"))
        self.assertIn("SparkleUpdater.shared", swift)


class TestTheReleaseWorkflow(unittest.TestCase):
    def setUp(self):
        self.yaml = read(WORKFLOW)

    def test_it_fires_on_version_tags(self):
        self.assertIn('tags: ["v*"]', self.yaml)

    def test_it_runs_the_packager(self):
        self.assertIn("./install/package-dmg.sh", self.yaml)

    def test_it_uploads_the_dmg_and_the_appcast(self):
        self.assertIn("dist/AI_smartbar-*.dmg dist/appcast.xml", self.yaml)

    def test_the_temporary_keychain_is_always_torn_down(self):
        # Secrets are materialised on the runner; the teardown must run even
        # when the build fails, so it is guarded by if: always().
        self.assertIn("if: always()", self.yaml)
        self.assertIn("security delete-keychain", self.yaml)


class TestTheDmgBundlesTheBackend(unittest.TestCase):
    """A checkout install IS the backend; a DMG copy has no clone, so the
    packager ships bin/ai-smartbar + the smartbar package inside the app and the
    Swift side falls back to it, run under the user's cswap venv python."""

    def setUp(self):
        self.script = read(PKGDMG)

    def test_it_copies_the_launcher_and_package_into_the_bundle(self):
        self.assertIn("Contents/Resources/backend", self.script)
        self.assertIn("bin/ai-smartbar", self.script)
        self.assertIn('ditto "$REPO/smartbar"', self.script)

    def test_it_ships_source_not_compiled_caches(self):
        # __pycache__ is host-specific and ~3x the source size; excluding it
        # keeps the signed bundle small and reproducible.
        self.assertIn("__pycache__", self.script)

    def test_the_backend_is_in_place_before_the_signature_covers_it(self):
        # codesign seals Resources into CodeResources; a backend added after
        # signing would break the seal.
        self.assertLess(self.script.index("Contents/Resources/backend"),
                        self.script.index("--- sign, inside-out"))

    def test_the_checkout_install_ships_no_bundled_backend(self):
        # The checkout already has the repo; a second copy inside the app would
        # be dead weight and could drift.
        self.assertNotIn("Contents/Resources/backend", read(MACOS))

    def test_swift_falls_back_to_the_bundled_backend(self):
        swift = read(os.path.join(SRC, "PresenceStatus.swift"))
        self.assertIn("bundledBackendRoot", swift)
        self.assertIn('appendingPathComponent("backend")', swift)

    def test_the_bundled_backend_runs_under_an_explicit_interpreter(self):
        # A dragged-in copy cannot rely on `python3` being on PATH; the DMG
        # audience's pipx/uv claude-swap interpreter is used instead.
        launcher = read(os.path.join(SRC, "Launcher.swift"))
        self.assertIn("SMARTBAR_PYTHON", launcher)
        self.assertIn("venvPython", launcher)
        self.assertIn("bundledBackendRoot", launcher)


if __name__ == "__main__":
    unittest.main()
