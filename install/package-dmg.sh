#!/usr/bin/env bash
# Build a signed, notarized DMG of the native SwiftUI app — the install path
# for people who drag an app into /Applications rather than clone the repo and
# run install/macos-swift.sh. The checkout install and this one are different
# audiences with different updaters (git vs Sparkle); see Distribution.swift.
#
# It is driven entirely by environment variables so the same script runs on a
# maintainer's Mac and, unchanged, in GitHub Actions (.github/workflows/
# release-dmg.yml). Absent credentials degrade, they do not fail: with no
# signing identity it builds an AD-HOC DMG you can only test locally (Gatekeeper
# will reject it on another Mac), and says so loudly.
#
#   MACOS_SIGN_IDENTITY   "Developer ID Application: NAME (TEAMID)" — the cert
#                         to sign with. Empty ⇒ ad-hoc, and notarization is
#                         skipped (there is nothing Apple will notarize). The
#                         Team ID is already inside this string, so notarytool
#                         (API-key auth) needs no separate team argument.
#   AC_API_KEY_ID         App Store Connect API key id  ┐ all three present ⇒
#   AC_API_ISSUER_ID      …its issuer id                ├ notarize + staple.
#   AC_API_KEY_PATH       …path to the AuthKey_*.p8     ┘ any missing ⇒ skip.
#   SPARKLE_ED_KEY_FILE   Sparkle EdDSA private key file. Present ⇒ also write
#                         dist/appcast.xml signed for the auto-updater.
#   SPARKLE_FEED_URL      Overrides the appcast URL baked into the bundle
#                         (default: this repo's releases/latest/download).
#
#   ./install/package-dmg.sh            # full run (needs the vars above)
#   ./install/package-dmg.sh --adhoc    # force ad-hoc, skip notarize (local)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="$REPO/macos-swift"
DIST="$REPO/dist"
ICON_SRC="$REPO/assets/ai-smartbar.png"
ENTITLEMENTS="$REPO/install/entitlements.plist"
cd "$REPO"

# The Sparkle PUBLIC key. Safe to commit — it only lets a client VERIFY that an
# update was signed by the matching private key (a GitHub secret / offline
# backup, never in the repo). Generated once with Sparkle's generate_keys; if
# it is ever rotated, change it here and in every shipped build's Info.plist.
SPARKLE_PUBLIC_ED_KEY="UYR7p9dQV1/UKqLeFVOIWzJv1xyrynGtwS6EWZOOMnY="

BUNDLE_ID="com.ductran.ai-smartbar"
APP_NAME="AI_smartbar.app"
VOL_NAME="AI smartbar"

ADHOC=0
[[ "${1:-}" == "--adhoc" ]] && ADHOC=1

# --- the one canonical version, exactly as install/release.sh reads it -------
VERSION="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$REPO/smartbar/__init__.py")"
[[ -n "$VERSION" ]] || { echo "cannot read __version__" >&2; exit 1; }
BUILD_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)"

# --- where the appcast enclosure and the feed will live ---------------------
# Derived from origin so a fork ships its own URLs with no edits. Same accepted
# URL shapes as install/release.sh's github_repo_from_url.
gh_repo() {
  local url; url="$(git -C "$REPO" remote get-url origin 2>/dev/null || true)"
  case "$url" in
    https://github.com/*) url="${url#https://github.com/}" ;;
    git@github.com:*)     url="${url#git@github.com:}" ;;
    ssh://git@github.com/*) url="${url#ssh://git@github.com/}" ;;
    *) return 1 ;;
  esac
  printf '%s\n' "${url%.git}"
}
GH_REPO="$(gh_repo || true)"
DMG_BASENAME="AI_smartbar-${VERSION}.dmg"
if [[ -n "$GH_REPO" ]]; then
  DMG_URL="https://github.com/${GH_REPO}/releases/download/v${VERSION}/${DMG_BASENAME}"
  DEFAULT_FEED="https://github.com/${GH_REPO}/releases/latest/download/appcast.xml"
