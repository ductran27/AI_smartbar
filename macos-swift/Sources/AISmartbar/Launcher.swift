// One place to run `bin/ai-smartbar <args>` and read its JSON. Before this,
// the same ~25 lines of Process setup — resolve the checkout, put python3 on
// a launchd-stripped PATH, capture stdout, parse JSON — were copied into
// OpenAIStatus, PlanStatus, PresenceStatus and AccountRemoval. The System
// tab would have been a fifth copy; factoring it here instead keeps the PATH
// fix and the "one shared answer" boundary in a single spot. Swift still maps
// nothing: these helpers only run the launcher and hand back its decoded
// JSON, exactly as the inlined copies did.
import Foundation

enum Launcher {
    /// The installed/dev checkout's launcher, or nil when it cannot be found
    /// or is not executable (the callers keep their last-good data then).
    static func path() -> String? {
        guard let root = PresenceStatus.repoRoot() else { return nil }
        let launcher = root + "/bin/ai-smartbar"
        return FileManager.default.isExecutableFile(atPath: launcher)
            ? launcher : nil
    }

    /// launchd hands a GUI app a bare PATH, and the launcher's shebang has to
    /// find python3 — the same prepend every inlined copy used.
    static func environment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        environment["PATH"] = [home + "/.local/bin", "/opt/homebrew/bin",
                               "/usr/local/bin", "/usr/bin", "/bin"]
            .joined(separator: ":")
        return environment
    }

    /// A configured (but not yet started) Process, or nil if the launcher is
    /// missing. The caller wires stdout/stderr and runs it — used both for
    /// one-shot reads and for the long-lived `--sysmon --stream`.
    static func process(_ args: [String]) -> Process? {
        guard let launcher = path() else { return nil }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: launcher)
        process.arguments = args
        process.environment = environment()
        return process
    }

    /// Run the launcher and return its raw stdout, or nil on any failure
    /// (missing checkout, launch error).
    static func run(_ args: [String]) -> Data? {
        guard let process = process(args) else { return nil }
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return data
    }

    /// Run the launcher and decode its stdout as a JSON object, or nil.
    static func json(_ args: [String]) -> [String: Any]? {
        guard let data = run(args) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data))
            as? [String: Any]
    }

    /// Run the launcher and decode its stdout into a Decodable type, or nil.
    static func decode<T: Decodable>(_ type: T.Type,
                                     _ args: [String]) -> T? {
        guard let data = run(args) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }
}
