// The updater for DMG-installed copies. A thin wrapper over Sparkle's stock
// controller — the feed URL and the public EdDSA key that authenticates every
// download both come from the bundle's Info.plist (SUFeedURL / SUPublicEDKey,
// written by install/package-dmg.sh), so there is almost nothing to configure
// here.
//
// Sparkle is linked into EVERY build (see Package.swift), but this type only
// ever builds a live updater when the copy was installed from a DMG. A
// checkout install returns a dormant instance: its `isActive` is false and
// `checkForUpdates()` does nothing, because that copy updates itself with git
// through UpdateStatus.swift instead. Distribution.usesSparkle is the switch.
import Foundation
import Sparkle

@MainActor
final class SparkleUpdater {
    static let shared = SparkleUpdater()

    // nil on a checkout install — nothing started, nothing to check.
    private let controller: SPUStandardUpdaterController?

    private init() {
        guard Distribution.usesSparkle else {
            controller = nil
            return
        }
        // startingUpdater: true begins Sparkle's own background schedule
        // (SUScheduledCheckInterval / SUEnableAutomaticChecks from Info.plist),
        // so a DMG copy finds new releases without the user opening the menu —
        // the same "announce an update on its own" behaviour a checkout gets
        // from the 6-hourly launchd job.
        controller = SPUStandardUpdaterController(
            startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil)
    }

    /// True only for a DMG copy with a live updater. The menu uses it to pick
    /// which updater the "Check for Updates" command drives.
    var isActive: Bool { controller != nil }

    /// User-initiated check. Shows Sparkle's standard progress/confirm UI.
    func checkForUpdates() {
        controller?.updater.checkForUpdates()
    }
}
