// The status palette, once per appearance — must match model.RGB and
// model.RGB_LIGHT, which the Linux badge and the cairo painter render from.
// Retuned so the used-ramp reads as one family that warms up rather than four
// unrelated traffic-light colors; gray is not neutral either, so it takes the
// same red/green/blue form as the rest instead of the white(:) shorthand.
//
// Pinned by tests/test_model_parity.py, which reads this file as source text
// and scrapes each ramp from ITS OWN property block — so the two must stay
// separately declared, one `switch self` each, rather than being merged into
// a single switch that returns a pair.
import AppKit
import SwiftUI

extension Status {
    /// The ramp read against a DARK ground: #3DBE8B #D8A64A #DD7A45 #D9534F,
    /// then #7C6BE8 for a spent limit and #5C6672 for "no measurement".
    var darkNSColor: NSColor {
        switch self {
        case .green: return NSColor(red: 0.239, green: 0.745, blue: 0.545, alpha: 1)
        case .yellow: return NSColor(red: 0.847, green: 0.651, blue: 0.290, alpha: 1)
        case .low: return NSColor(red: 0.867, green: 0.478, blue: 0.271, alpha: 1)
        case .critical: return NSColor(red: 0.851, green: 0.325, blue: 0.310, alpha: 1)
        case .full: return NSColor(red: 0.486, green: 0.420, blue: 0.910, alpha: 1)
        case .gray: return NSColor(red: 0.361, green: 0.400, blue: 0.447, alpha: 1)
        }
    }

    /// The same ramp retuned for a LIGHT ground. Darkened rather than
    /// reused: the values above were picked against a near-black panel, and
    /// on white the green and the yellow fall under the 3:1 a filled bar
    /// needs — the two most common states, washing out exactly when someone
    /// glances at them.
    var lightNSColor: NSColor {
        switch self {
        case .green: return NSColor(red: 0.086, green: 0.580, blue: 0.396, alpha: 1)
        case .yellow: return NSColor(red: 0.706, green: 0.463, blue: 0.043, alpha: 1)
        case .low: return NSColor(red: 0.749, green: 0.310, blue: 0.106, alpha: 1)
        case .critical: return NSColor(red: 0.769, green: 0.176, blue: 0.153, alpha: 1)
        case .full: return NSColor(red: 0.353, green: 0.278, blue: 0.827, alpha: 1)
        case .gray: return NSColor(red: 0.545, green: 0.588, blue: 0.639, alpha: 1)
        }
    }

    /// The popover's status colour for the appearance it is being drawn in.
    func color(in scheme: ColorScheme) -> Color {
        Color(nsColor: scheme == .light ? lightNSColor : darkNSColor)
    }

    /// The MENU BAR icon's colour, which is deliberately not appearance-
    /// aware: the tray is its own surface with its own contrast rules (macOS
    /// composites it over a bar that is neither the popover's window nor the
    /// desktop), and it has always drawn the dark-ground ramp. Retuning it is
    /// a separate question from what the popover does.
    var nsColor: NSColor { darkNSColor }
}
