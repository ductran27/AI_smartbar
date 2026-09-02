// The System tab: machine vitals (per-core CPU, a 60-minute history, memory)
// over three cards, plus Leftovers and Busy process lists. Every string comes
// from the payload (core/sysmon.py); this view only lays them out and colours
// each bar by the used-ramp of its own value — the panel's one rule, that
// saturation means "how much is spent". The live stream is started while this
// view is on screen and stopped when it leaves, so a closed popover can never
// leave a sampler running (the failure this whole feature exists to catch).
//
// Geometry and type come from the shared theme table (popover_theme.py:
// CARD_GAP 9, CARD_PAD_H/V 14/11.5, CARD_RADIUS 15.5, LABEL_W 73, SIZE_EMAIL
// 15.5, SIZE_CAPTION 12.5, SIZE_CHIP 11.5, BAR_H 7.5, SYS_CORES_H 22,
// SYS_HIST_H 34, SYS_ROW_H/TALL 26/38, PROC_KIND_W 48) — the same numbers
// the cairo painters draw, so the tab is one instrument on every platform.
import SwiftUI

struct SystemView: View {
    let payload: SystemPayload
    @EnvironmentObject private var system: SystemStatus
    @Environment(\.colorScheme) private var colorScheme
    @State private var confirmToken: String?
    @State private var hovered: String?

    private var palette: Palette { Palette.of(colorScheme) }

    var body: some View {
        VStack(spacing: 9) {
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
            HStack(spacing: 8) {
                Circle().fill(ramp(Double(payload.cpu.pct)))
                    .frame(width: 9, height: 9)
                Text("This Mac").font(.system(size: 15.5, weight: .semibold))
                    .foregroundStyle(palette.text)
                Text(payload.machine.caption).font(.system(size: 12.5))
                    .foregroundStyle(palette.textTertiary).lineLimit(1)
                Spacer(minLength: 4)
                if payload.live {
                    Text("LIVE").font(.system(size: 11.5, weight: .bold))
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
                TrendChart(values: payload.history.pct)
            }
            vitalRow(label: "MEM", caption: payload.mem.caption,
                     value: "\(Int(payload.mem.pct.rounded()))%") {
                TrendChart(values: payload.mem.history.pct)
            }
        }
    }

    private func vitalRow<Content: View>(label: String, caption: String,
                                         value: String,
                                         @ViewBuilder content: () -> Content)
        -> some View {
        VStack(alignment: .leading, spacing: 2.5) {
            HStack(spacing: 8) {
                Text(label).font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(palette.text)
                    .frame(width: 73, alignment: .leading)
                Text(caption).font(.system(size: 12.5))
                    .foregroundStyle(palette.textSecondary).lineLimit(1)
                Spacer(minLength: 6)
                Text(value).font(.system(size: 12.5, design: .monospaced))
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
                    .font(.system(size: 12.5)).foregroundStyle(palette.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                ForEach(payload.leftovers.rows) { row in
                    procRow(row, tall: true)
                }
            }
            // "+N more" rides the foot line exactly as the painted hosts draw
            // it (popover_layout._system_view) — 9+ orphans used to be
            // invisible here.
            let more = payload.leftovers.more ?? 0
            let foot = (payload.leftovers.foot ?? "")
                + (more > 0 ? " · +\(more) more" : "")
            if !foot.isEmpty {
                Text(foot).font(.system(size: 12.5))
                    .foregroundStyle(palette.textTertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 4)
            }
        }
    }

    private var busyCard: some View {
        card {
            procHeader(title: "Busy",
                       caption: payload.busy.caption ?? "", chip: nil)
            if payload.busy.rows.isEmpty {
                Text("Nothing busy right now.")
                    .font(.system(size: 12.5)).foregroundStyle(palette.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                ForEach(payload.busy.rows) { row in
                    procRow(row, tall: false)
                }
            }
        }
    }

    private func procHeader(title: String, caption: String,
                            chip: String?) -> some View {
        HStack(spacing: 8) {
            Text(title).font(.system(size: 15.5, weight: .semibold))
                .foregroundStyle(palette.text)
            Text(caption).font(.system(size: 12.5))
                .foregroundStyle(palette.textTertiary).lineLimit(1)
            Spacer(minLength: 4)
            if let chip, !chip.isEmpty {
                let burning = chip.contains("burning")
                Text(chip).font(.system(size: 11.5))
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
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundStyle(palette.text).lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 6)
                Button("Kill") { confirmToken = nil; system.kill(row.token) }
                    .buttonStyle(.borderedProminent).tint(palette.danger)
                    .controlSize(.small)
                    .help("Kill \(row.name)")
                Button("Keep") { confirmToken = nil }
                    .buttonStyle(.bordered).controlSize(.small)
                    .help("Keep this process")
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
                    Text(row.name).font(.system(size: 12.5))
                        .foregroundStyle(palette.text).lineLimit(1)
                        .truncationMode(.middle)
                    Spacer(minLength: 6)
                    if showCross { crossButton(row) }
                    Text(row.meta).font(.system(size: 12.5, design: .monospaced))
                        .foregroundStyle(palette.textSecondary)
                }
                Text(row.sub).font(.system(size: 12.5))
                    .foregroundStyle(palette.textTertiary).lineLimit(1)
                    .padding(.leading, 56)
            }
        } else {
            HStack(spacing: 8) {
                kindChip(row.kind)
                Text(row.sub.isEmpty ? row.name : "\(row.name)   \(row.sub)")
                    .font(.system(size: 12.5)).foregroundStyle(palette.text)
                    .lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 6)
                if showCross { crossButton(row) }
                Text(row.meta).font(.system(size: 12.5, design: .monospaced))
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

    /// Kind-chip ink mirrors popover_layout._kind_ink: junk and hot take the
    /// STATUS ramp's critical and yellow (the same reds/ambers every card
    /// uses), not the palette's danger/warning button tints.
    private func kindChip(_ kind: String) -> some View {
        let ink: Color
        switch kind {
        case "junk": ink = Status.critical.color(in: colorScheme)
        case "hot": ink = Status.yellow.color(in: colorScheme)
        case "session": ink = ramp(0)
        default: ink = palette.textTertiary
        }
        return Text(kind).font(.system(size: 11.5))
            .foregroundStyle(ink)
            .frame(width: 48)
            .padding(.vertical, 2)
            .background(Capsule().fill(palette.buttonDisabled))
    }

    private func card<Content: View>(@ViewBuilder content: () -> Content)
        -> some View {
        VStack(alignment: .leading, spacing: 0, content: content)
            .padding(.horizontal, 14).padding(.vertical, 11.5)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 15.5).fill(palette.cardBG))
            .overlay(RoundedRectangle(cornerRadius: 15.5)
                .stroke(palette.cardBorder, lineWidth: 1))
    }
}

// MARK: - trend chart

/// A 60-minute history as a filled area chart — the time-over counterpart to
/// the per-core bar strip, and the same instrument the cairo painter draws
/// (popover_draw._draw_area / the System note in popover_theme.py). The area
/// under the curve is washed in the vertical used-ramp gradient and the top
/// edge is stroked in that same ramp, so the curve's height reads in exactly
/// the colour a bar of that value would: the panel keeps its one rule, that
/// colour only ever means "how much is spent". Samples map evenly left→right
/// (newest at the right edge); a nil is an honest gap that breaks the curve.
///
/// Geometry mirrors popover_theme: SYS_HIST_H 34, SYS_AREA_RADIUS 3,
/// SYS_AREA_LINE 1.5, SYS_AREA_FILL_ALPHA 0.32.
struct TrendChart: View {
    let values: [Int?]
    @Environment(\.colorScheme) private var colorScheme

