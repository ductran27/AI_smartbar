// The Swift twin of popover_theme.py's chrome tokens: the two grounds
// (window/card background) and four inks (chalk/mist/dim/spent) that give
// the popover its own blue-shifted, cool-grey look instead of borrowing
// macOS's neutral system greys. Pinned by tests/test_popover_theme_parity.py,
// which reads this file as source text, so a value changed on one side
// without the other fails the ordinary unit suite with no Xcode required.
import SwiftUI

enum Palette {
    static let windowBG = Color(red: 0.059, green: 0.071, blue: 0.086)
    static let cardBG = Color(red: 0.090, green: 0.110, blue: 0.133)
    static let chalk = Color(red: 0.914, green: 0.929, blue: 0.949)
    static let mist = Color(red: 0.596, green: 0.639, blue: 0.690)
    static let dim = Color(red: 0.361, green: 0.400, blue: 0.447)
    static let spent = Color(red: 0.725, green: 0.757, blue: 0.796)
}
