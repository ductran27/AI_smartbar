// Which commit THIS bundle was built from — the other half of its identity.
//
// AppVersion.current names the last RELEASE, and only install/release.sh ever
// moves it. On a channel=main device that is not the same question: the
// checkout follows origin/main continuously while the version sits still, so
// the app can be many commits newer than the number it prints and still be
// telling the truth about its version. About said "v1.0.0" while running code
// from seven commits later, and nothing on screen could tell the two apart.
//
// install/macos-swift.sh stamps the sha into the bundle's Info.plist at build
// time. Deliberately NOT a generated source file like Version.swift: writing
// one on every build would leave the checkout dirty, which is exactly what
// release.sh's clean-tree gate and the updater's work-in-progress policy
// refuse to proceed through.
import Foundation

enum AppBuild {
    /// Info.plist key written by install/macos-swift.sh. Custom rather than
    /// CFBundleVersion, whose documented format is period-separated integers —
    /// a sha is not one, and misusing the key would be a second lie to fix
    /// the first.
    static let infoKey = "SMARTBARBuildSHA"

    /// How much of the sha is worth showing: git's own default abbreviation.
    static let abbrev = 7

    /// The full sha, or "" when the app runs unbundled (`swift run`) or was
    /// built from a checkout with no git available. Unknown is a normal
    /// answer here, and every reader below degrades to naming the version
    /// alone rather than showing an empty pair of brackets.
    static let sha: String =
        Bundle.main.object(forInfoDictionaryKey: infoKey) as? String ?? ""

    /// The sha as the UI shows it, or "" when unknown.
    static var short: String { String(sha.prefix(abbrev)) }

    /// What About appends after the version: " (da43ea0)", or "" when the
    /// sha is unknown.
    static var suffix: String { short.isEmpty ? "" : " (\(short))" }
}
