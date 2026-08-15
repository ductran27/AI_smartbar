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
    var usageFetchedAt: String?    // when cswap actually read the usage API
    var usageAgeSeconds: Double?   // age of that measurement at list time
}

struct CswapUsageRaw: Decodable {
    var fiveHour: CswapWindowRaw?
    var sevenDay: CswapWindowRaw?
    var spend: CswapWindowRaw?
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
// v3 semantics: every user-visible number is "% used" (the /usage scale);
// the 5-step status ramp is judged on usage and bars/pills FILL as tokens
// are spent (mirror of smartbar/core/model.py).

/// The used ramp — green → yellow → low → critical → full (purple, spent) —
/// plus `gray`, which is OFF that ramp: it means "no measurement at all".
enum Status: String {
    case green, yellow, low, critical, full, gray
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
    static var low: Double { value("SMARTBAR_LOW", 75) }
    static var red: Double { value("SMARTBAR_RED", 90) }

    static func status(forUsedPct pct: Double) -> Status {
        let used = max(0, pct)
        if used >= 100 { return .full }   // spent, not merely critical
        if used >= red { return .critical }
        if used >= low { return .low }
        if used >= yellow { return .yellow }
        return .green
    }
}

/// Card/row explanation for a cswap usageStatus without usable usage data
/// (mirror of model.STATE_TEXT).
enum AccountState {
    static func text(forStatus status: String) -> String {
        switch status {
        case "relogin_required":
            return "Re-login required — sign in as this account in Claude Code once"
        case "token_expired":
            return "Token expired — Claude Code refreshes it on next use"
        case "keychain_unavailable":
            return "Keychain locked — credentials unreadable"
        case "no_credentials":
            return "No stored credentials"
        case "api_key":
            return "API-key account — no subscription usage"
        default:
            return "No usage data"
        }
    }

    /// Slots whose STORED credential is dead: switching to one would
    /// restore a credential Anthropic already rejected.
    static func switchBlocked(status: String) -> Bool {
        status == "relogin_required" || status == "no_credentials"
    }
}

struct Metric: Identifiable, Equatable {
    var key: String        // "5h", "7d", "spend", or "scoped:<Name>"
    var label: String      // "5h", "7d", "Spend", "Fable"
    var short: String      // "5h", "7d", "$", "F"
    var pct: Double        // % used, as reported by cswap (the /usage scale)
    var resetsAt: String
    var countdown: String  // preformatted by cswap, e.g. "4h 3m"

    var id: String { key }
    var status: Status { Thresholds.status(forUsedPct: pct) }
    var isScoped: Bool { key.hasPrefix("scoped:") }

    var usedPct: Int { Int(max(0, pct).rounded()) }

    /// The heading this metric wears in the popover — a word, not a key.
    /// Mirror of model.metric_title: the name sits on its own line above the
    /// bar now, where "5h" reads as a token rather than as a thing you have.
    /// "7d" becomes "Weekly" because that is both what it is and what Claude
    /// Code's /usage calls it. A per-model bucket already carries a real
    /// name (the model), and anything unrecognised keeps whatever cswap
    /// labelled it rather than being mangled by a rule not written for it —
    /// the same refusal to guess metricWindowSeconds makes.
    var title: String {
        let trimmed = key.trimmingCharacters(in: .whitespaces)
        let fallback = label.isEmpty ? trimmed : label
        if trimmed.hasPrefix("scoped:") { return fallback }
        if trimmed == "spend" { return "Spend" }
        guard let found = trimmed.range(of: #"^(\d+)([hd])$"#,
                                        options: .regularExpression),
              found.lowerBound == trimmed.startIndex else { return fallback }
        let unit = trimmed.suffix(1)
        guard let amount = Int(trimmed.dropLast()) else { return fallback }
        if unit == "d" { return amount == 7 ? "Weekly" : "\(amount)-day" }
        return "\(amount)-hour"
    }

    /// Countdown recomputed from the absolute reset time so the wait shown
    /// stays live however old the snapshot is; cswap's fetch-time string is
    /// the fallback when resetsAt is unparseable.
    func liveCountdown(now: Date = Date()) -> String {
        TimeRemaining.countdown(to: resetsAt, now: now) ?? countdown
    }