else
  DMG_URL="${DMG_BASENAME}"
  DEFAULT_FEED="appcast.xml"
fi
SPARKLE_FEED_URL="${SPARKLE_FEED_URL:-$DEFAULT_FEED}"

# --- resolve the signing posture up front, so the log says what will happen --
SIGN_IDENTITY="${MACOS_SIGN_IDENTITY:-}"
if [[ "$ADHOC" == "1" ]]; then SIGN_IDENTITY=""; fi
NOTARIZE=0
if [[ -n "$SIGN_IDENTITY" && -n "${AC_API_KEY_ID:-}" \
      && -n "${AC_API_ISSUER_ID:-}" && -n "${AC_API_KEY_PATH:-}" ]]; then
  NOTARIZE=1
fi
if [[ -z "$SIGN_IDENTITY" ]]; then
  echo "WARNING: no MACOS_SIGN_IDENTITY — building an AD-HOC DMG. It is for" >&2
  echo "         local testing only; Gatekeeper will reject it elsewhere." >&2
  SIGN_IDENTITY="-"
elif [[ "$NOTARIZE" == "0" ]]; then
  echo "WARNING: signing with '$SIGN_IDENTITY' but NOT notarizing (AC_* unset)." >&2
  echo "         Gatekeeper will still quarantine the download." >&2
fi

# --- build ------------------------------------------------------------------
command -v swift >/dev/null || { echo "Swift toolchain missing" >&2; exit 1; }
echo "Building AI_smartbar $VERSION (release)…"
swift build -c release --package-path "$PKG"
BUILD_DIR="$(swift build -c release --package-path "$PKG" --show-bin-path)"
BIN="$BUILD_DIR/AISmartbar"
[[ -x "$BIN" ]] || { echo "Build produced no binary at $BIN" >&2; exit 1; }
[[ -d "$BUILD_DIR/Sparkle.framework" ]] \
  || { echo "Sparkle.framework not found beside the binary" >&2; exit 1; }

# --- assemble a fresh bundle (no running-app inode games needed here) --------
rm -rf "$DIST"
APP_DIR="$DIST/$APP_NAME"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Frameworks" \
         "$APP_DIR/Contents/Resources"
cp "$BIN" "$APP_DIR/Contents/MacOS/AISmartbar"
ditto "$BUILD_DIR/Sparkle.framework" \
      "$APP_DIR/Contents/Frameworks/Sparkle.framework"

# The app icon — same iconset the checkout installer builds, from the same
# committed asset, so the two installs are visually identical. (Kept in step
# with install/macos-swift.sh by tests/test_release_dmg.py.)
if [[ -f "$ICON_SRC" ]] && command -v sips >/dev/null \
   && command -v iconutil >/dev/null; then
  ICONSET="$DIST/AppIcon.iconset"
  rm -rf "$ICONSET"; mkdir -p "$ICONSET"
  for spec in 16:16 16:32 32:32 32:64 128:128 128:256 256:256 256:512 \
              512:512 512:1024; do
    nominal="${spec%%:*}"; pixels="${spec##*:}"; suffix=""
    [[ "$pixels" == "$nominal" ]] || suffix="@2x"
    sips -z "$pixels" "$pixels" "$ICON_SRC" \
         --out "$ICONSET/icon_${nominal}x${nominal}${suffix}.png" \
         >/dev/null 2>&1 || true
  done
  iconutil -c icns "$ICONSET" -o "$APP_DIR/Contents/Resources/AppIcon.icns" \
    || echo "WARNING: could not build the app icon." >&2
  rm -rf "$ICONSET"
else
  echo "WARNING: $ICON_SRC missing — the DMG app will use a generic icon." >&2
fi

