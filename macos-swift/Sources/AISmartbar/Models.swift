// Data model + presentation logic, ported 1:1 from the unit-tested Python
// core (smartbar/core/model.py + cswap.py parser). Foundation-only: no UI
// imports, so this file is independent of SwiftUI availability.
import Foundation

// MARK: - Wire format (cswap list --json, schemaVersion 1; see
// tests/fixtures/cswap_list.json for a captured real payload)

struct CswapList: Decodable {
    var schemaVersion: Int?
    var activeAccountNumber: Int?
    var accounts: [CswapAccountRaw]?
}

struct CswapAccountRaw: Decodable {
    var number: Int?
    var email: String?
    var organizationName: String?
    var active: Bool?
    var usageStatus: String?
    var usage: CswapUsageRaw?
}

struct CswapUsageRaw: Decodable {
    var fiveHour: CswapWindowRaw?
    var sevenDay: CswapWindowRaw?
    var scoped: [CswapScopedRaw]?
}

struct CswapWindowRaw: Decodable {
    var pct: Double?
    var resetsAt: String?
    var countdown: String?
    var clock: String?
}

struct CswapScopedRaw: Decodable {
    var name: String?
    var pct: Double?
    var resetsAt: String?
    var countdown: String?
    var clock: String?
}

// MARK: - Domain
// v2 semantics: every user-visible number is "% left"; the 5-step status
// ramp is judged on what's left (mirror of smartbar/core/model.py).

enum Status: String {
    case green, yellow, low, critical, gray
}

enum Thresholds {
    static func envDouble(_ name: String, _ fallback: Double) -> Double {
        guard let raw = ProcessInfo.processInfo.environment[name],
              let value = Double(raw) else { return fallback }
        return value
    }

    private static func value(_ name: String, _ fallback: Double) -> Double {
        if ProcessInfo.processInfo.environment["SMARTBAR_TEST_THRESHOLD"] != nil {
            return envDouble("SMARTBAR_TEST_THRESHOLD", fallback)
        }
        return envDouble(name, fallback)
    }

    static var yellow: Double { value("SMARTBAR_YELLOW", 50) }
    static var low: Double { value("SMARTBAR_LOW", 25) }
    static var red: Double { value("SMARTBAR_RED", 10) }

    static func status(forUsedPct pct: Double) -> Status {
        let left = max(0, 100 - pct)
        if left <= 0 { return .gray }
        if left <= red { return .critical }
        if left <= low { return .low }
        if left <= yellow { return .yellow }
        return .green
    }
}

struct Metric: Identifiable, Equatable {
    var key: String        // "5h", "7d", or "scoped:<Name>"
    var label: String      // "5h", "7d", "Fable"
    var short: String      // "5h", "7d", "F"
    var pct: Double
    var resetsAt: String
    var countdown: String  // preformatted by cswap, e.g. "4h 3m"

    var id: String { key }
    var status: Status { Thresholds.status(forUsedPct: pct) }
    var isScoped: Bool { key.hasPrefix("scoped:") }

    /// % of the window remaining, clamped at 0.
    var left: Double { max(0, 100 - pct) }
    var leftPct: Int { Int(left.rounded()) }
}

struct Account: Identifiable, Equatable {
    var number: Int
    var email: String
    var org: String
    var active: Bool
    var ok: Bool           // usageStatus == "ok" and usage present
    var metrics: [Metric]

    var id: Int { number }

    var generalWorst: Metric? {
        metrics.filter { !$0.isScoped }.max(by: { $0.pct < $1.pct })
    }

    var scopedWorst: Metric? {
        metrics.filter { $0.isScoped }.max(by: { $0.pct < $1.pct })
    }

    var worstPct: Double { metrics.map { $0.pct }.max() ?? 0 }
    var worstLeftPct: Int { Int(max(0, 100 - worstPct).rounded()) }
    var worstStatus: Status {
        metrics.max(by: { $0.pct < $1.pct })?.status ?? .gray
    }

    /// States for the twin-pill icon: general all-models pill first, then
    /// one pill per scoped (per-model) metric. Empty when there is no data
    /// (the renderer draws the hollow "?" state).
    var pillStates: [(fraction: Double, status: Status)] {
        var states: [(fraction: Double, status: Status)] = []
        if let general = generalWorst {
            states.append((general.left / 100, general.status))
        }
        for metric in metrics where metric.isScoped {
            states.append((metric.left / 100, metric.status))
        }
        return states
    }

    /// One-line "% left" summary (accessibility label for the icon).
    var summary: String {
        if metrics.isEmpty { return "no usage data" }
        return metrics.map { "\($0.label) \($0.leftPct)% left" }
            .joined(separator: " · ")
    }
}

struct Snapshot: Equatable {
    var accounts: [Account]
    var schemaWarning: String?

    var activeAccount: Account? { accounts.first(where: { $0.active }) }

    /// Best non-active account to switch to (most headroom), or nil.
    var bestSwitch: Account? {
        accounts.filter { !$0.active && $0.ok && !$0.metrics.isEmpty }
            .min(by: { $0.worstPct < $1.worstPct })
    }

    static func parse(_ data: Data) throws -> Snapshot {
        let decoded: CswapList
        do {
            decoded = try JSONDecoder().decode(CswapList.self, from: data)
        } catch {
            throw CswapError.badJSON(String(describing: error))
        }
        var warning: String?
        if decoded.schemaVersion != 1 {
            warning = "unexpected cswap schemaVersion \(String(describing: decoded.schemaVersion))"
        }
        let accounts: [Account] = (decoded.accounts ?? []).map { raw in
            let ok = raw.usageStatus == "ok" && raw.usage != nil
            var metrics: [Metric] = []
            if ok, let usage = raw.usage {
                if let window = usage.fiveHour {
                    metrics.append(Metric(key: "5h", label: "5h", short: "5h",
                                          pct: window.pct ?? 0,
                                          resetsAt: window.resetsAt ?? "",
                                          countdown: window.countdown ?? ""))
                }
                if let window = usage.sevenDay {
                    metrics.append(Metric(key: "7d", label: "7d", short: "7d",
                                          pct: window.pct ?? 0,
                                          resetsAt: window.resetsAt ?? "",
                                          countdown: window.countdown ?? ""))
                }
                for scoped in usage.scoped ?? [] {
                    let name = (scoped.name?.isEmpty == false) ? scoped.name! : "?"
                    metrics.append(Metric(key: "scoped:\(name)", label: name,
                                          short: String(name.prefix(1)).uppercased(),
                                          pct: scoped.pct ?? 0,
                                          resetsAt: scoped.resetsAt ?? "",
                                          countdown: scoped.countdown ?? ""))
                }
            }
            return Account(number: raw.number ?? 0,
                           email: raw.email ?? "?",
                           org: raw.organizationName ?? "",
                           active: raw.active ?? false,
                           ok: ok,
                           metrics: metrics)
        }
        return Snapshot(accounts: accounts, schemaWarning: warning)
    }
}
