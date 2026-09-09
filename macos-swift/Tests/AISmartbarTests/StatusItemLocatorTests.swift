// Tests for the menu-bar tooltip's write-dedup (StatusItemLocator).
//
// Re-assigning NSView.toolTip reinstalls the view's tooltip tracking
// rectangle; doing that while the tooltip is on screen makes it linger and
// dismiss slowly. The MenuBarExtra label closure re-runs on every SwiftUI
// publish (five app-level stores, several ticks a minute between them), so the
// locator must apply a write only when the text has actually changed — the
// same "only write when the value moved" rule UsageStore uses for @Published.
import XCTest
@testable import AISmartbar

final class StatusItemLocatorTests: XCTestCase {
    func testAppliesTheFirstTooltipText() {
        let locator = StatusItemLocator()
        XCTAssertTrue(locator.shouldApplyTooltip("AI smartbar: loading"))
    }

    func testSkipsAnUnchangedTooltipText() {
        let locator = StatusItemLocator()
        _ = locator.shouldApplyTooltip("work@acme.com\n5h 47% used")
        XCTAssertFalse(locator.shouldApplyTooltip("work@acme.com\n5h 47% used"))
    }

    func testAppliesAgainWhenTheCountdownTicksThenHoldsForTheNewText() {
        let locator = StatusItemLocator()
        _ = locator.shouldApplyTooltip("work@acme.com\n5h 47% used · resets in 2h 13m")
        XCTAssertTrue(locator.shouldApplyTooltip("work@acme.com\n5h 47% used · resets in 2h 12m"))
        XCTAssertFalse(locator.shouldApplyTooltip("work@acme.com\n5h 47% used · resets in 2h 12m"))
    }
}
