# Signed DMG release — setup and runbook

The native macOS app ships two ways, for two audiences:

- **Checkout install** (`install/macos-swift.sh`) — clones the repo, builds
  locally, and self-updates with git. This is unchanged.
- **DMG install** (`install/package-dmg.sh`) — a signed, notarized disk image a
  user drags into `/Applications`, which then self-updates through **Sparkle**.

The two are the *same app* (same bundle id, same icon). Which updater a copy
uses is decided at launch from its `SMARTBARDistribution` Info.plist key — see
`macos-swift/Sources/AISmartbar/Distribution.swift`. A checkout copy never
starts Sparkle; a DMG copy never touches the git updater.

Once the one-time setup below is done, **every `install/release.sh` release
automatically builds and attaches a DMG** — the `release-dmg` workflow fires on
the `vX.Y.Z` tag. You do not run anything by hand per release.

---

## One-time setup (Phase 0)

You need the paid **Apple Developer Program** (the free tier cannot issue a
Developer ID certificate). Everything here is done once and stored as GitHub
repository secrets; nothing sensitive lives in the repo or on your Mac after.

### 1. Developer ID Application certificate → `.p12`

1. Xcode → **Settings → Accounts** → select your team → **Manage
   Certificates…** → **+** → **Developer ID Application**. (Not "Apple
   Distribution", which is App Store only.)
2. In **Keychain Access**, find `Developer ID Application: <name> (<TEAMID>)`,
   expand it, select **both** the certificate and its private key, right-click →
   **Export 2 items…** → save a `.p12` and set an export password.
3. Base64 it for the secret:
   ```bash
   base64 -i DeveloperID.p12 | pbcopy      # → MACOS_CERT_P12_BASE64
   ```

### 2. App Store Connect API key (for notarization)

`notarytool` authenticates with an API key, not your Apple ID password.

1. [App Store Connect](https://appstoreconnect.apple.com) → **Users and
   Access → Integrations → App Store Connect API** → generate a key with the
   **Developer** role.
2. Note the **Key ID** and **Issuer ID**, and download the `AuthKey_XXXX.p8`
   (downloadable **once**).
   ```bash
   base64 -i AuthKey_XXXX.p8 | pbcopy      # → AC_API_KEY_P8_BASE64
   ```

### 3. Sparkle signing key

Already generated (its **public** half is committed in `install/package-dmg.sh`
as `SPARKLE_PUBLIC_ED_KEY`). You only need to place the **private** half in a
secret and back it up — if it is ever lost, no future build can push an update
to existing DMG users.

To regenerate from scratch instead (only if you don't have the private key):
```bash
swift build -c release --package-path macos-swift        # fetches Sparkle
TOOLS="$(find macos-swift/.build/artifacts -name generate_keys | head -1 | xargs dirname)"
"$TOOLS/generate_keys"                 # prints the public key → package-dmg.sh
"$TOOLS/generate_keys" -x sparkle_key  # exports the private key → the secret
```

### 4. Add the GitHub repository secrets

Settings → Secrets and variables → Actions → **New repository secret**, or:

```bash
gh secret set MACOS_CERT_P12_BASE64  < <(base64 -i DeveloperID.p12)
gh secret set MACOS_CERT_PASSWORD    # the .p12 export password
gh secret set AC_API_KEY_ID          # App Store Connect key id
gh secret set AC_API_ISSUER_ID       # its issuer id
gh secret set AC_API_KEY_P8_BASE64   < <(base64 -i AuthKey_XXXX.p8)
gh secret set SPARKLE_ED_PRIVATE_KEY < sparkle_key
```

Then **delete the local `.p12`, `.p8` and `sparkle_key` files** (keep an
offline backup of the Sparkle key somewhere safe).

---

## How a release produces a DMG

1. `install/release.sh <bump>` bumps the version, gates on CI, tags `vX.Y.Z`.
2. The tag push triggers `.github/workflows/release-dmg.yml`, which on a macOS
   runner: imports the certificate into a throwaway keychain, runs
   `install/package-dmg.sh`, and uploads `AI_smartbar-X.Y.Z.dmg` +
   `appcast.xml` to the release.
3. `package-dmg.sh` builds the app, embeds Sparkle, signs inside-out with the
   hardened runtime + `install/entitlements.plist`, notarizes and staples both
   the app and the DMG, then signs the appcast with the Sparkle key.
4. Existing DMG users' apps read `appcast.xml` from
   `releases/latest/download/appcast.xml` and offer the update.

A failure here never affects the release itself — the tag stands and checkout
installs keep updating with git. Re-run just the DMG via **Actions →
release-dmg → Run workflow** with the tag.

## Testing the packaging locally (no credentials)

```bash
./install/package-dmg.sh --adhoc      # ad-hoc signed, no notarization
```

Produces `dist/AI_smartbar-<version>.dmg` you can mount and open on **this**
Mac to check the layout and that the app launches. Gatekeeper will reject an
ad-hoc DMG on any *other* Mac — that is expected; only the notarized CI build
is distributable.

## ⚠️ Known gap: the DMG app needs the Python backend

The app is a front-end over the Python launcher (`bin/ai-smartbar`) and
`claude-swap` (`cswap`). A DMG copy with no checkout has nothing to read from
(`repoRoot()` returns nil), so it will show empty data. Making the DMG a fully
self-contained install — bundling a Python runtime + `smartbar/`, and handling
`cswap` — is separate work tracked outside this pipeline. Until then the DMG is
the right *distribution and update* mechanism, but the app inside still expects
the backend to be present.
