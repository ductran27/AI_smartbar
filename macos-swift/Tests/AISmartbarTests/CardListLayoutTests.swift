// Tests for the pure layout decisions behind the account-card list
// (CardListLayout). No UI: these pin the two behaviours the popover got
// wrong when many Claude accounts were present — the list collapsing to a
// fixed sliver, and the list growing past its cap — which a bare
// `ScrollView { … }.frame(maxHeight:)` produced inside the auto-sizing
// MenuBarExtra window (SwiftUI hands such a ScrollView its ~10pt
// "unspecified" height, so the cards were there but scrolled out of a
// too-short viewport until a provider-tab switch forced a re-layout).
import XCTest
@testable import AISmartbar

final class CardListLayoutTests: XCTestCase {
    private let scrollsPast = 4
    private let maxHeight: CGFloat = 555

    // MARK: scrolls(count:scrollsPast:)

    func testDoesNotScrollAtOrBelowThreshold() {
        XCTAssertFalse(CardListLayout.scrolls(count: 0, scrollsPast: scrollsPast))
        XCTAssertFalse(CardListLayout.scrolls(count: 4, scrollsPast: scrollsPast))
    }

    func testScrollsOnlyPastThreshold() {
        XCTAssertTrue(CardListLayout.scrolls(count: 5, scrollsPast: scrollsPast))
        XCTAssertTrue(CardListLayout.scrolls(count: 9, scrollsPast: scrollsPast))
    }

    // MARK: height(contentHeight:maxHeight:)

    /// A tall list caps at maxHeight and scrolls — it must never be shorter
    /// (the sliver the bug produced).
    func testTallListCapsAtMax() {
        XCTAssertEqual(
            CardListLayout.height(contentHeight: 900, maxHeight: maxHeight),
            maxHeight)
    }

    /// A short list takes exactly its content height — no dead space below
    /// the cards, and no collapse.
    func testShortListTakesContentHeight() {
        XCTAssertEqual(
            CardListLayout.height(contentHeight: 300, maxHeight: maxHeight),
            300)
    }

    /// The height is always a function of the content, never a fixed value
    /// independent of it — the exact property the collapsing ScrollView
    /// violated.
    func testHeightTracksContentUpToTheCap() {
        for content in stride(from: CGFloat(0), through: 800, by: 50) {
            let h = CardListLayout.height(contentHeight: content,
                                          maxHeight: maxHeight)
            XCTAssertEqual(h, min(content, maxHeight))
            XCTAssertLessThanOrEqual(h, maxHeight)
        }
    }
}
