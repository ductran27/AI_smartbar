// The status palette (dark-only design; must match model.RGB, which the
// Linux badge renders from): #2EA652 #D9A621 #E4604B #CC2F2F, then #8B5CF6
// for a spent limit, and #737373 gray for "no measurement".
import AppKit
import SwiftUI

extension Status {
    var nsColor: NSColor {
        switch self {
        case .green: return NSColor(red: 0.18, green: 0.65, blue: 0.32, alpha: 1)
        case .yellow: return NSColor(red: 0.85, green: 0.65, blue: 0.13, alpha: 1)
        case .low: return NSColor(red: 0.894, green: 0.376, blue: 0.294, alpha: 1)
        case .critical: return NSColor(red: 0.80, green: 0.184, blue: 0.184, alpha: 1)
        case .full: return NSColor(red: 0.545, green: 0.361, blue: 0.965, alpha: 1)
        case .gray: return NSColor(white: 0.45, alpha: 1)
        }
    }

    var color: Color { Color(nsColor: nsColor) }
}
