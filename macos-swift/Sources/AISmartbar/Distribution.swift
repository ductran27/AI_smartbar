// How THIS copy of the app was installed — the fork in the road for updates.
//
// Two channels reach a Mac and they cannot share an updater:
//
//   * A CHECKOUT (install/macos-swift.sh) keeps a git clone and rebuilds
//     itself in place — smartbar/update_runner.py pulls the tag and re-runs
//     the installer. UpdateStatus.swift drives that from the popover.
//   * A DMG a user dragged into /Applications has no checkout to pull, so it
//     updates through Sparkle's signed appcast instead (SparkleUpdater.swift).
//
// install/package-dmg.sh stamps `SMARTBARDistribution=dmg` into the bundle it
// signs; the checkout installer writes no such key, so ABSENT means source.
// Defaulting the absent case to .source is deliberate: every existing install
// in the field predates this key, and they are all checkouts.
import Foundation

enum Distribution: String {
    case source
    case dmg

    /// Info.plist key written by install/package-dmg.sh (only). Read once at
    /// launch — a bundle's distribution cannot change while it runs.
    static let infoKey = "SMARTBARDistribution"

    static let current: Distribution = {
        let raw = Bundle.main.object(forInfoDictionaryKey: infoKey) as? String
        return raw.flatMap(Distribution.init(rawValue:)) ?? .source
    }()

    /// Whether Sparkle owns updates for this copy. The Sparkle framework is
    /// linked into every build (the app will not launch without it once it is
    /// a link dependency), but it is only ever *started* here — a checkout
    /// install must keep using the git updater, not Sparkle.
    static var usesSparkle: Bool { current == .dmg }
}
