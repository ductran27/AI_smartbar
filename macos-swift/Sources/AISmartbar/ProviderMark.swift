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
        case "claude": return claudePath(size: size)
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
        case "overview": return overviewPath(size: size)
        case "pause": return pausePath(size: size)
        case "warn": return warnDotPath(size: size)
        default: return Path()
        }
    }

    // MARK: - claude — a simplified wordless "A": two strokes from an
    // apex plus a crossbar. Not a reproduction of Anthropic's mark, just
    // a short shape recognisable enough to tell the tab apart at a
    // glance, the way the label beside it already does in words (mirror
    // of popover_draw._draw_claude).

    private func claudePath(size: CGFloat) -> Path {
        var path = Path()
        let cx = size / 2, cy = size / 2
        let half = size * 0.34
        let top = cy - size * 0.38
        let bottom = cy + size * 0.38
        path.move(to: CGPoint(x: cx, y: top))
        path.addLine(to: CGPoint(x: cx - half, y: bottom))
        path.move(to: CGPoint(x: cx, y: top))
        path.addLine(to: CGPoint(x: cx + half, y: bottom))
        let barY = cy + size * 0.12
        let barHalf = half * 0.55
        path.move(to: CGPoint(x: cx - barHalf, y: barY))
        path.addLine(to: CGPoint(x: cx + barHalf, y: barY))
        return path
    }

    // MARK: - openai — a hollow hexagon with two internal spokes. A
    // short, generic "knot", not a reproduction of their logo (mirror of
    // popover_draw._draw_openai).

    private func openAIPath(size: CGFloat) -> Path {
        var path = Path()
        let cx = size / 2, cy = size / 2
        let r = size * 0.42
        var points: [CGPoint] = []
        for i in 0..<6 {
            let angle = CGFloat(i) * .pi / 3 - .pi / 2
            points.append(CGPoint(x: cx + r * cos(angle), y: cy + r * sin(angle)))
        }
        path.move(to: points[0])
        for point in points.dropFirst() { path.addLine(to: point) }
        path.closeSubpath()
        path.move(to: CGPoint(x: cx, y: cy))
        path.addLine(to: points[0])
        path.move(to: CGPoint(x: cx, y: cy))
        path.addLine(to: points[3])
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