# Info.plist. The DMG-only keys are what set this copy apart from a checkout:
#   SMARTBARDistribution=dmg  → the app starts Sparkle, not the git updater.
#   SUFeedURL / SUPublicEDKey → where Sparkle looks and how it verifies.
#   CFBundleVersion           → what Sparkle compares against the appcast's
#                               sparkle:version; must be present and monotonic.
cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleName</key><string>AI smartbar</string>
  <key>CFBundleDisplayName</key><string>AI smartbar</string>
  <key>CFBundleExecutable</key><string>AISmartbar</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>SMARTBARBuildSHA</key><string>${BUILD_SHA}</string>
  <key>SMARTBARDistribution</key><string>dmg</string>
  <key>SUFeedURL</key><string>${SPARKLE_FEED_URL}</string>
  <key>SUPublicEDKey</key><string>${SPARKLE_PUBLIC_ED_KEY}</string>
  <key>SUEnableAutomaticChecks</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
EOF

# --- bundle the Python backend so a DMG copy works without a checkout -------
# The app is a front-end: the System tab, the OpenAI card and account removal
# shell out to bin/ai-smartbar, and the account/usage cards to the user's own
# cswap. A dragged-in copy has no clone for the launcher to live in, so ship one
# inside the app — PresenceStatus.repoRoot() falls back to it, and Launcher runs
# it with the cswap venv python (the DMG audience has claude-swap installed).
# Copy the whole package for simplicity; the invoked subcommands lazy-import
# only stdlib + claude_swap, so the heavy UI deps (rumps/PIL) are never loaded.
# Must happen BEFORE signing, which seals Resources into the code signature.
BACKEND="$APP_DIR/Contents/Resources/backend"
mkdir -p "$BACKEND/bin"
cp "$REPO/bin/ai-smartbar" "$BACKEND/bin/ai-smartbar"
chmod +x "$BACKEND/bin/ai-smartbar"
ditto "$REPO/smartbar" "$BACKEND/smartbar"
# Ship source only — compiled caches are host-specific and would just bloat the
# signed bundle (the package is ~600 KB of source, ~2 MB of __pycache__).
find "$BACKEND" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
echo "Bundled backend: $(du -sh "$BACKEND" | cut -f1)"

