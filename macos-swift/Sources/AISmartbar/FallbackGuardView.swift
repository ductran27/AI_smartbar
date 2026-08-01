// Compact fallback-guard status row with native disclosure details and
// actions. Every action calls the shared CLI through FallbackGuardStatus;
// this view has no managed-settings or privilege code.
import SwiftUI

struct FallbackGuardView: View {
    @EnvironmentObject private var guardStatus: FallbackGuardStatus
    @State private var isExpanded = false
    @State private var advancedExpanded = false
    @State private var confirmsRemoval = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.16)) {
                    isExpanded.toggle()
                    if !isExpanded {
                        advancedExpanded = false
                        confirmsRemoval = false
                    }
                }
            } label: {
                HStack(spacing: 8) {
                    statusSymbol
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Auto fallback")
                            .font(.caption.weight(.semibold))
                        Text(statusTitle)
                            .font(.caption2)
                            .foregroundStyle(statusColor)
                    }
                    Spacer(minLength: 6)
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.tertiary)
                        .accessibilityHidden(true)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Automatic fallback guard, \(statusTitle)")

            if isExpanded {
                details
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(.vertical, 7)
        .padding(.horizontal, 9)
        .background(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(Color.white.opacity(0.055))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .strokeBorder(Color.white.opacity(0.07), lineWidth: 1)
        )
    }

    @ViewBuilder
    private var statusSymbol: some View {
        if guardStatus.operation != nil {
            ProgressView()
                .controlSize(.small)
                .frame(width: 15, height: 15)
                .accessibilityHidden(true)
        } else {
            Image(systemName: statusIcon)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(statusColor)
                .frame(width: 15, height: 15)
                .accessibilityHidden(true)
        }
    }

    private var statusTitle: String {
        if let operation = guardStatus.operation { return operation.title }
        if let state = displayedState { return state.title }
        return guardStatus.lastError == nil ? "Checking protection…" : "Action needed"
    }

    /// A failed refresh must not leave a stale green badge looking current.
    /// The last-good details stay visible underneath, but the row fails closed.
    private var displayedState: FallbackGuardPresentationState? {
        if guardStatus.lastError != nil { return .actionNeeded }
        return guardStatus.report?.presentationState
    }

    private var statusIcon: String {
        guard let state = displayedState else {
            return "exclamationmark.shield"
        }
        switch state {
        case .protected: return "checkmark.shield.fill"
        case .protectedInconclusive: return "questionmark.diamond.fill"
        case .actionNeeded: return "exclamationmark.shield.fill"
        case .notProtected: return "shield"
        }
    }

    private var statusColor: Color {
        guard let state = displayedState else {
            return guardStatus.lastError == nil ? .secondary : .orange
        }
        switch state {
        case .protected: return .green
        case .protectedInconclusive: return .yellow
        case .actionNeeded: return .orange
        case .notProtected: return .secondary
        }
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let report = guardStatus.report {
                Text(report.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                VStack(alignment: .leading, spacing: 5) {
                    policyRow("Safety-triggered fallback",
                              value: report.safetyAutoFallback)
                    policyRow("Availability fallback",
                              value: report.availabilityAutoFallback)
                    manualOpusRow(report)
                }

                if let liveCheck = report.lastLiveCheck {
                    liveCheckView(liveCheck)
                }

                guardExplanation(report)
            } else if let error = guardStatus.lastError {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let error = guardStatus.lastError,
               guardStatus.report != nil {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.caption2)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            actionButtons
            advancedDetails
        }
        .padding(.top, 1)
    }

    private func policyRow(_ title: String,
                           value: FallbackGuardPolicyValue) -> some View {
        HStack(spacing: 6) {
            Image(systemName: policyIcon(value))
                .foregroundStyle(policyColor(value))
                .frame(width: 13)
            Text(title)
                .font(.caption2)
            Spacer(minLength: 5)
            Text(value.displayName)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(policyColor(value))
        }
    }

    private func policyIcon(_ value: FallbackGuardPolicyValue) -> String {
        switch value {
        case .blocked: return "checkmark.circle.fill"
        case .enabled: return "xmark.circle.fill"
        case .unknown: return "questionmark.circle"
        }
    }

    private func policyColor(_ value: FallbackGuardPolicyValue) -> Color {
        switch value {
        case .blocked: return .green
        case .enabled: return .orange
        case .unknown: return .secondary
        }
    }

    private func manualOpusRow(_ report: FallbackGuardReport) -> some View {
        let restricted = report.manualOpusRestrictedByGuard
        let verified = report.manualOpusVerifiedAvailable
        let notRestricted = restricted == false
        let unknown = restricted == nil
        return HStack(spacing: 6) {
            Image(systemName: notRestricted ? "checkmark.circle.fill"
                  : (unknown ? "questionmark.circle" : "xmark.circle.fill"))
                .foregroundStyle(notRestricted ? Color.green
                    : (unknown ? Color.secondary : Color.orange))
                .frame(width: 13)
            Text("Manual Opus selection")
                .font(.caption2)
            Spacer(minLength: 5)
            Text(verified ? "Available"
                 : (notRestricted ? "Not restricted"
                    : (unknown ? "Unknown" : "Restricted")))
                .font(.caption2.weight(.semibold))
                .foregroundStyle(notRestricted ? Color.green
                    : (unknown ? Color.secondary : Color.orange))
        }
    }

    private func liveCheckView(_ check: FallbackGuardLiveCheck) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 5) {
                Image(systemName: liveCheckIcon(check.normalizedStatus))
                    .foregroundStyle(liveCheckColor(check.normalizedStatus))
                Text("Live check: \(check.status.isEmpty ? "Unknown" : check.status.capitalized)")
                    .font(.caption2.weight(.semibold))
                Spacer(minLength: 4)
                if let date = check.checkedDate {
                    Text(date.formatted(date: .abbreviated, time: .shortened))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            if !check.probes.isEmpty {
                ForEach(Array(check.probes.enumerated()), id: \.offset) { _, probe in
                    let models = probe.observedModels.isEmpty
                        ? "no model reported"
                        : probe.observedModels.joined(separator: ", ")
                    Text("\(probe.name): \(probe.outcome) · \(models)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
            if let cost = check.totalCostUsd,
               let limit = check.budgetLimitUsd {
                Text(String(format: "Probe cost $%.3f of $%.2f limit", cost, limit))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(6)
        .background(RoundedRectangle(cornerRadius: 6)
            .fill(Color.white.opacity(0.04)))
    }

    private func liveCheckIcon(_ status: String) -> String {
        switch status {
        case "passed": return "checkmark.circle.fill"
        case "failed": return "xmark.circle.fill"
        default: return "questionmark.circle"
        }
    }

    private func liveCheckColor(_ status: String) -> Color {
        switch status {
        case "passed": return .green
        case "failed": return .orange
        default: return .yellow
        }
    }

    private func guardExplanation(_ report: FallbackGuardReport) -> some View {
        let scope = report.scope.isEmpty ? "this Mac's local Claude Code settings"
            : report.scope
        let manual: String
        if report.manualOpusVerifiedAvailable {
            manual = "Manual Opus was verified available."
        } else if report.manualOpusRestrictedByGuard == false {
            manual = "Manual Opus is not restricted by this guard."
        } else {
            manual = "Manual Opus availability is not currently confirmed."
        }
        return Text("The guard controls automatic fallback in \(scope). \(manual) Remote and cloud sessions are outside this device's scope.")
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var actionButtons: some View {
        HStack(spacing: 7) {
            Button("Protect") { guardStatus.enable() }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(guardStatus.isBusy
                          || guardStatus.report?.hasCompletePolicy == true)
                .help("Install or repair local automatic-fallback protection")
            Button("Verify") { guardStatus.verify() }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(guardStatus.isBusy
                          || guardStatus.report?.protected != true)
                .help("Run the bounded live verification probes")
            Spacer(minLength: 0)
        }
    }

    private var advancedDetails: some View {
        DisclosureGroup(isExpanded: $advancedExpanded) {
            VStack(alignment: .leading, spacing: 6) {
                if let report = guardStatus.report {
                    metadataRow("Claude", report.claudeVersion)
                    metadataRow("Managed source", report.activeManagedSource)
                    metadataRow("Policy", report.policyPath)
                }

                if confirmsRemoval {
                    Text("Remove protection from this Mac? Automatic fallback may resume.")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 7) {
                        Button("Remove protection", role: .destructive) {
                            confirmsRemoval = false
                            guardStatus.remove()
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.red)
                        .controlSize(.small)
                        Button("Keep") { confirmsRemoval = false }
                            .buttonStyle(.bordered)
                            .controlSize(.small)
                    }
                } else {
                    Button("Remove protection…", role: .destructive) {
                        confirmsRemoval = true
                    }
                    .controlSize(.small)
                    .disabled(guardStatus.isBusy
                              || guardStatus.report == nil)
                    .help("Ask the shared CLI to remove its managed policy")
                }
            }
            .padding(.top, 5)
        } label: {
            Text("Advanced")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func metadataRow(_ label: String, _ value: String) -> some View {
        if !value.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Text("\(label):")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.tertiary)
                Text(value)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(value)
            }
        }
    }
}
