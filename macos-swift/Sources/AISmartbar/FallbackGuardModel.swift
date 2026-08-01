// Display model for `ai-smartbar --fallback-guard …`.
//
// Python owns policy discovery, mutation and live verification. Swift decodes
// that answer leniently and decides only how to present it. In particular,
// this file never reads or writes Claude's managed-settings files directly.
import Foundation

enum FallbackGuardPolicyValue: String, Equatable {
    case blocked
    case enabled
    case unknown

    /// The current CLI emits strings. Old/development helpers sometimes
    /// emitted booleans, where `false` meant the fallback was disabled.
    init(string: String) {
        switch string.trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased() {
        case "blocked", "disabled", "deny", "denied", "false", "off", "0":
            self = .blocked
        case "enabled", "allowed", "allow", "true", "on", "1":
            self = .enabled
        default:
            self = .unknown
        }
    }

    var displayName: String {
        switch self {
        case .blocked: return "Blocked"
        case .enabled: return "Enabled"
        case .unknown: return "Unknown"
        }
    }
}

enum FallbackGuardPresentationState: Equatable {
    case protected
    case protectedInconclusive
    case actionNeeded
    case notProtected

    var title: String {
        switch self {
        case .protected: return "Protected"
        case .protectedInconclusive: return "Protected + inconclusive"
        case .actionNeeded: return "Action needed"
        case .notProtected: return "Not protected"
        }
    }
}

struct FallbackGuardProbe: Decodable, Equatable {
    var name: String
    var outcome: String
    var requestedModel: String
    var observedModels: [String]
    var costUsd: Double?
    var requestId: String?

    private enum CodingKeys: String, CodingKey {
        case name, outcome, requestedModel, observedModels, costUsd, requestId
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        name = values.lossyString(.name)
        outcome = values.lossyString(.outcome)
        requestedModel = values.lossyString(.requestedModel)
        if let rows = try? values.decode([String].self, forKey: .observedModels) {
            observedModels = rows
        } else if let one = try? values.decode(String.self, forKey: .observedModels) {
            observedModels = one.isEmpty ? [] : [one]
        } else {
            observedModels = []
        }
        costUsd = try? values.decode(Double.self, forKey: .costUsd)
        requestId = try? values.decode(String.self, forKey: .requestId)
    }
}

struct FallbackGuardLiveCheck: Decodable, Equatable {
    var status: String
    var checkedAt: String
    var claudeVersion: String
    var totalCostUsd: Double?
    var budgetLimitUsd: Double?
    var probes: [FallbackGuardProbe]

    private enum CodingKeys: String, CodingKey {
        case status, checkedAt, claudeVersion, totalCostUsd, budgetLimitUsd, probes
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        status = values.lossyString(.status)
        checkedAt = values.lossyString(.checkedAt)
        claudeVersion = values.lossyString(.claudeVersion)
        totalCostUsd = try? values.decode(Double.self, forKey: .totalCostUsd)
        budgetLimitUsd = try? values.decode(Double.self, forKey: .budgetLimitUsd)
        probes = (try? values.decode([FallbackGuardProbe].self,
                                     forKey: .probes)) ?? []
    }

    var normalizedStatus: String {
        status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    var checkedDate: Date? { TimeRemaining.parseISO(checkedAt) }
}

struct FallbackGuardReport: Decodable, Equatable {
    var ok: Bool
    var state: String
    var protected: Bool
    var safetyAutoFallback: FallbackGuardPolicyValue
    var availabilityAutoFallback: FallbackGuardPolicyValue
    var manualOpusRestrictedByGuard: Bool?
    var scope: String
    var claudeVersion: String
    var activeManagedSource: String
    var policyPath: String
    var details: [String]
    var lastLiveCheck: FallbackGuardLiveCheck?

