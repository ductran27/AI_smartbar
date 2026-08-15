// Six small marks — overview, claude, openai, clock, pause, warn — drawn
// from Path geometry rather than SF Symbols. SF Symbols exist only on
// macOS, and every one of these marks also has to run through Linux's
// cairo painter (popover_draw._draw_glyph, one drawing function per kind);
// reaching for an SF Symbol here and a hand-picked cairo path there is
// exactly how the two would silently drift apart, which is the whole
// reason this repo pins Swift/Python agreement with source-scraping tests
// instead of a Swift toolchain (see tests/test_model_parity.py). The
// geometry below mirrors _draw_glyph's per-kind functions closely enough
// to read as the same icon, not so exactly that the two have to move in
// lockstep — there is no pixel-parity test between them, only that both
// exist and both compile.
import SwiftUI

struct ProviderMark: View {
    let kind: String

    var body: some View {
        GeometryReader { geo in
            let size = min(geo.size.width, geo.size.height)
            ZStack {
                strokedPath(size: size)
                    .stroke(style: StrokeStyle(lineWidth: max(1, size * 0.14),
                                               lineCap: .round, lineJoin: .round))
                filledPath(size: size)
                    .fill()
            }
        }
    }

    /// Everything drawn with `.stroke()` — a hollow outline reads as
    /// "structure", the same job a stroke does in every cairo glyph.
    private func strokedPath(size: CGFloat) -> Path {
        switch kind {
        case "openai": return openAIPath(size: size)
        case "clock": return clockPath(size: size)
        case "warn": return warnOutlinePath(size: size)
        default: return Path()
        }
    }

    /// Everything drawn with `.fill()` — solid marks read as "presence"
    /// (overview's grid, pause's bars), or, for `warn`, the one filled dot
    /// under an otherwise stroked triangle.
    private func filledPath(size: CGFloat) -> Path {
        switch kind {
        case "claude": return claudePath(size: size)
        case "overview": return overviewPath(size: size)
        case "pause": return pausePath(size: size)
        case "warn": return warnDotPath(size: size)
        default: return Path()
        }
    }

    // MARK: - claude — a filled starburst: eleven tapered rays whose wide
    // ends overlap into a solid hub. `reach` is each ray's length as a
    // fraction of the glyph radius and `skew` nudges it off its even share
    // of the circle, standing in for the hand-drawn wobble of the real
    // mark — fixed tables rather than random numbers so the glyph is
    // identical every render and matches popover_draw.CLAUDE_REACH /
    // CLAUDE_SKEW number for number.

    private static let claudeReach: [CGFloat] =
        [1.00, 0.86, 0.94, 0.82, 0.90, 1.00, 0.84, 0.92, 0.88, 0.97, 0.83]
    private static let claudeSkew: [CGFloat] =
        [0.00, 0.04, -0.03, 0.02, -0.05, 0.01, 0.03, -0.02, 0.05, -0.01, 0.02]

    private func claudePath(size: CGFloat) -> Path {
        var path = Path()
        let cx = size / 2, cy = size / 2
        let tau = CGFloat.pi * 2
        let base = size * 0.075     // half-width where a ray meets the hub
        let rays = Self.claudeReach.count
        for (i, reach) in Self.claudeReach.enumerated() {
            let angle = tau * CGFloat(i) / CGFloat(rays)
                + tau * Self.claudeSkew[i] - tau / 4
            // The base is a chord across the hub, so it is drawn on the
            // normal — the angle a quarter turn from the ray's own.
            let nx = cos(angle + tau / 4), ny = sin(angle + tau / 4)
            let tip = size * 0.5 * reach
            path.move(to: CGPoint(x: cx + base * nx, y: cy + base * ny))
            path.addLine(to: CGPoint(x: cx + tip * cos(angle),
                                     y: cy + tip * sin(angle)))
            path.addLine(to: CGPoint(x: cx - base * nx, y: cy - base * ny))
            path.closeSubpath()
        }
        let hub = size * 0.14
        path.addEllipse(in: CGRect(x: cx - hub, y: cy - hub,
                                   width: hub * 2, height: hub * 2))
        return path
    }

    // MARK: - openai — the blossom reduced to what survives at 11pt: a
    // six-lobed rosette around a hexagonal core. The real mark's woven
    // over-and-under is invisible at this size, so it is dropped rather
    // than approximated — the lobe count, the roundness and the hexagon
    // are what make it read (mirror of popover_draw._draw_openai).

