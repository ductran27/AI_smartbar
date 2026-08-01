// Thin process wrapper and observable state for the shared fallback-guard CLI.
// All policy reads, privileged installation, live probes and removal stay in
// Python; the native app invokes commands and renders their JSON answer.
import Foundation

enum FallbackGuardCommand: String, CaseIterable {
    case status
    case enable
    case verify
    case remove
}

struct FallbackGuardCLIResponse {
    var exitCode: Int32
    var report: FallbackGuardReport
}

enum FallbackGuardCLIError: Error, CustomStringConvertible {
    case checkoutNotFound
    case launcherNotFound
    case launchFailed(String)
    case noAnswer(Int32)
    case invalidJSON(String)

    var description: String {
        switch self {
        case .checkoutNotFound:
            return "AI smartbar checkout not found"
        case .launcherNotFound:
            return "AI smartbar launcher not found"
        case .launchFailed(let detail):
            return "Could not run fallback guard: \(detail)"
        case .noAnswer(let code):
            return "Fallback guard returned no answer (rc=\(code))"
        case .invalidJSON(let detail):
            return "Fallback guard returned invalid JSON: \(detail)"
        }
    }
}

struct FallbackGuardCLIClient: @unchecked Sendable {
    let execute: @Sendable (FallbackGuardCommand) throws -> FallbackGuardCLIResponse

    static let live = FallbackGuardCLIClient { command in
        try runProcess(command)
    }

    static func arguments(for command: FallbackGuardCommand) -> [String] {
        ["--fallback-guard", command.rawValue]
    }

    private static func runProcess(
        _ command: FallbackGuardCommand
    ) throws -> FallbackGuardCLIResponse {
        guard let root = PresenceStatus.repoRoot() else {
            throw FallbackGuardCLIError.checkoutNotFound
        }
        let launcher = root + "/bin/ai-smartbar"
        guard FileManager.default.isExecutableFile(atPath: launcher) else {
            throw FallbackGuardCLIError.launcherNotFound
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: launcher)
        process.arguments = arguments(for: command)
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        // A GUI app has no terminal. Null stdin makes an accidental textual
        // prompt fail closed; any administrator authorization UI belongs to
        // the shared CLI, not this process wrapper.
        process.standardInput = FileHandle.nullDevice
        let output = Pipe()
        process.standardOutput = output
        // The contract always returns user-facing JSON, including failures.
        // Discard diagnostics so they can never corrupt that JSON stream.
        process.standardError = FileHandle.nullDevice

        var environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        environment["PATH"] = [home + "/.local/bin", "/opt/homebrew/bin",
                               "/usr/local/bin", "/usr/bin", "/bin"]
            .joined(separator: ":")
        process.environment = environment

        do { try process.run() } catch {
            throw FallbackGuardCLIError.launchFailed(error.localizedDescription)
        }
        // Read first: waiting before draining a full pipe can deadlock.
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard !data.isEmpty else {
            throw FallbackGuardCLIError.noAnswer(process.terminationStatus)
        }
        let report: FallbackGuardReport
        do {
            report = try JSONDecoder().decode(FallbackGuardReport.self,
                                              from: data)
        } catch {
            throw FallbackGuardCLIError.invalidJSON(String(describing: error))
        }
        // Exit 10 (inspected but unprotected) and exit 2 (cancelled or a live
        // check was inconclusive) are still complete reports. Never discard a
        // valid answer merely because its outcome is non-zero.
        return FallbackGuardCLIResponse(exitCode: process.terminationStatus,
                                        report: report)
    }
}

enum FallbackGuardOperation: Equatable {
    case checking
    case installing
    case verifying
    case removing

    var title: String {
        switch self {
        case .checking: return "Checking protection…"
        case .installing: return "Installing protection…"
        case .verifying: return "Verifying…"
        case .removing: return "Removing protection…"
        }
    }
}

@MainActor
final class FallbackGuardStatus: ObservableObject {
    @Published private(set) var report: FallbackGuardReport?
    @Published private(set) var operation: FallbackGuardOperation?
    @Published private(set) var lastError: String?

    private let client: FallbackGuardCLIClient
    private var generation = 0

    init(client: FallbackGuardCLIClient = .live, refreshOnInit: Bool = true) {
        self.client = client
        if refreshOnInit { refresh() }
    }

    var isBusy: Bool { operation != nil }

    func refresh() {
        perform(.status, as: .checking, priority: .utility)
    }

    func enable() {
        perform(.enable, as: .installing, priority: .userInitiated)
    }

    func verify() {
        perform(.verify, as: .verifying, priority: .userInitiated)
    }

    func remove() {
        perform(.remove, as: .removing, priority: .userInitiated)
    }

    private func perform(_ command: FallbackGuardCommand,
                         as nextOperation: FallbackGuardOperation,
                         priority: TaskPriority) {
        guard operation == nil else { return }
        operation = nextOperation
        lastError = nil
        generation += 1
        let current = generation
        let client = client
        Task.detached(priority: priority) {
            let result: Result<FallbackGuardCLIResponse, Error>
            do {
                result = .success(try client.execute(command))
            } catch {
                result = .failure(error)
            }
            await MainActor.run { [weak self] in
                guard let self, current == self.generation else { return }
                self.operation = nil
                switch result {
                case .success(let response):
                    self.report = response.report
                    self.lastError = self.errorMessage(for: response,
                                                       command: command)
                case .failure(let error):
                    // Keep the last-good report visible; the error line says
                    // that this attempt could not replace it.
                    self.lastError = String(describing: error)
                }
            }
        }
    }

    private func errorMessage(for response: FallbackGuardCLIResponse,
                              command: FallbackGuardCommand) -> String? {
        let report = response.report
        let detail = report.details.first
        if response.exitCode == 1 || report.normalizedState == "error"
            || report.normalizedState == "unsupported" {
            return detail ?? "Fallback guard failed"
        }
        // Exit 2 from verify means "live result inconclusive", already made
        // visible by the yellow status. On mutating commands it means the
        // user cancelled or no safe change could be confirmed.
        if response.exitCode == 2 && command != .verify {
            return detail ?? "Protection was not changed"
        }
        return nil
    }
}