    private enum CodingKeys: String, CodingKey {
        case ok, state, protected
        case safetyAutoFallback, availabilityAutoFallback
        case manualOpusRestrictedByGuard
        case scope, claudeVersion, activeManagedSource, policyPath, details
        case lastLiveCheck
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        ok = values.lossyBool(.ok) ?? false
        state = values.lossyString(.state)
        protected = values.lossyBool(.protected) ?? false
        safetyAutoFallback = values.policyValue(.safetyAutoFallback)
        availabilityAutoFallback = values.policyValue(.availabilityAutoFallback)
        manualOpusRestrictedByGuard = values.lossyBool(
            .manualOpusRestrictedByGuard)
        scope = values.lossyString(.scope)
        claudeVersion = values.lossyString(.claudeVersion)
        activeManagedSource = values.lossyString(.activeManagedSource)
        policyPath = values.lossyString(.policyPath)
        if let rows = try? values.decode([String].self, forKey: .details) {
            details = rows.filter { !$0.isEmpty }
        } else if let one = try? values.decode(String.self, forKey: .details) {
            details = one.isEmpty ? [] : [one]
        } else if let keyed = try? values.decode([String: String].self,
                                                  forKey: .details) {
            details = keyed.keys.sorted().map { "\($0): \(keyed[$0] ?? "")" }
        } else {
            details = []
        }
        lastLiveCheck = try? values.decode(FallbackGuardLiveCheck.self,
                                           forKey: .lastLiveCheck)
    }

    var normalizedState: String {
        state.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    /// Static policy is complete only when every invariant is explicit.
    /// Unknown values never inherit trust from a broad `state: protected`
    /// label. A complete policy may still need a fresh live verification.
    var hasCompletePolicy: Bool {
        let protectedState = normalizedState == "protected"
            || normalizedState == "protected_inconclusive"
        return protected
            && protectedState
            && safetyAutoFallback == .blocked
            && availabilityAutoFallback == .blocked
            && manualOpusRestrictedByGuard == false
    }

    /// Green additionally requires a passing check made by the currently
    /// installed Claude version. A missing or stale check remains yellow.
    var isFullyProtected: Bool {
        guard hasCompletePolicy,
              normalizedState == "protected",
              let check = lastLiveCheck,
              check.normalizedStatus == "passed",
              !claudeVersion.isEmpty,
              check.claudeVersion == claudeVersion else { return false }
        return true
    }

    /// A false policy flag proves that this guard does not hide manual Opus;
    /// only the dedicated live probe proves it was actually served.
    var manualOpusVerifiedAvailable: Bool {
        lastLiveCheck?.probes.contains {
            $0.name == "manual_opus"
                && $0.outcome.uppercased() == "OPUS_OK"
        } == true
    }

    var presentationState: FallbackGuardPresentationState {
        switch normalizedState {
        case "not_protected":
            return .notProtected
        case "unsupported", "error", "action_needed":
            return .actionNeeded
        case "protected", "protected_inconclusive":
            guard hasCompletePolicy else { return .actionNeeded }
            if lastLiveCheck?.normalizedStatus == "failed" {
                return .actionNeeded
            }
            if normalizedState == "protected_inconclusive"
                || !isFullyProtected {
                return .protectedInconclusive
            }
            return .protected
        default:
            return protected ? .actionNeeded : .notProtected
        }
    }

    var summary: String {
        switch presentationState {
        case .protected:
            return "Automatic fallback is blocked on this Mac."
        case .protectedInconclusive:
            return "Managed policy is complete; live verification is not current or was inconclusive."
        case .actionNeeded:
            return details.first ?? "Protection is incomplete or conflicting."
        case .notProtected:
            return details.first ?? "Automatic fallback is not blocked on this Mac."
        }
    }
}

private extension KeyedDecodingContainer {
    func lossyString(_ key: Key) -> String {
        if let value = try? decode(String.self, forKey: key) { return value }
        if let value = try? decode(Int.self, forKey: key) { return String(value) }
        if let value = try? decode(Double.self, forKey: key) { return String(value) }
        if let value = try? decode(Bool.self, forKey: key) {
            return value ? "true" : "false"
        }
        return ""
    }

    func lossyBool(_ key: Key) -> Bool? {
        if let value = try? decode(Bool.self, forKey: key) { return value }
        if let value = try? decode(Int.self, forKey: key) { return value != 0 }
        guard let value = try? decode(String.self, forKey: key) else { return nil }
        switch value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "true", "yes", "on", "enabled", "1": return true
        case "false", "no", "off", "disabled", "0": return false
        default: return nil
        }
    }

    func policyValue(_ key: Key) -> FallbackGuardPolicyValue {
        if let value = try? decode(String.self, forKey: key) {
            return FallbackGuardPolicyValue(string: value)
        }
        // Field meaning is "is automatic fallback enabled?". Therefore
        // false is the protected/blocked setting and true is enabled.
        if let value = try? decode(Bool.self, forKey: key) {
            return value ? .enabled : .blocked
        }
        if let value = try? decode(Int.self, forKey: key) {
            return value == 0 ? .blocked : .enabled
        }
        return .unknown
    }
}