    private var palette: Palette { Palette.of(colorScheme) }

    // Colour 0 at the bottom (value 0) to 1 at the top (value 100), built from
    // the SAME thresholds and status colours the bars use — no rule of its
    // own. Capped at critical: "full" purple is a discrete "limit spent" state
    // for an account pill, not a band a CPU/memory line climbs through.
    private var rampStops: [Gradient.Stop] {
        func ink(_ status: Status) -> Color { status.color(in: colorScheme) }
        return [.init(color: ink(.green), location: 0),
                .init(color: ink(.yellow), location: Thresholds.yellow / 100),
                .init(color: ink(.low), location: Thresholds.low / 100),
                .init(color: ink(.critical), location: Thresholds.red / 100),
                .init(color: ink(.critical), location: 1)]
    }

    private var gradient: LinearGradient {
        LinearGradient(stops: rampStops, startPoint: .bottom, endPoint: .top)
    }

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 3).fill(palette.barTrack)
            gradient.opacity(0.32).mask(TrendArea(values: values))
            gradient.mask(TrendLine(values: values).stroke(
                style: StrokeStyle(lineWidth: 1.5, lineCap: .round,
                                   lineJoin: .round)))
        }
        .frame(height: 34)
        .clipShape(RoundedRectangle(cornerRadius: 3))
    }
}

/// Consecutive present samples as [(index, value)] runs — the curve is one run
/// per unbroken stretch, so a gap simply ends a run and starts the next.
private func trendRuns(_ values: [Int?]) -> [[(Int, Double)]] {
    var runs: [[(Int, Double)]] = []
    var run: [(Int, Double)] = []
    for (index, value) in values.enumerated() {
        if let value {
            run.append((index, Double(value)))
        } else if !run.isEmpty {
            runs.append(run)
            run = []
        }
    }
    if !run.isEmpty { runs.append(run) }
    return runs
}

private func trendX(_ index: Int, count: Int, in rect: CGRect) -> CGFloat {
    count > 1 ? rect.minX + rect.width * CGFloat(index) / CGFloat(count - 1)
              : rect.midX
}

private func trendY(_ value: Double, in rect: CGRect) -> CGFloat {
    rect.maxY - rect.height * CGFloat(min(max(value, 0), 100) / 100)
}

/// The area under each run, closed down to the baseline (filled by the wash).
private struct TrendArea: Shape {
    let values: [Int?]
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let count = values.count
        for run in trendRuns(values) where run.count >= 2 {
            path.move(to: CGPoint(x: trendX(run[0].0, count: count, in: rect),
                                  y: rect.maxY))
            for (index, value) in run {
                path.addLine(to: CGPoint(
                    x: trendX(index, count: count, in: rect),
                    y: trendY(value, in: rect)))
            }
            path.addLine(to: CGPoint(
                x: trendX(run[run.count - 1].0, count: count, in: rect),
                y: rect.maxY))
            path.closeSubpath()
        }
        return path
    }
}

/// The curve's top edge (stroked at full strength). An isolated minute between
/// gaps has no line, so it is drawn as a small dot instead.
private struct TrendLine: Shape {
    let values: [Int?]
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let count = values.count
        for run in trendRuns(values) {
            if run.count == 1 {
                let point = CGPoint(
                    x: trendX(run[0].0, count: count, in: rect),
                    y: trendY(run[0].1, in: rect))
                path.addEllipse(in: CGRect(x: point.x - 1.5, y: point.y - 1.5,
                                           width: 3, height: 3))
                continue
            }
            for (order, sample) in run.enumerated() {
                let point = CGPoint(
                    x: trendX(sample.0, count: count, in: rect),
                    y: trendY(sample.1, in: rect))
                if order == 0 { path.move(to: point) }
                else { path.addLine(to: point) }
            }
        }
        return path
    }
}
