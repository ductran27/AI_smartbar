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

    static func fetch() throws -> Snapshot {
        try Snapshot.parse(run(["list", "--json"]))
    }

    static func switchTo(_ number: Int) throws {
        try run(["switch", String(number)])
    }
}
