// Thin Process wrapper around the claude-swap CLI — the data engine.
// Foundation-only; mirrors smartbar/core/cswap.py.
import Foundation

enum CswapError: Error, CustomStringConvertible {
    case notFound
    case failed(Int32, String)
    case badJSON(String)

    var description: String {
        switch self {
        case .notFound:
            return "cswap not found — install claude-swap (pipx install claude-swap)"
        case .failed(let code, let detail):
            return "cswap failed (rc=\(code)): \(detail)"
        case .badJSON(let detail):
            return "cswap returned invalid JSON: \(detail)"
        }
    }
}

enum CswapClient {
    static let timeoutSeconds: Double = 30
    static let primerTimeoutSeconds: Double = 25

    /// Force-freshens claude-swap's usage store before a list, via its own
    /// auto-engine collector convention (explicit fetch set → the store's
    /// atomic reserve() uses stale-OR-plan-due eligibility — the sanctioned
    /// way to beat the 3-min serve TTL and harvest urgent 60s plans). A
    /// fresh-and-not-yet-due account is still served from the store, so the
    /// per-token API budget is preserved by construction.
    /// Keep in sync with smartbar/core/cswap.py PRIMER_CODE.
    static let primerCode = """
    import sys
    try:
        from claude_swap.switcher import ClaudeAccountSwitcher
        switcher = ClaudeAccountSwitcher()
        numbers = {a.number for a in switcher.accounts_snapshot(fetch=set()).accounts}
        if numbers:
            switcher.accounts_snapshot(fetch=numbers)
    except Exception as exc:
        sys.stderr.write("primer: %s\\n" % exc)
        sys.exit(1)
    """

    /// launchd-launched GUI apps get a bare PATH, so resolve explicitly.
    static func binaryPath() -> String? {
        let env = ProcessInfo.processInfo.environment
        if let override = env["SMARTBAR_CSWAP"], !override.isEmpty {
            return override
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            home + "/.local/bin/cswap",
            "/opt/homebrew/bin/cswap",
            "/usr/local/bin/cswap",
        ]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    @discardableResult
    static func run(_ arguments: [String]) throws -> Data {
        guard let binary = binaryPath() else { throw CswapError.notFound }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: binary)
        process.arguments = arguments
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe
        try process.run()

        let killTimer = DispatchWorkItem {
            if process.isRunning { process.terminate() }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeoutSeconds,
                                          execute: killTimer)
        process.waitUntilExit()
        killTimer.cancel()

        let outData = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0 else {
            let errData = stderrPipe.fileHandleForReading.readDataToEndOfFile()
            let raw = errData.isEmpty ? outData : errData
            let detail = String(data: raw, encoding: .utf8) ?? ""
            throw CswapError.failed(process.terminationStatus,
                                    String(detail.prefix(200)))
        }
        return outData
    }

    /// The pipx venv interpreter that can import claude_swap, parsed from
    /// the cswap launcher's exec line; nil (compiled binary, mock, moved
    /// venv) just disables the primer.
    static func venvPython() -> String? {
        let env = ProcessInfo.processInfo.environment
        if let override = env["SMARTBAR_CSWAP_PYTHON"], !override.isEmpty {
            return override
        }
        guard let binary = binaryPath(),
              let handle = FileHandle(forReadingAtPath: binary),
              let head = String(data: handle.readData(ofLength: 512),
                                encoding: .utf8)
        else { return nil }
        defer { try? handle.close() }
        guard let match = head.range(of: #"'(/[^']*/bin/python[^']*)'"#,
                                     options: .regularExpression) else {
            return nil
        }
        let path = String(head[match].dropFirst().dropLast())
        return FileManager.default.isExecutableFile(atPath: path) ? path : nil
    }

    /// Best-effort store freshen; failures are silent (the follow-up list
    /// serves last-good data regardless).
    @discardableResult
    static func primeFresh() -> Bool {
        guard let python = venvPython() else { return false }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-c", primerCode]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            return false
        }
        let killTimer = DispatchWorkItem {
            if process.isRunning { process.terminate() }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + primerTimeoutSeconds,
                                          execute: killTimer)
        process.waitUntilExit()
        killTimer.cancel()
        return process.terminationStatus == 0
    }

    static func fetch(fresh: Bool = false) throws -> Snapshot {
        if fresh { primeFresh() }
        return try Snapshot.parse(run(["list", "--json"]))
    }

    static func switchTo(_ number: Int) throws {
        try run(["switch", String(number)])
    }

    /// Register the current login. `cswap add` without a slot never
    /// prompts: a new account auto-assigns the next slot, an
    /// already-registered one refreshes its stored credential, a
    /// logged-out state fails cleanly ("Please log in first").
    static func add() throws {
        try run(["add"])
    }
}
