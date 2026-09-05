// Posts the app's user-facing notifications (usage alerts, update-ready).
//
// macOS credits a notification to the BUNDLE of the process that posted it,
// and its icon follows that credit. The old path spawned /usr/bin/osascript,
// which is not an app bundle, so every banner arrived wearing Script Editor's
// generic folded-page icon instead of this app's.
//
// UNUserNotificationCenter posts under THIS bundle (com.ductran.ai-smartbar) →
// the app's own icon — but only once the bundle carries a real Developer ID
// signature. Measured on macOS 26.6:
//
//   * ad-hoc (`codesign -s -`): requestAuthorization returns
//     "Notifications are not allowed for this application" immediately, no
//     prompt. UN is unusable.
//   * Developer ID (notarization NOT required — an "Unnotarized Developer ID"
//     bundle in ~/Applications is enough): requestAuthorization prompts, and
//     the banner shows the app icon.
//
// So this tries UN and, whenever UN is unavailable (the ad-hoc source build)
// or later refuses, falls back to osascript — a wrong-icon banner still beats
// no banner. install/macos-swift.sh Developer-ID-signs the source install when
// SMARTBAR_SIGN_IDENTITY is set; the DMG build is signed and notarized in CI.
// Linux/Windows carry the real name and logo unconditionally — see
// smartbar/update_runner.py and smartbar/core/notify.py.
import Foundation
import UserNotifications

final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    static let shared = Notifier()

    // Written once on the authorization callback, read from any posting
    // thread. Guarded because those are different threads; a lost race only
    // ever costs one notification the fallback path, never correctness.
    private let lock = NSLock()
    private var _canUseUserNotifications = false
    private var canUseUserNotifications: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _canUseUserNotifications }
        set { lock.lock(); _canUseUserNotifications = newValue; lock.unlock() }
    }

    /// Call once at launch. Sets the delegate (so banners present even while
    /// this agent is the active app) and asks for authorization — which
    /// prompts on a Developer-ID-signed build and fails fast on an ad-hoc one.
    func configure() {
        // UNUserNotificationCenter.current() traps when the process has no
        // bundle identifier (a bare `swift run` of the executable). The real
        // install is always a bundle; guard the dev case into the fallback.
        guard Bundle.main.bundleIdentifier != nil else { return }
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        // requestAuthorization only prompts when the status is notDetermined;
        // the real gate is the resulting settings, which also reflect a later
        // toggle in System Settings > Notifications (picked up on relaunch).
        center.requestAuthorization(options: [.alert]) { [weak self] _, _ in
            self?.refreshAuthorization()
        }
        refreshAuthorization()
    }

    private func refreshAuthorization() {
        UNUserNotificationCenter.current().getNotificationSettings { [weak self] settings in
            self?.canUseUserNotifications =
                settings.authorizationStatus == .authorized
                || settings.authorizationStatus == .provisional
        }
    }

    /// nonisolated-friendly: safe to call from any thread.
    func post(title: String, body: String) {
        guard canUseUserNotifications else {
            postViaOsascript(title: title, body: body)
            return
        }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let request = UNNotificationRequest(
            identifier: UUID().uuidString, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { [weak self] error in
            // A revocation between launch and now (user turned notifications
            // off) surfaces here — fall back so the message is never dropped.
            if error != nil { self?.postViaOsascript(title: title, body: body) }
        }
    }

    private func postViaOsascript(title: String, body: String) {
        // Backslashes are not escaped: the callers' strings carry none, and
        // the pre-existing path never did either. Only the quote that would
        // close the AppleScript string early is neutralised.
        let escapedTitle = title.replacingOccurrences(of: "\"", with: "\\\"")
        let escapedBody = body.replacingOccurrences(of: "\"", with: "\\\"")
        let script = "display notification \"\(escapedBody)\" with title \"\(escapedTitle)\""
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", script]
        try? process.run()
    }

    // Present banners even when THIS app is frontmost (popover open, or just
    // after the ⌃⌥A hotkey). Without this, macOS withholds a foreground app's
    // own banners, so an update-ready alert raised while the popover is open
    // would never show. Silent (no .sound), matching the osascript path.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler:
            @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner])
    }
}
