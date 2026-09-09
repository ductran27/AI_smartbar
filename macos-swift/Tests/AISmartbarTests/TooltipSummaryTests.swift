// Tests for the menu-bar hover tooltip's per-account rendering
// (Account.tooltip(now:)). Pure string logic against a fixed clock, so the
// reset countdowns are deterministic — no UI, no live cswap fetch.
import XCTest
@testable import AISmartbar

final class TooltipSummaryTests: XCTestCase {
    // A fixed "now" so every countdown below is exact.
    private let now = TimeRemaining.parseISO("2026-09-09T12:00:00+00:00")!

    private func metric(
        key: String, label: String, pct: Double,
        resetsAt: String, countdown: String
    ) -> Metric {
        Metric(key: key, label: label, short: label, pct: pct,
               resetsAt: resetsAt, countdown: countdown)
    }

    private func account(email: String, metrics: [Metric]) -> Account {
        Account(number: 1, email: email, org: "", active: true, ok: true,
                status: "ok", metrics: metrics, fetchedAt: nil)
    }

    func testShowsEmailThenEachMetricWithLiveReset() {
        let acct = account(email: "work@acme.com", metrics: [
            metric(key: "5h", label: "5h", pct: 47,
                   resetsAt: "2026-09-09T14:13:00+00:00", countdown: "2h 13m"),
            metric(key: "7d", label: "7d", pct: 30,
                   resetsAt: "2026-09-12T16:00:00+00:00", countdown: "3d 4h"),
        ])

        XCTAssertEqual(acct.tooltip(now: now), """
        work@acme.com
        5h 47% used · resets in 2h 13m
        7d 30% used · resets in 3d 4h
        """)
    }

    func testDropsResetClauseWhenNoCountdownAvailable() {
        // Unparseable resetsAt AND no preformatted countdown → "resets in "
        // would be blank; the whole clause is dropped instead.
        let acct = account(email: "work@acme.com", metrics: [
            metric(key: "spend", label: "Spend", pct: 62,
                   resetsAt: "", countdown: ""),
        ])

        XCTAssertEqual(acct.tooltip(now: now), """
        work@acme.com
        Spend 62% used
        """)
    }

    func testFallsBackToPreformattedCountdownWhenResetUnparseable() {
        // resetsAt can't be parsed, but cswap gave a fetch-time countdown —
        // stale beats blank, matching Metric.liveCountdown.
        let acct = account(email: "work@acme.com", metrics: [
            metric(key: "5h", label: "5h", pct: 88,
                   resetsAt: "", countdown: "4h 3m"),
        ])

        XCTAssertEqual(acct.tooltip(now: now), """
        work@acme.com
        5h 88% used · resets in 4h 3m
        """)
    }
}
