// The Swift twin of popover_theme.py's Scheme: every colour the popover
// paints, once per appearance. The panel follows the system appearance now,
// so this is two palettes rather than the single dark one it used to be, and
// `Palette.of(colorScheme)` is how a view picks its side.
//
// Pinned by tests/test_popover_theme_parity.py, which reads this file as
// SOURCE TEXT — so a value changed on one side without the other fails the
// ordinary unit suite with no Swift toolchain and no Xcode required. Every
// entry is written in the SAME four-argument form on ONE line, including
// opaque colours that could have omitted `opacity:`, purely so that scraper
// stays a single regex instead of a set of special cases.
import SwiftUI

struct Palette {
    let windowBG: Color
    let cardBG: Color
    let cardBorder: Color
    let text: Color
    let textSecondary: Color
    let textTertiary: Color
    let textSpent: Color
    let barTrack: Color
    let pace: Color
    let buttonBG: Color
    let buttonBGHover: Color
    let buttonBorder: Color
    let buttonDisabled: Color
    let tabBGSelected: Color
    let tabBGHover: Color
    let tabBG: Color
    let accent: Color
    let accentHover: Color
    let accentText: Color
    let danger: Color
    let dangerHover: Color
    let warning: Color

    static let dark = Palette(
        windowBG: Color(red: 0.059, green: 0.071, blue: 0.086, opacity: 1.0),
        cardBG: Color(red: 0.090, green: 0.110, blue: 0.133, opacity: 1.0),
        cardBorder: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 0.06),
        text: Color(red: 0.914, green: 0.929, blue: 0.949, opacity: 1.0),
        textSecondary: Color(red: 0.596, green: 0.639, blue: 0.690, opacity: 1.0),
        textTertiary: Color(red: 0.361, green: 0.400, blue: 0.447, opacity: 1.0),
        textSpent: Color(red: 0.725, green: 0.757, blue: 0.796, opacity: 1.0),
        barTrack: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 0.09),
        pace: Color(red: 0.090, green: 0.110, blue: 0.133, opacity: 1.0),
        buttonBG: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 0.12),
        buttonBGHover: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 0.20),
        buttonBorder: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 0.18),
        buttonDisabled: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 0.05),
        tabBGSelected: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 0.16),
        tabBGHover: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 0.12),
        tabBG: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 0.06),
        accent: Color(red: 0.0, green: 0.48, blue: 1.0, opacity: 1.0),
        accentHover: Color(red: 0.15, green: 0.56, blue: 1.0, opacity: 1.0),
        accentText: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 1.0),
        danger: Color(red: 0.80, green: 0.184, blue: 0.184, opacity: 1.0),
        dangerHover: Color(red: 0.88, green: 0.25, blue: 0.25, opacity: 1.0),
        warning: Color(red: 1.0, green: 0.62, blue: 0.20, opacity: 1.0))

    static let light = Palette(
        windowBG: Color(red: 0.949, green: 0.957, blue: 0.969, opacity: 1.0),
        cardBG: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 1.0),
        cardBorder: Color(red: 0.0, green: 0.0, blue: 0.0, opacity: 0.07),
        text: Color(red: 0.086, green: 0.106, blue: 0.133, opacity: 1.0),
        textSecondary: Color(red: 0.361, green: 0.400, blue: 0.447, opacity: 1.0),
        textTertiary: Color(red: 0.545, green: 0.588, blue: 0.639, opacity: 1.0),
        textSpent: Color(red: 0.263, green: 0.294, blue: 0.341, opacity: 1.0),
        barTrack: Color(red: 0.0, green: 0.0, blue: 0.0, opacity: 0.08),
        pace: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 1.0),
        buttonBG: Color(red: 0.0, green: 0.0, blue: 0.0, opacity: 0.06),
        buttonBGHover: Color(red: 0.0, green: 0.0, blue: 0.0, opacity: 0.11),
        buttonBorder: Color(red: 0.0, green: 0.0, blue: 0.0, opacity: 0.14),
        buttonDisabled: Color(red: 0.0, green: 0.0, blue: 0.0, opacity: 0.05),
        tabBGSelected: Color(red: 0.0, green: 0.0, blue: 0.0, opacity: 0.12),
        tabBGHover: Color(red: 0.0, green: 0.0, blue: 0.0, opacity: 0.09),
        tabBG: Color(red: 0.0, green: 0.0, blue: 0.0, opacity: 0.05),
        accent: Color(red: 0.0, green: 0.42, blue: 0.90, opacity: 1.0),
        accentHover: Color(red: 0.0, green: 0.36, blue: 0.82, opacity: 1.0),
        accentText: Color(red: 1.0, green: 1.0, blue: 1.0, opacity: 1.0),
        danger: Color(red: 0.76, green: 0.145, blue: 0.145, opacity: 1.0),
        dangerHover: Color(red: 0.66, green: 0.11, blue: 0.11, opacity: 1.0),
        warning: Color(red: 0.72, green: 0.42, blue: 0.04, opacity: 1.0))

    /// The palette for an appearance. `.dark` is the fallback for anything
    /// SwiftUI adds later, matching popover_theme.scheme_for's own default —
    /// this panel spent its whole life dark, so that is where an unknown
    /// appearance is least surprising.
    static func of(_ scheme: ColorScheme) -> Palette {
        scheme == .light ? .light : .dark
    }
}
