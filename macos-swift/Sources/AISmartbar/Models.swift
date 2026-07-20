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

enum Status: String {
    case green, yellow, red, gray

    var dot: String {
        switch self {
        case .green: return "🟢"
        case .yellow: return "🟡"
        case .red: return "🔴"
        case .gray: return "⚪"
        }
    }
}

enum Thresholds {
    static func envDouble(_ name: String, _ fallback: Double) -> Double {
        guard let raw = ProcessInfo.processInfo.environment[name],
              let value = Double(raw) else { return fallback }
        return value
    }

    static var yellow: Double {
        if ProcessInfo.processInfo.environment["SMARTBAR_TEST_THRESHOLD"] != nil {
            return envDouble("SMARTBAR_TEST_THRESHOLD", 70)
        }
        return envDouble("SMARTBAR_YELLOW", 70)
    }

    static var red: Double {
        if ProcessInfo.processInfo.environment["SMARTBAR_TEST_THRESHOLD"] != nil {
            return envDouble("SMARTBAR_TEST_THRESHOLD", 90)
        }
        return envDouble("SMARTBAR_RED", 90)
    }

    static func status(for pct: Double) -> Status {
        if pct >= red { return .red }
        if pct >= yellow { return .yellow }
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
    var status: Status { Thresholds.status(for: pct) }
    var isScoped: Bool { key.hasPrefix("scoped:") }
    var roundedPct: Int { Int(pct.rounded()) }
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

    /// Badge rows: the general all-models limit first, then the per-model
    /// bucket, each carrying its own threshold color.
    var rows: [(text: String, status: Status)] {
        var result: [(text: String, status: Status)] = []
        for metric in [generalWorst, scopedWorst].compactMap({ $0 }) {
            result.append((text: "\(metric.short)\(metric.roundedPct)",
                           status: metric.status))
        }
        if result.isEmpty {
            result.append((text: "?", status: .gray))
        }
        return result
    }
}

struct Snapshot: Equatable {
    var accounts: [Account]
    var schemaWarning: String?

    var activeAccount: Account? { accounts.first(where: { $0.active }) }

    /// Menu-bar text: one dotted segment per badge row, e.g. "🟢 5h31 · 🟢 F30".
    var menuBarTitle: String {
        let rows = activeAccount?.rows ?? [(text: "?", status: Status.gray)]
        return rows.map { "\($0.status.dot) \($0.text)" }.joined(separator: " · ")
    }

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
