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

    // MARK: step(frames:viewport:)

    /// Five 150pt cards 9pt apart, scrolled `offset` points down, in a 555pt
    /// viewport — the shape of the real list.
    private func frames(offset: CGFloat) -> [CardFrame] {
        (0..<5).map { i in
            let top = CGFloat(i) * 159 - offset
            return CardFrame(id: "c\(i)", minY: top, maxY: top + 150)
        }
    }

    /// One card per press: down brings up the card right after the top one,
    /// not the first card cut off at the bottom (a page — c3, three cards on,
    /// read as sliding too far).
    func testAtTheTopOnlyDownIsOffered() {
        let step = CardListLayout.step(frames: frames(offset: 0), viewport: 555)
        XCTAssertNil(step.up)
        XCTAssertEqual(step.down, "c1")
        XCTAssertTrue(step.scrolls)
    }

    func testAtTheBottomOnlyUpIsOffered() {
        // Content is 786pt tall; scrolled to its end.
        let step = CardListLayout.step(frames: frames(offset: 231), viewport: 555)
        XCTAssertNil(step.down)
        // c1 runs from -72 to 78: the card cut off above.
        XCTAssertEqual(step.up, "c1")
    }

    /// A card the last press landed flush with the edge counts as in view,
    /// even when layout rounding leaves it a fraction of a point past it —
    /// otherwise the next press would scroll to the card already there.
    func testACardLandedOnTheEdgeIsNotAStepAway() {
        let step = CardListLayout.step(frames: frames(offset: 159.4), viewport: 555)
        XCTAssertEqual(step.up, "c0")
        XCTAssertEqual(step.down, "c2")
    }

    func testAListThatFitsDoesNotScroll() {
        let short = Array(frames(offset: 0).prefix(3))
        let step = CardListLayout.step(frames: short, viewport: 555)
        XCTAssertEqual(step, CardListStep())
        XCTAssertFalse(step.scrolls)
    }

    /// Preferences arrive in no promised order; the step must not depend on it.
    func testFrameOrderDoesNotMatter() {
        let shuffled = frames(offset: 100).reversed()
        XCTAssertEqual(CardListLayout.step(frames: Array(shuffled), viewport: 555),
                       CardListLayout.step(frames: frames(offset: 100), viewport: 555))
    }
}
