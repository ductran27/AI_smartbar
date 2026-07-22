// Draws the twin-pill menu-bar icon: one vertical pill per metric group
// (general limit first, then per-model buckets), fill anchored to the
// bottom = % tokens USED, rising as the budget is spent (a nearly full
// pill means nearly at the limit). Colored image, not a template — the
// fixed track shade reads on any menu-bar tint.
import AppKit

enum MenuBarIcon {
    static let pillWidth: CGFloat = 5
    static let pillHeight: CGFloat = 16
    static let gap: CGFloat = 2
    static let margin: CGFloat = 2

    static func image(for states: [(fraction: Double, status: Status)]) -> NSImage {
        let count = states.isEmpty ? 2 : states.count
        let width = margin * 2 + pillWidth * CGFloat(count) + gap * CGFloat(count - 1)
        let size = NSSize(width: width, height: pillHeight)
        let image = NSImage(size: size, flipped: false) { _ in
            for index in 0..<count {
                let x = margin + CGFloat(index) * (pillWidth + gap)
                let frame = NSRect(x: x, y: 0, width: pillWidth, height: pillHeight)
                if states.isEmpty {
                    // Hollow pill: no data (loading or >=3 failed refreshes).
                    let outline = NSBezierPath(
                        roundedRect: frame.insetBy(dx: 0.5, dy: 0.5),
                        xRadius: 2, yRadius: 2)
                    outline.lineWidth = 1
                    NSColor(white: 0.55, alpha: 0.8).setStroke()
                    outline.stroke()
                    continue
                }
                NSColor(white: 0.5, alpha: 0.45).setFill()
                NSBezierPath(roundedRect: frame, xRadius: 2.5, yRadius: 2.5).fill()
                let state = states[index]
                guard state.fraction > 0 else { continue }
                let fillHeight = max(2, pillHeight * CGFloat(min(state.fraction, 1)))
                let radius = min(2.5, fillHeight / 2)
                let fillRect = NSRect(x: x, y: 0, width: pillWidth, height: fillHeight)
                state.status.nsColor.setFill()
                NSBezierPath(roundedRect: fillRect, xRadius: radius, yRadius: radius).fill()
            }
            if states.isEmpty {
                let question = NSAttributedString(
                    string: "?",
                    attributes: [
                        .font: NSFont.systemFont(ofSize: 9, weight: .bold),
                        .foregroundColor: NSColor(white: 1, alpha: 0.75),
                    ])
                let bounds = question.size()
                question.draw(at: NSPoint(x: (width - bounds.width) / 2,
                                          y: (pillHeight - bounds.height) / 2))
            }
            return true
        }
        image.isTemplate = false
        return image
    }
}
