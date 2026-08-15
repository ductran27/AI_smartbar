// The status palette (dark-only design; must match model.RGB, which the
// Linux badge renders from): #3DBE8B #D8A64A #DD7A45 #D9534F, then #7C6BE8
// for a spent limit, and #5C6672 gray for "no measurement". Retuned so the
// used-ramp reads as one family that warms up rather than four unrelated
// traffic-light colors; gray is no longer neutral, so it takes the same
// red/green/blue form as the rest instead of the white(:) shorthand.
import AppKit
import SwiftUI

extension Status {
    var nsColor: NSColor {
        switch self {
        case .green: return NSColor(red: 0.239, green: 0.745, blue: 0.545, alpha: 1)
        case .yellow: return NSColor(red: 0.847, green: 0.651, blue: 0.290, alpha: 1)
        case .low: return NSColor(red: 0.867, green: 0.478, blue: 0.271, alpha: 1)
        case .critical: return NSColor(red: 0.851, green: 0.325, blue: 0.310, alpha: 1)
        case .full: return NSColor(red: 0.486, green: 0.420, blue: 0.910, alpha: 1)
        case .gray: return NSColor(red: 0.361, green: 0.400, blue: 0.447, alpha: 1)
        }
    }

    var color: Color { Color(nsColor: nsColor) }
}
