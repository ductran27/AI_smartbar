// The System tab: machine vitals (per-core CPU, a 60-minute history, memory)
// over three cards, plus Leftovers and Busy process lists. Every string comes
// from the payload (core/sysmon.py); this view only lays them out and colours
// each bar by the used-ramp of its own value — the panel's one rule, that
// saturation means "how much is spent". The live stream is started while this
// view is on screen and stopped when it leaves, so a closed popover can never
// leave a sampler running (the failure this whole feature exists to catch).
import SwiftUI

struct SystemView: View {
    let payload: SystemPayload
    @EnvironmentObject private var system: SystemStatus
    @Environment(\.colorScheme) private var colorScheme
    @State private var confirmToken: String?
    @State private var hovered: String?

    private var palette: Palette { Palette.of(colorScheme) }

    var body: some View {
        VStack(spacing: 8) {
            vitalsCard
            leftoversCard
            busyCard
        }
        .onAppear { system.startStream() }
        .onDisappear { system.stopStream() }
    }

    private func ramp(_ pct: Double) -> Color {
        Thresholds.status(forUsedPct: pct).color(in: colorScheme)
    }

    // MARK: - vitals

    private var vitalsCard: some View {
        card {
            HStack(spacing: 7) {
                Circle().fill(ramp(Double(payload.cpu.pct)))
                    .frame(width: 8, height: 8)
                Text("This Mac").font(.callout.weight(.semibold))
                    .foregroundStyle(palette.text)
                Text(payload.machine.caption).font(.caption2)
                    .foregroundStyle(palette.textTertiary).lineLimit(1)
                Spacer(minLength: 4)
                if payload.live {
                    Text("LIVE").font(.system(size: 10.5, weight: .bold))
                        .foregroundStyle(ramp(0))
                        .padding(.horizontal, 6).padding(.vertical, 1.5)
                        .background(Capsule().fill(ramp(0).opacity(0.18)))
                }
            }
            vitalRow(label: "CPU", caption: payload.cpu.caption,
                     value: "\(payload.cpu.pct)%") {
                HStack(spacing: 2) {
                    ForEach(Array(payload.cpu.cores.enumerated()), id: \.offset) {
                        _, value in
                        columnBar(pct: Double(value), height: 22)
                    }
                }
            }
            vitalRow(label: "60 min", caption: payload.history.peakText,
                     value: "\(payload.history.lastPct)%") {
                HStack(spacing: 1) {
                    ForEach(Array(payload.history.pct.enumerated()),
                            id: \.offset) { _, value in
                        columnBar(pct: value.map(Double.init), height: 34)
                    }
                }
            }
            vitalRow(label: "MEM", caption: payload.mem.caption,
                     value: "\(Int(payload.mem.pct.rounded()))%") {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(palette.barTrack)
                        Capsule().fill(ramp(payload.mem.pct))
                            .frame(width: max(7, geo.size.width
                                              * min(payload.mem.pct, 100) / 100))
                    }
                }.frame(height: 7)
            }
        }
    }

    private func vitalRow<Content: View>(label: String, caption: String,
                                         value: String,
                                         @ViewBuilder content: () -> Content)
        -> some View {
        VStack(alignment: .leading, spacing: 2.5) {
            HStack(spacing: 8) {
                Text(label).font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(palette.text)
                    .frame(width: 46, alignment: .leading)
                Text(caption).font(.caption2)
                    .foregroundStyle(palette.textSecondary).lineLimit(1)
                Spacer(minLength: 6)
                Text(value).font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(palette.text)
            }
            content()
        }
        .padding(.top, 6)
    }

    /// One column of a per-core or history strip: a track with a fill rising
    /// from the bottom, coloured by its own value. nil (a missing minute) is
    /// the track alone.
    private func columnBar(pct: Double?, height: CGFloat) -> some View {
        GeometryReader { geo in
            ZStack(alignment: .bottom) {
                RoundedRectangle(cornerRadius: 2).fill(palette.barTrack)
                if let pct {
                    RoundedRectangle(cornerRadius: 2).fill(ramp(pct))
                        .frame(height: max(0, geo.size.height
                                           * min(max(pct, 0), 100) / 100))
                }
            }
        }.frame(height: height)
    }

    // MARK: - process cards

    private var leftoversCard: some View {
        card {
            procHeader(title: "Leftovers", caption: "orphans of dead sessions",
                       chip: payload.leftovers.chip)
            if payload.leftovers.rows.isEmpty {
                Text("Nothing left behind — every orphan is gone.")
                    .font(.caption2).foregroundStyle(palette.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                ForEach(payload.leftovers.rows) { row in
                    procRow(row, tall: true)
                }
            }
            if let foot = payload.leftovers.foot, !foot.isEmpty {
                Text(foot).font(.caption2).foregroundStyle(palette.textTertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 4)
            }
        }
    }

    private var busyCard: some View {
        card {
            procHeader(title: "Busy",
                       caption: payload.busy.caption ?? "", chip: nil)
            ForEach(payload.busy.rows) { row in
                procRow(row, tall: false)
            }
        }
    }

    private func procHeader(title: String, caption: String,
                            chip: String?) -> some View {
        HStack(spacing: 8) {
            Text(title).font(.callout.weight(.semibold))
                .foregroundStyle(palette.text)
            Text(caption).font(.caption2)
                .foregroundStyle(palette.textTertiary).lineLimit(1)
            Spacer(minLength: 4)
            if let chip, !chip.isEmpty {
                let burning = chip.contains("burning")
                Text(chip).font(.system(size: 10.5))
                    .foregroundStyle(burning ? palette.danger
                                     : palette.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 1.5)
                    .background(Capsule().fill(palette.buttonDisabled))
            }
        }
    }

    @ViewBuilder
    private func procRow(_ row: ProcRow, tall: Bool) -> some View {
        if confirmToken == row.token && row.isKillable {
            HStack(spacing: 7) {
                Text("Kill \(row.name)?")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(palette.text).lineLimit(1)
                Spacer(minLength: 6)
                Button("Kill") { confirmToken = nil; system.kill(row.token) }
                    .buttonStyle(.borderedProminent).tint(palette.danger)
                    .controlSize(.small)
                Button("Keep") { confirmToken = nil }
                    .buttonStyle(.bordered).controlSize(.small)
            }
            .frame(height: tall ? 38 : 26)
        } else {
            rowContent(row, tall: tall)
                .frame(height: tall ? 38 : 26)
                .contentShape(Rectangle())
                .onHover { hovered = $0 ? row.token : (hovered == row.token
                                                       ? nil : hovered) }
        }
    }

    @ViewBuilder
    private func rowContent(_ row: ProcRow, tall: Bool) -> some View {
        let showCross = row.isKillable && hovered == row.token
        if tall {
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 8) {
                    kindChip(row.kind)
                    Text(row.name).font(.system(size: 12))
                        .foregroundStyle(palette.text).lineLimit(1)
                    Spacer(minLength: 6)
                    if showCross { crossButton(row) }
                    Text(row.meta).font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(palette.textSecondary)
                }
                Text(row.sub).font(.caption2)
                    .foregroundStyle(palette.textTertiary).lineLimit(1)
                    .padding(.leading, 56)
            }
        } else {
            HStack(spacing: 8) {
                kindChip(row.kind)
                Text(row.sub.isEmpty ? row.name : "\(row.name)   \(row.sub)")
                    .font(.system(size: 12)).foregroundStyle(palette.text)
                    .lineLimit(1)
                Spacer(minLength: 6)
                if showCross { crossButton(row) }
                Text(row.meta).font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(palette.textSecondary)
            }
        }
    }

    private func crossButton(_ row: ProcRow) -> some View {
        Button { confirmToken = row.token } label: {
            Image(systemName: "xmark").font(.system(size: 10, weight: .bold))
                .foregroundStyle(palette.text)
        }
        .buttonStyle(.plain).help("Kill \(row.name)")
    }

    private func kindChip(_ kind: String) -> some View {
        let ink: Color
        switch kind {
        case "junk": ink = palette.danger
        case "hot": ink = palette.warning
        case "session": ink = ramp(0)
        default: ink = palette.textTertiary
        }
        return Text(kind).font(.system(size: 10))
            .foregroundStyle(ink)
            .frame(width: 48)
            .padding(.vertical, 2)
            .background(Capsule().fill(palette.buttonDisabled))
    }

    private func card<Content: View>(@ViewBuilder content: () -> Content)
        -> some View {
        VStack(alignment: .leading, spacing: 0, content: content)
            .padding(.horizontal, 12.5).padding(.vertical, 10.5)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 14).fill(palette.cardBG))
            .overlay(RoundedRectangle(cornerRadius: 14)
                .stroke(palette.cardBorder, lineWidth: 1))
    }
}