    /// How far through its reset window this metric is, 0...1, or nil —
    /// mirror of model.pace_fraction. nil when metricWindowSeconds(key)
    /// says the window has no stated length, when resetsAt is empty or
    /// unparseable, or when the reset has already passed (nothing left to
    /// pace against). Otherwise `1 - (time left) / (window length)`,
    /// clamped to 0...1: 0 right after a reset, 1 right before the next
    /// one — "how far through this window are we", independent of how
    /// much of the budget is actually spent (that's `fraction`).
    func paceFraction(now: Date = Date()) -> Double? {
        guard let window = metricWindowSeconds(forKey: key) else { return nil }
        guard let resets = TimeRemaining.parseISO(resetsAt) else { return nil }
        let remaining = resets.timeIntervalSince(now)
        guard remaining > 0 else { return nil }
        return min(max(1 - remaining / window, 0), 1)
    }
}

/// Mirror of model.window_seconds: the length of the reset window a metric
/// KEY names, in seconds, or nil when the window has no stated length.
/// Only "<n>h"/"<n>d" keys (Claude Code's "5h"/"7d", and whatever shape
/// core/codex.py._window_key emits for a Codex rate-limit window — it
/// follows this exact pattern) have one. "spend" and every "scoped:<Name>"
/// per-model bucket carry a reset TIME (resetsAt) but no window LENGTH
/// cswap tells us, so they get nil here rather than a guessed length
/// paceFraction() could silently be wrong about.
private func metricWindowSeconds(forKey key: String) -> Double? {
    guard let range = key.range(of: #"^(\d+)[hd]$"#, options: .regularExpression)
    else { return nil }
    let digits = key[range.lowerBound..<key.index(before: range.upperBound)]
    guard let amount = Double(digits) else { return nil }
    return amount * (key.hasSuffix("d") ? 86400 : 3600)
}

struct Account: Identifiable, Equatable {
    var number: Int
    var email: String
    var org: String
    var active: Bool
    var ok: Bool           // usageStatus == "ok" and usage present
    var status: String     // raw cswap usageStatus ("" when absent)
    var metrics: [Metric]
    var fetchedAt: Date?   // usageFetchedAt: when the measurement was taken
    // "claude" (cswap slots) or "openai" (OpenAIStatus). OpenAI cards have
    // no switch button and must not borrow the Claude plan badge or device
    // count for the same address (pinned by TestOpenAIParity).
    var provider: String = "claude"
    var plan: String = ""              // OpenAI cards carry their badge inline
    var stateTextOverride: String = "" // display text computed by Python

    var id: Int { number }

    /// Explanation shown when there is no usable usage data ("" otherwise).
    var stateText: String {
        if !stateTextOverride.isEmpty && metrics.isEmpty {
            return stateTextOverride
        }
        if ok { return metrics.isEmpty ? "No usage data" : "" }
        return AccountState.text(forStatus: status)
    }

    /// Activating this slot would restore a dead stored credential.
    var switchBlocked: Bool { AccountState.switchBlocked(status: status) }

    var generalWorst: Metric? {
        metrics.filter { !$0.isScoped }.max(by: { $0.pct < $1.pct })
    }

    var scopedWorst: Metric? {
        metrics.filter { $0.isScoped }.max(by: { $0.pct < $1.pct })
    }

    var worstPct: Double { metrics.map { $0.pct }.max() ?? 0 }
    var worstUsedPct: Int { Int(max(0, worstPct).rounded()) }
    var worstStatus: Status {
        metrics.max(by: { $0.pct < $1.pct })?.status ?? .gray
    }

    /// Mirror of model.dot_style. v3 paints a dot gray at 100% used, which is
    /// the very same gray a dataless account gets — so "the Fable bucket is
    /// spent" and "this slot's credential is dead" rendered identically.
    /// Hollow means there is NO measurement behind the dot.
    var dotHollow: Bool { metrics.isEmpty }

    /// States for the twin-pill icon: general all-models pill first, then
    /// one pill per scoped (per-model) metric. Pills FILL as tokens are
    /// spent. Empty when there is no data (the renderer draws the hollow
    /// "?" state).
    var pillStates: [(fraction: Double, status: Status)] {
        var states: [(fraction: Double, status: Status)] = []
        if let general = generalWorst {
            states.append((min(general.pct, 100) / 100, general.status))
        }
        for metric in metrics where metric.isScoped {
            states.append((min(metric.pct, 100) / 100, metric.status))
        }
        return states
    }

    /// One-line "% used" summary (accessibility label for the icon).
    var summary: String {
        if metrics.isEmpty { return stateText.isEmpty ? "no usage data" : stateText }
        return metrics.map { "\($0.label) \($0.usedPct)% used" }
            .joined(separator: " · ")
    }
}

struct Snapshot: Equatable {
    var accounts: [Account]
    var schemaWarning: String?

    var activeAccount: Account? { accounts.first(where: { $0.active }) }

    /// When cswap last measured usage at the API — the honest "Updated"
    /// time (the active account's, since that's what /usage shows too).
    var dataDate: Date? {
        activeAccount?.fetchedAt ?? accounts.compactMap { $0.fetchedAt }.max()
    }

    /// Best non-active account to switch to (most headroom), or nil.
    var bestSwitch: Account? {
        accounts.filter { !$0.active && $0.ok && !$0.metrics.isEmpty && !$0.switchBlocked }
            .min(by: { $0.worstPct < $1.worstPct })
    }

    /// The live login's own slot reports a dead stored credential — the
    /// state `cswap add` heals by re-capturing the live credential.
    var needsRecapture: Bool { activeAccount?.switchBlocked == true }

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
            let status = raw.usageStatus ?? ""
            let ok = status == "ok" && raw.usage != nil
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
                if let window = usage.spend {
                    // resetsAt/countdown carried through like every other
                    // window: MetricBarRow renders a countdown for whatever
                    // it is given, so dropping them here was the Spend row
                    // showing a reset time on Linux/Windows and nothing on
                    // macOS. Pinned by test_cswap_parity.py's
                    // TestEveryWindowKeepsItsResetTime.
                    metrics.append(Metric(key: "spend", label: "Spend", short: "$",
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
                           status: status,
                           metrics: metrics,
                           fetchedAt: TimeRemaining.parseISO(raw.usageFetchedAt ?? ""))
        }
        return Snapshot(accounts: accounts, schemaWarning: warning)
    }
}