# --- sign, inside-out (Apple discourages --deep for distribution) -----------
# Sparkle ships nested executables that each need their own hardened-runtime
# signature; sign the deepest first, then the framework, then the app. The app
# alone carries the entitlements file (the nested helpers inherit runtime).
FW="$APP_DIR/Contents/Frameworks/Sparkle.framework"
sign() { codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$@"; }
if [[ "$SIGN_IDENTITY" == "-" ]]; then
  # Ad-hoc has no secure timestamp and no runtime hardening to prove; keep it
  # simple so a credential-less local run still produces a openable bundle.
  sign() { codesign --force --sign - "$@"; }
fi
echo "Signing with: $SIGN_IDENTITY"
# Order matters. Versions/B is the physical path behind Versions/Current.
sign "$FW/Versions/B/XPCServices/Downloader.xpc" 2>/dev/null || true
sign "$FW/Versions/B/XPCServices/Installer.xpc"  2>/dev/null || true
sign "$FW/Versions/B/Autoupdate"
sign "$FW/Versions/B/Updater.app"
sign "$FW"
if [[ "$SIGN_IDENTITY" == "-" ]]; then
  codesign --force --sign - "$APP_DIR/Contents/MacOS/AISmartbar"
  codesign --force --sign - "$APP_DIR"
else
  codesign --force --options runtime --timestamp \
           --sign "$SIGN_IDENTITY" "$APP_DIR/Contents/MacOS/AISmartbar"
  codesign --force --options runtime --timestamp \
           --entitlements "$ENTITLEMENTS" \
           --sign "$SIGN_IDENTITY" "$APP_DIR"
fi
codesign --verify --deep --strict --verbose=2 "$APP_DIR" 2>&1 | tail -2

# --- notarize the app, then staple it (so a copy dragged OUT is trusted) ----
notarize_and_staple() {
  local target="$1"
  echo "Notarizing $(basename "$target")… (this waits on Apple)"
  xcrun notarytool submit "$target" \
    --key "$AC_API_KEY_PATH" --key-id "$AC_API_KEY_ID" \
    --issuer "$AC_API_ISSUER_ID" --wait
  xcrun stapler staple "$target"
}
if [[ "$NOTARIZE" == "1" ]]; then
  # notarytool cannot take a bare .app — wrap it in a zip to SUBMIT, but staple
  # the ticket back onto the .app (stapler cannot staple a zip). This is what
  # makes a copy dragged straight out of the DMG trusted offline.
  APP_ZIP="$DIST/AI_smartbar-app.zip"
  ditto -c -k --keepParent "$APP_DIR" "$APP_ZIP"
  echo "Notarizing the app… (this waits on Apple)"
  xcrun notarytool submit "$APP_ZIP" --key "$AC_API_KEY_PATH" \
    --key-id "$AC_API_KEY_ID" --issuer "$AC_API_ISSUER_ID" --wait
  xcrun stapler staple "$APP_DIR"
  rm -f "$APP_ZIP"
fi

# --- build the DMG: the app + a drop target, plus a volume icon -------------
STAGE="$DIST/stage"
rm -rf "$STAGE"; mkdir -p "$STAGE"
ditto "$APP_DIR" "$STAGE/$APP_NAME"
ln -s /Applications "$STAGE/Applications"
# Give the mounted volume the app's own icon (needs the magic filename + the
# custom-icon bit). Cosmetic, never fatal.
if [[ -f "$APP_DIR/Contents/Resources/AppIcon.icns" ]]; then
  cp "$APP_DIR/Contents/Resources/AppIcon.icns" "$STAGE/.VolumeIcon.icns"
  command -v SetFile >/dev/null && SetFile -a C "$STAGE" 2>/dev/null || true
fi
DMG="$DIST/$DMG_BASENAME"
rm -f "$DMG"
hdiutil create -volname "$VOL_NAME" -srcfolder "$STAGE" \
  -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
echo "Built $DMG"

# --- sign + notarize + staple the DMG itself --------------------------------
if [[ "$SIGN_IDENTITY" != "-" ]]; then
  codesign --force --sign "$SIGN_IDENTITY" --timestamp "$DMG"
fi
if [[ "$NOTARIZE" == "1" ]]; then
  notarize_and_staple "$DMG"
fi

# --- appcast for the auto-updater (only with the Sparkle key) ---------------
if [[ -n "${SPARKLE_ED_KEY_FILE:-}" && -f "${SPARKLE_ED_KEY_FILE}" ]]; then
  SIGN_UPDATE="$(find "$PKG/.build/artifacts" -name sign_update -type f 2>/dev/null | head -1)"
  if [[ -z "$SIGN_UPDATE" ]]; then
    echo "WARNING: Sparkle's sign_update tool not found — no appcast written." >&2
  else
    # Emits: sparkle:edSignature="…" length="…"
    SIG_ATTRS="$("$SIGN_UPDATE" "$DMG" --ed-key-file "$SPARKLE_ED_KEY_FILE")"
    PUBDATE="$(LC_ALL=C date -u '+%a, %d %b %Y %H:%M:%S +0000')"
    cat > "$DIST/appcast.xml" <<EOF
<?xml version="1.0" standalone="yes"?>
<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">
  <channel>
    <title>AI smartbar</title>
    <item>
      <title>Version ${VERSION}</title>
      <pubDate>${PUBDATE}</pubDate>
      <sparkle:version>${VERSION}</sparkle:version>
      <sparkle:shortVersionString>${VERSION}</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>13.0</sparkle:minimumSystemVersion>
      <enclosure url="${DMG_URL}" ${SIG_ATTRS} type="application/x-apple-diskimage" />
    </item>
  </channel>
</rss>
EOF
    echo "Wrote $DIST/appcast.xml"
  fi
else
  echo "No SPARKLE_ED_KEY_FILE — skipping appcast (DMG will not auto-update)."
fi

echo "Done. Artifacts in $DIST/"
ls -1 "$DIST"
