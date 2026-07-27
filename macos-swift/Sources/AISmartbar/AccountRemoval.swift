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
        guard let root = PresenceStatus.repoRoot() else {
            return "smartbar checkout not found"
        }
        let launcher = root + "/bin/ai-smartbar"
        guard FileManager.default.isExecutableFile(atPath: launcher) else {
            return "smartbar launcher not found"
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: launcher)
        process.arguments = ["--remove-account", "\(provider):\(identifier)"]
        // launchd hands a GUI app a bare PATH, and the launcher's shebang
        // has to be able to find python3 — same treatment as OpenAIStatus.
        var environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        environment["PATH"] = [home + "/.local/bin", "/opt/homebrew/bin",
                               "/usr/local/bin", "/usr/bin", "/bin"]
            .joined(separator: ":")
        process.environment = environment
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch {
            return "could not run the launcher: \(error.localizedDescription)"
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard let raw = try? JSONSerialization.jsonObject(with: data)
                as? [String: Any] else {
            return "the launcher returned no answer"
        }
        if (raw["ok"] as? Bool) == true { return nil }
        let detail = raw["error"] as? String ?? ""
        return detail.isEmpty ? "removal failed" : detail
    }
}
