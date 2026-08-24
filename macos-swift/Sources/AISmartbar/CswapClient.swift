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
    static let combinedTimeoutSeconds: Double = 40

    /// Force-freshens claude-swap's usage store, via its own auto-engine
    /// collector convention (explicit fetch set → the store's atomic
    /// reserve() uses stale-OR-plan-due eligibility — the sanctioned way
    /// to beat the 3-min serve TTL and harvest urgent 60s plans). A
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

    /// Prime AND list in ONE interpreter boot: the primer body above, then
    /// the real `cswap list --json` in-process (cli.main prints the
    /// canonical JSON to stdout). Halves the per-poll process spawns vs
    /// primer + list. Exit 97 marks "claude_swap internals moved" so we
    /// latch off and fall back for the rest of this app run.
    /// Keep in sync with smartbar/core/cswap.py COMBINED_CODE.
    static let combinedCode = """
    import sys
    try:
        from claude_swap.switcher import ClaudeAccountSwitcher
        switcher = ClaudeAccountSwitcher()
        numbers = {a.number for a in switcher.accounts_snapshot(fetch=set()).accounts}
        if numbers:
            switcher.accounts_snapshot(fetch=numbers)
        from claude_swap import cli
        sys.argv = ["cswap", "list", "--json"]
        cli.main()
    except SystemExit:
        raise
    except Exception as exc:
        sys.stderr.write("combined: %s\\n" % exc)
        sys.exit(97)
    """

    // Latched on exit 97; touched from concurrent fetch tasks.
    private static let latchLock = NSLock()
    private static var combinedUnsupportedStorage = false
    private static var combinedUnsupported: Bool {
        get { latchLock.lock(); defer { latchLock.unlock() }; return combinedUnsupportedStorage }
        set { latchLock.lock(); defer { latchLock.unlock() }; combinedUnsupportedStorage = newValue }
    }

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

    /// Run a process to completion with a kill-timer. Returns
    /// (terminationStatus, stdout, stderr).
    private static func runProcess(
        _ executable: String, arguments: [String], timeout: Double
    ) throws -> (status: Int32, stdout: Data, stderr: Data) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe
        try process.run()

        let killTimer = DispatchWorkItem {
            if process.isRunning { process.terminate() }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout,
                                          execute: killTimer)
        // Drain BOTH pipes before waiting: a child writing more than the
        // 64 KB pipe buffer (a chatty venv python on stderr, a long list)
        // blocks on write while we block in waitUntilExit — a deadlock the
        // kill timer only resolves 40 s later as "cswap failed (rc=15)".
        var errData = Data()
        let drained = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            errData = stderrPipe.fileHandleForReading.readDataToEndOfFile()
            drained.signal()
        }
        let outData = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
        drained.wait()
        process.waitUntilExit()
        killTimer.cancel()
        return (process.terminationStatus, outData, errData)
    }

    @discardableResult
    static func run(_ arguments: [String]) throws -> Data {
        guard let binary = binaryPath() else { throw CswapError.notFound }
        let result = try runProcess(binary, arguments: arguments,
                                    timeout: timeoutSeconds)
        guard result.status == 0 else {
            let raw = result.stderr.isEmpty ? result.stdout : result.stderr
            let detail = String(data: raw, encoding: .utf8) ?? ""
            throw CswapError.failed(result.status, String(detail.prefix(200)))
        }
        return result.stdout
    }

    /// The pipx venv interpreter that can import claude_swap, parsed from
    /// the cswap launcher's exec line; nil (compiled binary, mock, moved
    /// venv) just disables the primer/combined paths.
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
        // Two launcher shapes (mirror of cswap.venv_python): distlib's
        // quoted sh-exec trick, used only when the interpreter path has a
        // space, and the PLAIN shebang it writes otherwise.
        if let match = head.range(of: #"'(/[^']*/bin/python[^']*)'"#,
                                  options: .regularExpression) {
            let path = String(head[match].dropFirst().dropLast())
            if FileManager.default.isExecutableFile(atPath: path) { return path }
        }
        if let match = head.range(of: #"^#!(/\S*/bin/python\S*)"#,
                                  options: .regularExpression) {
            let path = String(head[match].dropFirst(2))
            if FileManager.default.isExecutableFile(atPath: path) { return path }
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        for candidate in [home + "/.local/share/pipx/venvs/claude-swap/bin/python",
                          home + "/.local/pipx/venvs/claude-swap/bin/python",
                          home + "/.local/share/uv/tools/claude-swap/bin/python"]
        where FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
        return nil
    }

    /// Best-effort store freshen; failures are silent (the follow-up list
    /// serves last-good data regardless). Fallback for when the combined
    /// path is unavailable.
    @discardableResult
    static func primeFresh() -> Bool {
        guard let python = venvPython() else { return false }
        guard let result = try? runProcess(python, arguments: ["-c", primerCode],
                                           timeout: primerTimeoutSeconds) else {
            return false
        }
        return result.status == 0
    }

    /// Prime + list JSON from one venv-python process; nil → fall back.
    static func fetchCombined() -> Data? {
        guard !combinedUnsupported, let python = venvPython() else { return nil }
        guard let result = try? runProcess(python, arguments: ["-c", combinedCode],
                                           timeout: combinedTimeoutSeconds) else {
            return nil
        }
        if result.status == 97 {
            combinedUnsupported = true  // internals moved; stop retrying this run
            return nil
        }
        guard result.status == 0, !result.stdout.isEmpty else { return nil }
        return result.stdout
    }

    static func fetch(fresh: Bool = false) throws -> Snapshot {
        if fresh {
            if let combined = fetchCombined(),
               let snapshot = try? Snapshot.parse(combined) {
                return snapshot
            }
            if combinedUnsupported {
                primeFresh()  // combined can never run here: old two-step behavior
            }
        }
        return try Snapshot.parse(run(["list", "--json"]))
    }

    static func switchTo(_ number: Int) throws {
        try run(["switch", String(number)])
    }

    /// Register or re-capture the current login. `cswap add` without a
    /// slot never prompts: a new account auto-assigns the next slot, an
    /// already-registered one refreshes its stored credential in place
    /// (and clears its dead-token state), a logged-out state fails
    /// cleanly ("Please log in first").
    static func add() throws {
        try run(["add"])
    }
}
