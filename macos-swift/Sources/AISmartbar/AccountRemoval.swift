// Account removal runs through the launcher (`ai-smartbar
// --remove-account provider:id`) for BOTH providers. ONE SHARED ANSWER,
// NOT A SWIFT PORT: the active-account guard and each provider's
// semantics live once in smartbar/core/account_removal.py (unit-tested),
// and Swift only shows the answer — the same rule as the OpenAI tab.
import Foundation

enum AccountRemoval {
    /// nil on success, else a short user-facing reason (it lands in the
    /// popover's error line).
    static func remove(provider: String, identifier: String) -> String? {
        // Through Launcher, like the four other call sites — so the
        // launchd-stripped PATH fix (which /usr/sbin:/sbin belongs to) and
        // the JSON handling live in one place and cannot drift again. This
        // hand-rolled its own Process/PATH before, with a PATH that had
        // already fallen a step behind Launcher's.
        guard let raw = Launcher.json(["--remove-account",
                                       "\(provider):\(identifier)"]) else {
            return "could not run the launcher"
        }
        if (raw["ok"] as? Bool) == true { return nil }
        let detail = raw["error"] as? String ?? ""
        return detail.isEmpty ? "removal failed" : detail
    }
}
