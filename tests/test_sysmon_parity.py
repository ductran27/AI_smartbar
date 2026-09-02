"""Cross-language pins for the System tab (source-scrape, no Swift toolchain).

Runs on Linux/CI like the other *_parity tests: it reads the Swift files as
text and asserts the "one shared answer" boundary — Swift decodes the
payload and lays it out, but holds no rule, no threshold, no env-var name and
no wording of its own; those live in core/sysmon.py + sysmon_runner.py.
"""
from __future__ import annotations

import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SWIFT_DIR = REPO / "macos-swift" / "Sources" / "AISmartbar"


def _swift(name):
    return (SWIFT_DIR / name).read_text(encoding="utf-8")


class TestSwiftMapsNothing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.all_swift = "".join(
            path.read_text(encoding="utf-8")
            for path in sorted(SWIFT_DIR.glob("*.swift")))

    def test_no_rule_text_leaks_into_swift(self):
        # Rule fragments + env-var names + policy words all live in Python.
        for marker in ("cdp-prof", "esbuild", "shell-snapshots",
                       "ms-playwright", "SMARTBAR_SYSMON", "AUTOKILL",
                       "hot_threshold", "junk rules"):
            self.assertNotIn(marker, self.all_swift,
                             f"sysmon policy leaked into Swift: {marker}")

    def test_sysmon_swift_never_reads_process_termination_status(self):
        # Same discipline as the OpenAI tab: the System-tab Swift does not
        # second-guess the launcher's own success/failure wording via exit
        # codes (other, older files predate this convention).
        for name in ("SystemStatus.swift", "SystemView.swift",
                     "Launcher.swift"):
            self.assertNotIn("terminationStatus", _swift(name))


class TestCadencesArePinned(unittest.TestCase):
    def test_poll_and_stream_intervals(self):
        status = _swift("SystemStatus.swift")
        self.assertIn("pollInterval: TimeInterval = 60", status)
        self.assertIn("streamInterval: TimeInterval = 1", status)


class TestLauncherIsTheSharedSpawner(unittest.TestCase):
    def test_system_files_spawn_through_launcher_not_inline(self):
        # SystemStatus/SystemView/OpenAIStatus construct no Process() of
        # their own — they go through Launcher, the one place the PATH fix
        # and checkout resolution live.
        for name in ("SystemStatus.swift", "SystemView.swift",
                     "OpenAIStatus.swift"):
            self.assertNotIn("Process()", _swift(name),
                             f"{name} should spawn via Launcher, not inline")

    def test_launcher_owns_the_path_fix(self):
        launcher = _swift("Launcher.swift")
        self.assertIn("/opt/homebrew/bin", launcher)
        self.assertIn("bin/ai-smartbar", launcher)


class TestSystemViewDecodesTheSharedShape(unittest.TestCase):
    def test_models_declares_the_payload(self):
        models = _swift("Models.swift")
        for symbol in ("struct SystemPayload", "struct ProcRow",
                       "struct SysCPU", "struct SysHistory"):
            self.assertIn(symbol, models)

    def test_memory_carries_a_history_of_the_same_shape_as_cpu(self):
        # Memory now has its own 60-minute trend, decoded as the same
        # SysHistory the CPU history uses (core/sysmon emits both from
        # history_block), so both draw as one chart.
        models = _swift("Models.swift")
        mem = models.split("struct SysMem", 1)[1].split("}", 1)[0]
        self.assertIn("history: SysHistory", mem)

    def test_the_two_histories_draw_as_trend_charts_not_bars(self):
        # Swift maps nothing: it lays the payload out. The 60-minute CPU and
        # memory rows are the TrendChart (an area chart), the time-over-time
        # counterpart to the per-core bar strip.
        view = _swift("SystemView.swift")
        self.assertIn("TrendChart(values: payload.history.pct)", view)
        self.assertIn("TrendChart(values: payload.mem.history.pct)", view)

    def test_stream_stops_when_the_tab_leaves(self):
        # The stream must be tied to the view's lifetime so a closed popover
        # never leaves a sampler running — the exact leak this feature exists
        # to catch.
        view = _swift("SystemView.swift")
        self.assertIn(".onAppear { system.startStream() }", view)
        self.assertIn(".onDisappear { system.stopStream() }", view)


if __name__ == "__main__":
    unittest.main()