    private func openAIPath(size: CGFloat) -> Path {
        var path = Path()
        let cx = size / 2, cy = size / 2
        let tau = CGFloat.pi * 2
        let peak = size * 0.46      // lobe tip
        let valley = peak * 0.78    // the dip between two lobes
        let core = size * 0.23
        func polar(_ r: CGFloat, _ angle: CGFloat) -> CGPoint {
            CGPoint(x: cx + r * cos(angle), y: cy + r * sin(angle))
        }
        path.move(to: polar(valley, 0))
        for k in 0..<6 {
            let here = tau * CGFloat(k) / 6, next = tau * CGFloat(k + 1) / 6
            // Both control points sit out at `peak`, splayed a half-lobe
            // apart, rounding the lobe off instead of pulling it to a point.
            path.addCurve(to: polar(valley, next),
                          control1: polar(peak, here + tau / 24),
                          control2: polar(peak, next - tau / 24))
        }
        path.closeSubpath()
        // The core is rotated a half-lobe against the rosette so its
        // corners point at the lobes rather than at the dips between them —
        // aligned the other way it reads as a six-pointed star.
        for k in 0..<6 {
            let point = polar(core, tau * CGFloat(k) / 6 + tau / 12)
            if k == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
        path.closeSubpath()
        return path
    }

    // MARK: - clock — a stroked circle with hour/minute hands, sitting
    // immediately left of a countdown (mirror of popover_draw._draw_clock).

    private func clockPath(size: CGFloat) -> Path {
        var path = Path()
        let cx = size / 2, cy = size / 2
        let r = size * 0.42
        path.addEllipse(in: CGRect(x: cx - r, y: cy - r, width: r * 2, height: r * 2))
        path.move(to: CGPoint(x: cx, y: cy))
        path.addLine(to: CGPoint(x: cx, y: cy - r * 0.55))          // hour hand
        path.move(to: CGPoint(x: cx, y: cy))
        path.addLine(to: CGPoint(x: cx + r * 0.6, y: cy - r * 0.1)) // minute hand
        return path
    }

    // MARK: - warn — a stroked triangle with a vertical stem, plus a
    // filled dot (mirror of popover_draw._draw_warn's outline+dot split).

    private func warnOutlinePath(size: CGFloat) -> Path {
        var path = Path()
        let cx = size / 2, cy = size / 2
        let r = size * 0.46
        let top = CGPoint(x: cx, y: cy - r)
        let left = CGPoint(x: cx - r * 0.92, y: cy + r * 0.75)
        let right = CGPoint(x: cx + r * 0.92, y: cy + r * 0.75)
        path.move(to: top)
        path.addLine(to: right)
        path.addLine(to: left)
        path.closeSubpath()
        path.move(to: CGPoint(x: cx, y: cy - r * 0.35))
        path.addLine(to: CGPoint(x: cx, y: cy + r * 0.2))
        return path
    }

    private func warnDotPath(size: CGFloat) -> Path {
        let cx = size / 2, cy = size / 2
        let r = size * 0.46
        let dotR = size * 0.05
        return Path(ellipseIn: CGRect(x: cx - dotR, y: cy + r * 0.5 - dotR,
                                      width: dotR * 2, height: dotR * 2))
    }

    // MARK: - overview — four rounded squares in a 2x2 grid, filled
    // (mirror of popover_draw._draw_overview). Stage 04 drew this without
    // wiring it into any view yet; stage 05's Overview tab is that "future
    // tab" — its pill in PopoverView.tabButton reaches for it exactly like
    // the claude/openai marks do.

    private func overviewPath(size: CGFloat) -> Path {
        var path = Path()
        let cell = size * 0.38
        let gap = size * 0.12
        let corner = cell * 0.3
        let cx = size / 2, cy = size / 2
        let signs: [CGFloat] = [-1, 1]
        for dx in signs {
            for dy in signs {
                let x = cx + dx * (cell + gap) / 2 - cell / 2
                let y = cy + dy * (cell + gap) / 2 - cell / 2
                path.addPath(Path(roundedRect: CGRect(x: x, y: y, width: cell, height: cell),
                                  cornerRadius: corner))
            }
        }
        return path
    }

    // MARK: - pause — two rounded vertical bars, filled (mirror of
    // popover_draw._draw_pause). Prefixes the footer's "Update held"
    // label the same way the existing "pause.circle" SF Symbol already
    // does there — that call site is untouched; this exists for the
    // Python side, which had no icon there before stage 04 at all.

    private func pausePath(size: CGFloat) -> Path {
        var path = Path()
        let barW = size * 0.22
        let barH = size * 0.78
        let gap = size * 0.18
        let cx = size / 2, cy = size / 2
        let top = cy - barH / 2
        let signs: [CGFloat] = [-1, 1]
        for dx in signs {
            let x = cx + dx * (gap / 2 + barW / 2) - barW / 2
            path.addPath(Path(roundedRect: CGRect(x: x, y: top, width: barW, height: barH),
                              cornerRadius: barW / 2))
        }
        return path
    }
}
