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
        // Mirrors install/macos-update.sh's AGENT_PATH — /usr/sbin:/sbin
        // included: sysctl lives there, and without it the probe reported
        // a 0 GB machine ("34.8 / 0 GB · 0%").
        environment["PATH"] = [home + "/.local/bin", "/opt/homebrew/bin",
                               "/usr/local/bin", "/usr/bin", "/bin",
                               "/usr/sbin", "/sbin"]
            .joined(separator: ":")
        return environment
    }

    /// The interpreter to run a BUNDLED launcher with. A checkout install runs
    /// bin/ai-smartbar directly and lets its `#!/usr/bin/env python3` shebang
    /// find the developer's python3 — unchanged. A DMG copy has no such
    /// guarantee, but its audience installed claude-swap via pipx/uv, so the
    /// cswap venv interpreter is present AND can import claude_swap — exactly
    /// what the launcher-backed subcommands need. nil ⇒ run the launcher itself.
    static func python() -> String? {
        let env = ProcessInfo.processInfo.environment
        if let override = env["SMARTBAR_PYTHON"], !override.isEmpty {
            return override
        }
        return CswapClient.venvPython()
    }

    /// How to exec `bin/ai-smartbar <args>`: (executable, arguments), or nil if
    /// no launcher can be found. A checkout launcher runs itself via its
    /// shebang; a launcher living inside the app bundle is run through an
    /// explicit interpreter (see python()), since a dragged-in copy cannot rely
    /// on python3 being on PATH.
    static func invocation(_ args: [String]) -> (URL, [String])? {
        guard let launcher = path() else { return nil }
        if let bundled = PresenceStatus.bundledBackendRoot(),
           launcher == bundled + "/bin/ai-smartbar",
           let python = python() {
            return (URL(fileURLWithPath: python), [launcher] + args)
        }
        return (URL(fileURLWithPath: launcher), args)
    }

    /// A configured (but not yet started) Process, or nil if the launcher is
    /// missing. The caller wires stdout/stderr and runs it — used both for
    /// one-shot reads and for the long-lived `--sysmon --stream`.
    static func process(_ args: [String]) -> Process? {
        guard let (executable, arguments) = invocation(args) else { return nil }
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
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
