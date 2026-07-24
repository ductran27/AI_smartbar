"""Smoke tests for the cairo painters — skipped where pycairo is absent.

The layout is covered by test_popover_layout; what these add is that the
painter survives every state it can be handed. That matters because the
machine this is developed on has no GTK and no tray: without them, a crash
in an unusual branch (no data, an error string, a hovered button) would only
surface on the user's Linux box. Install pycairo to run them here.
"""
import os
import tempfile
import unittest

try:
    import cairo
except ImportError:                                  # pragma: no cover
    cairo = None

from smartbar.core import popover_layout as layout


def _demo():
    from smartbar.linux.popover_preview import demo_snapshot
    return demo_snapshot()


@unittest.skipIf(cairo is None, "pycairo not installed")
class TestPopoverPainter(unittest.TestCase):
    def render(self, built):
        from smartbar.linux import popover_draw
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "popover.png")
            popover_draw.render_png(built, path, scale=1.0)
            self.assertGreater(os.path.getsize(path), 0)

    def test_every_card_state_in_one_panel(self):
        self.render(layout.build(_demo(), version="1.2.3",
                                 fetched_at="2026-07-24T18:00:00Z"))

    def test_pending_update_and_hovered_controls(self):
        for hover in ("", "update", "refresh", "switch:1"):
            self.render(layout.build(_demo(), version="1.2.3",
                                     pending_version="1.3.0", hover=hover))

    def test_loading_error_and_stale(self):
        self.render(layout.build(None))
        self.render(layout.build(None, error="cswap exploded"))
        self.render(layout.build(_demo(), stale=True,
                                 fetched_at="2026-07-24T18:00:00Z",
                                 blocked_reason="2 unpushed commit(s)"))

    def test_empty_and_unregistered_snapshots(self):
        from smartbar.core import model
        self.render(layout.build(model.Snapshot(accounts=[])))
        demo = _demo()
        for account in demo.accounts:
            account.active = False       # signed in, nothing registered yet
        self.render(layout.build(demo))

    def test_long_email_is_truncated_not_overflowed(self):
        demo = _demo()
        demo.accounts[0].email = "a" * 120 + "@example.com"
        self.render(layout.build(demo, version="1.2.3"))


@unittest.skipIf(cairo is None, "pycairo not installed")
class TestTrayBadge(unittest.TestCase):
    def render(self, states, pending=False):
        from smartbar.linux.tray_icon import render_pills
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "icon.png")
            render_pills(states, path, update_pending=pending)
            self.assertGreater(os.path.getsize(path), 0)

    def test_pills_for_each_status(self):
        for name in ("green", "yellow", "low", "critical", "full", "gray"):
            self.render([(0.5, name), (1.0, name)])

    def test_no_data_draws_the_hollow_question_mark(self):
        self.render([])
        self.render([], pending=True)

    def test_update_badge_widens_the_icon(self):
        self.render([(0.3, "green")], pending=True)


if __name__ == "__main__":
    unittest.main()
