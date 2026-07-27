"""Device-presence policy: the count has to be right, or it is worse than absent.

These cover the ways a distributed count can lie — a device that died with
its ref still parked, a clock that disagrees, a ref that leaked, a remote we
could not reach — because the machine this is developed on cannot reproduce
any of them, and a wrong "(2)" is indistinguishable from a right one on
screen. tests/e2e-presence.sh then drives the same rules through real git.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from smartbar.core import model, presence


def beacon(device="dev1", label="box", epoch=1000, active="a" * 16):
    return presence.Beacon(device=device, label=label, epoch=epoch,
                           active=active,
                           ref=presence.encode_ref(device, label, epoch, active))


class TestAccountKey(unittest.TestCase):
    def test_normalises_case_and_whitespace(self):
        key = presence.account_key("Syu3cs@Virginia.EDU")
        self.assertEqual(key, presence.account_key("  syu3cs@virginia.edu  "))
        self.assertEqual(len(key), presence.KEY_LEN)
        self.assertRegex(key, r"^[0-9a-f]+$")

    def test_different_accounts_differ_and_empty_is_empty(self):
        self.assertNotEqual(presence.account_key("a@b.com"),
                            presence.account_key("c@d.com"))
        self.assertEqual(presence.account_key(""), "")
        self.assertEqual(presence.account_key(None), "")

    def test_the_address_itself_never_appears(self):
        # The ref is published to a repo; the address must not ride along.
        ref = presence.encode_ref("dev1", "box", 1000,
                                  presence.account_key("syu3cs@virginia.edu"))
        self.assertNotIn("syu3cs", ref)
        self.assertNotIn("@", ref)


class TestLabel(unittest.TestCase):
    def test_hostname_is_reduced_to_its_first_component(self):
        self.assertEqual(presence.sanitize_label("Ducs-MacBook.local"),
                         "ducs-macbook")

    def test_unusable_hostnames_still_publish(self):
        for raw in ("", None, "???", "...", "   "):
            self.assertEqual(presence.sanitize_label(raw), "device")

    def test_truncates_without_leaving_a_trailing_dash(self):
        label = presence.sanitize_label("a" * 23 + "-bbbb")
        self.assertLessEqual(len(label), presence.LABEL_MAX)
        self.assertFalse(label.endswith("-"))


class TestPlatformInTheLabel(unittest.TestCase):
    """A beacon otherwise says nothing about what a machine IS.

    Without this, "which of my devices is that, and is my Linux box even in
    the loop?" cannot be answered from the count — the hostname alone does
    not distinguish a Mac from a Linux box, since sanitize_label drops the
    domain that would have hinted at it.
    """

    def setUp(self):
        self.saved_platform = presence.sys.platform
        self.saved_label = os.environ.get("SMARTBAR_PRESENCE_LABEL")
        os.environ.pop("SMARTBAR_PRESENCE_LABEL", None)

    def tearDown(self):
        presence.sys.platform = self.saved_platform
        if self.saved_label is None:
            os.environ.pop("SMARTBAR_PRESENCE_LABEL", None)
        else:
            os.environ["SMARTBAR_PRESENCE_LABEL"] = self.saved_label

    def test_the_two_platforms_that_matter(self):
        for raw, want in (("darwin", "mac"), ("linux", "linux"),
                          ("linux2", "linux")):
            presence.sys.platform = raw
            self.assertEqual(presence.platform_tag(), want)

    def test_an_unknown_platform_still_yields_a_usable_label(self):
        for raw in ("freebsd14",):
            presence.sys.platform = raw
            tag = presence.platform_tag()
            self.assertTrue(tag)
            self.assertEqual(presence.sanitize_label(tag), tag)

    def test_win32_gets_its_own_short_tag_like_mac_and_linux(self):
        # win32 used to fall through to sanitize_label(raw) above, which
        # produced "win32" — functional, but the odd one out next to
        # "mac"/"linux". This is that fallthrough assertion, moved out and
        # updated now that platform_tag() has a dedicated win32 arm.
        presence.sys.platform = "win32"
        self.assertEqual(presence.platform_tag(), "win")

    def test_the_runner_puts_the_platform_in_front_of_the_hostname(self):
        from smartbar import presence_runner
        presence.sys.platform = "linux"
        with mock.patch.object(presence_runner.socket, "gethostname",
                               return_value="thinkpad.lan"):
            self.assertEqual(presence_runner.device_label(), "linux-thinkpad")
        presence.sys.platform = "darwin"
        with mock.patch.object(presence_runner.socket, "gethostname",
                               return_value="Ducs-MacBook.local"):
            self.assertEqual(presence_runner.device_label(), "mac-ducs-macbook")

    def test_the_prefix_survives_a_hostname_too_long_to_fit(self):
        # Truncation has to eat the hostname, not the part that says which
        # machine this is.
        from smartbar import presence_runner
        presence.sys.platform = "linux"
        with mock.patch.object(presence_runner.socket, "gethostname",
                               return_value="x" * 60):
            label = presence_runner.device_label()
        self.assertTrue(label.startswith("linux-"))
        self.assertLessEqual(len(label), presence.LABEL_MAX)

    def test_an_explicit_label_is_still_exactly_what_was_asked_for(self):
        from smartbar import presence_runner
        presence.sys.platform = "linux"
        os.environ["SMARTBAR_PRESENCE_LABEL"] = "kitchen-pi"
        self.assertEqual(presence_runner.device_label(), "kitchen-pi")
        os.environ["SMARTBAR_PRESENCE_LABEL"] = ""
        self.assertEqual(presence_runner.device_label(), "device")

    def test_a_prefixed_label_is_still_a_ref_git_accepts(self):
        # TestRefRoundTrip makes this promise for raw hostnames; the prefix
        # must not be the thing that finally produces an invalid ref.
        from smartbar import presence_runner
        for platform in ("darwin", "linux", "freebsd14"):
            presence.sys.platform = platform
            for host in ("", "???", "HOST.sub.example.com", "x" * 60, "-lead-"):
                with mock.patch.object(presence_runner.socket, "gethostname",
                                       return_value=host):
                    label = presence_runner.device_label()
                ref = presence.encode_ref("abc123", label, 1753380000,
                                          presence.account_key("a@b.com"))
                self.assertEqual(
                    subprocess.run(["git", "check-ref-format", ref]).returncode,
                    0, f"git rejected {ref!r} ({platform}, host {host!r})")
                self.assertEqual(presence.decode_ref(ref).label, label)


class TestRefRoundTrip(unittest.TestCase):
    def test_encode_then_decode_is_lossless(self):
        key = presence.account_key("a@b.com")
        ref = presence.encode_ref("abc123", "My-Box.local", 1753380000, key)
        decoded = presence.decode_ref(ref)
        self.assertEqual(decoded.device, "abc123")
        self.assertEqual(decoded.label, "my-box")
        self.assertEqual(decoded.epoch, 1753380000)
        self.assertEqual(decoded.active, key)

    def test_no_active_slot_round_trips_as_empty(self):
        ref = presence.encode_ref("abc123", "box", 1000, "")
        self.assertTrue(ref.endswith("/" + presence.NO_ACTIVE))
        self.assertEqual(presence.decode_ref(ref).active, "")

    def test_anything_not_ours_decodes_to_none(self):
        for ref in ("refs/heads/main", "refs/tags/v1.0.0", "",
                    "refs/smartbar/p2/abc123/box/1000/" + "a" * 16,  # newer schema
                    "refs/smartbar/p1/abc123/box/notanumber/" + "a" * 16,
                    "refs/smartbar/p1/abc123/box/1000/TOOSHORT",
                    "refs/smartbar/p1/abc123/box/1000",              # truncated
                    "refs/smartbar/p1/ABC123/box/1000/" + "a" * 16): # not lowercase
            self.assertIsNone(presence.decode_ref(ref), ref)

    def test_decode_all_skips_the_unreadable(self):
        good = presence.encode_ref("abc123", "box", 1000, "b" * 16)
        self.assertEqual([b.ref for b in
                          presence.decode_all([good, "refs/heads/main", None])],
                         [good])

    @unittest.skipIf(shutil.which("git") is None, "git not installed")
    def test_generated_refs_satisfy_git_itself(self):
        # Ref-name rules are git's, not ours: ask git rather than assume.
        for host in ("box", "Ducs-MacBook.local", "a b\tc", "~^:?*[\\",
                     "..", ".hidden", "end.lock", "@{now}", "-" * 30,
                     "ünïcödé", "x" * 200, "", "/"):
            ref = presence.encode_ref(presence.new_device_id(), host,
                                      1753380000, presence.account_key("a@b.com"))
            self.assertEqual(
                subprocess.run(["git", "check-ref-format", ref]).returncode, 0,
                f"git rejected {ref!r} (from hostname {host!r})")


class TestDedupe(unittest.TestCase):
    def test_a_leaked_old_ref_does_not_double_count(self):
        # A push that half-lands leaves two refs for one device.
        beacons = [beacon(epoch=1000), beacon(epoch=2000)]
        newest = presence.newest_by_device(beacons)
        self.assertEqual(len(newest), 1)
        self.assertEqual(newest["dev1"].epoch, 2000)

    def test_distinct_devices_are_kept_apart(self):
        beacons = [beacon(device="dev1"), beacon(device="dev2")]
        self.assertEqual(len(presence.newest_by_device(beacons)), 2)


class TestLiveness(unittest.TestCase):
    WINDOW = 900.0

    def live(self, b, now, seen=None):
        return presence.is_live(b, now, seen or {}, self.WINDOW)

    def test_recent_beat_counts_and_an_old_one_does_not(self):
        self.assertTrue(self.live(beacon(epoch=1000), 1300))
        self.assertFalse(self.live(beacon(epoch=1000), 1000 + 901))

    def test_boundary_is_inclusive(self):
        self.assertTrue(self.live(beacon(epoch=1000), 1000 + self.WINDOW))

    def test_a_slightly_fast_clock_still_counts(self):
        # Publisher's clock 10 min ahead: negative age, still live.
        self.assertTrue(self.live(beacon(epoch=2000), 1400))

    def test_a_wrecked_clock_is_not_believed_forever(self):
        # A dead RTC reporting years ahead would otherwise count for ever.
        far = beacon(epoch=1000 + int(presence.FUTURE_MAX) + 60)
        self.assertFalse(self.live(far, 1000))

    def test_a_slow_clock_is_rescued_by_what_we_observed(self):
        # The undercount case: its epoch looks expired, but we watched its
        # ref change 60s ago on OUR clock, so it is demonstrably alive.
        stale = beacon(epoch=1000)
        now = 1000 + 5000
        self.assertFalse(self.live(stale, now))
        seen = {"dev1": {"ref": stale.ref, "at": now - 60}}
        self.assertTrue(self.live(stale, now, seen))

    def test_the_rescue_expires_too(self):
        stale = beacon(epoch=1000)
        now = 1000 + 5000
        seen = {"dev1": {"ref": stale.ref, "at": now - 5000}}
        self.assertFalse(self.live(stale, now, seen))

    def test_corrupt_observation_records_are_ignored(self):
        stale = beacon(epoch=1000)
        for seen in ({"dev1": "nonsense"}, {"dev1": {"at": "soon"}}, {}):
            self.assertFalse(self.live(stale, 9999, seen))


class TestObserve(unittest.TestCase):
    def test_first_sight_is_not_proof_of_life(self):
        # The ref of a machine that died a year ago is indistinguishable
        # from a live one until we watch it move.
        seen = presence.observe([beacon()], {}, 500)
        self.assertEqual(seen["dev1"]["ref"], beacon().ref)
        self.assertIsNone(seen["dev1"].get("at"))

    def test_a_changed_ref_is_proof(self):
        first = presence.observe([beacon(epoch=1000)], {}, 500)
        again = presence.observe([beacon(epoch=2000)], first, 900)
        self.assertEqual(again["dev1"]["at"], 900)

    def test_an_unchanged_ref_keeps_the_proof_already_earned(self):
        first = presence.observe([beacon(epoch=1000)], {}, 500)
        moved = presence.observe([beacon(epoch=2000)], first, 900)
        still = presence.observe([beacon(epoch=2000)], moved, 1500)
        self.assertEqual(still["dev1"]["at"], 900)

    def test_a_stale_ref_we_just_discovered_is_not_rescued(self):
        # The regression e2e scenario E caught: a fresh install must not
        # count every abandoned device for a whole window.
        dead = beacon(epoch=1000)
        now = 1000 + 86400
        seen = presence.observe([dead], {}, now)
        self.assertFalse(presence.is_live(dead, now, seen, 900))

    def test_devices_that_stopped_publishing_drop_out(self):
        first = presence.observe([beacon(device="dev1"), beacon(device="dev2")],
                                 {}, 500)
        self.assertEqual(len(first), 2)
        self.assertEqual(list(presence.observe([beacon(device="dev1")],
                                               first, 900)), ["dev1"])


class TestCounts(unittest.TestCase):
    MINE = "syu3cs@virginia.edu"
    OTHER = "ios8build@gmail.com"

    def counts(self, live, self_active, self_device="me"):
        return presence.device_counts(live, [self.MINE, self.OTHER],
                                      self_device, self_active)

    def test_alone_on_an_account_counts_one(self):
        self.assertEqual(self.counts([], presence.account_key(self.MINE)),
                         {self.MINE: 1})

    def test_a_second_device_on_the_same_account_counts_two(self):
        other = beacon(device="linux", active=presence.account_key(self.MINE))
        self.assertEqual(self.counts([other], presence.account_key(self.MINE)),
                         {self.MINE: 2})

    def test_accounts_nobody_is_on_are_absent_not_zero(self):
        counts = self.counts([], presence.account_key(self.MINE))
        self.assertNotIn(self.OTHER, counts)

    def test_our_own_published_ref_is_never_counted_twice(self):
        # A device that CAN push sees its own ref come back; the tally must
        # come from the live snapshot instead, or every count is one too many.
        mine = beacon(device="me", active=presence.account_key(self.MINE))
        self.assertEqual(self.counts([mine], presence.account_key(self.MINE)),
                         {self.MINE: 1})

    def test_a_device_that_cannot_push_still_counts_itself(self):
        # Read-only credential: our ref never appears, we count ourselves.
        other = beacon(device="linux", active=presence.account_key(self.OTHER))
        self.assertEqual(self.counts([other], presence.account_key(self.MINE)),
                         {self.MINE: 1, self.OTHER: 1})

    def test_accounts_this_device_does_not_have_are_ignored(self):
        stranger = beacon(device="linux", active=presence.account_key("who@x.com"))
        self.assertEqual(self.counts([stranger], ""), {})

    def test_a_device_with_no_active_slot_counts_for_nobody(self):
        idle = beacon(device="linux", active="")
        self.assertEqual(self.counts([idle], ""), {})

    def test_three_devices_on_one_account(self):
        key = presence.account_key(self.MINE)
        live = [beacon(device="linux", active=key),
                beacon(device="work", active=key)]
        self.assertEqual(self.counts(live, key), {self.MINE: 3})

    def test_counts_sum_to_the_number_of_live_devices(self):
        # The self-check that makes a wrong number visible: exactly one slot
        # is active per device, so the badges must add up to the device count.
        live = [beacon(device="linux", active=presence.account_key(self.MINE)),
                beacon(device="work", active=presence.account_key(self.OTHER))]
        counts = self.counts(live, presence.account_key(self.MINE))
        self.assertEqual(sum(counts.values()), len(live) + 1)


class TestStateFreshness(unittest.TestCase):
    WINDOW = 900.0

    def test_a_good_read_wins(self):
        self.assertEqual(
            presence.counts_for_state({"a@b.com": 2}, {"a@b.com": 9}, 0, 100,
                                      self.WINDOW), {"a@b.com": 2})

    def test_a_failed_read_holds_the_last_good_answer_briefly(self):
        self.assertEqual(
            presence.counts_for_state(None, {"a@b.com": 2}, 100, 500,
                                      self.WINDOW), {"a@b.com": 2})

    def test_once_it_ages_out_we_stop_claiming_to_know(self):
        self.assertEqual(
            presence.counts_for_state(None, {"a@b.com": 2}, 100, 100 + 901,
                                      self.WINDOW), {})

    def test_never_having_read_shows_nothing(self):
        # Rendering "(1)" from local knowledge alone would assert "only this
        # device" at exactly the moment we cannot see the others.
        self.assertEqual(presence.counts_for_state(None, {}, 0, 500,
                                                   self.WINDOW), {})


class TestSweep(unittest.TestCase):
    def test_our_superseded_refs_are_deleted_and_the_new_one_kept(self):
        keep = presence.encode_ref("me", "box", 2000, "a" * 16)
        old = beacon(device="me", epoch=1000)
        self.assertEqual(
            presence.own_stale_refs([old, presence.decode_ref(keep)], "me", keep),
            [old.ref])

    def test_another_device_is_never_in_our_replace_push(self):
        self.assertEqual(
            presence.own_stale_refs([beacon(device="linux")], "me", ""), [])

    def test_a_sleeping_laptop_is_not_swept(self):
        napping = beacon(device="linux", epoch=1000)
        self.assertEqual(presence.litter([napping], "me", 1000 + 86400 * 29), [])

    def test_a_month_dead_ref_is_litter(self):
        dead = beacon(device="linux", epoch=1000)
        self.assertEqual(
            presence.litter([dead], "me", 1000 + presence.DEAD_AFTER + 1),
            [dead.ref])

    def test_we_never_sweep_ourselves_as_litter(self):
        mine = beacon(device="me", epoch=1000)
        self.assertEqual(
            presence.litter([mine], "me", 1000 + presence.DEAD_AFTER + 1), [])

    def test_a_sweep_cannot_make_an_unbounded_push(self):
        dead = [beacon(device=f"d{i:04d}", epoch=1000) for i in range(100)]
        self.assertEqual(
            len(presence.litter(dead, "me", 1000 + presence.DEAD_AFTER + 1)),
            presence.MAX_SWEEP)


class TestRendering(unittest.TestCase):
    def account(self, devices):
        acct = model.Account(number=1, email="a@b.com")
        acct.devices = devices
        return acct

    def test_the_count_follows_the_address(self):
        self.assertEqual(model.account_label(self.account(2)), "a@b.com (2)")

    def test_no_badge_when_nobody_is_on_it(self):
        self.assertEqual(model.account_label(self.account(0)), "a@b.com")

    def test_menu_rows_carry_it_too(self):
        self.assertIn("a@b.com (3)", model.menu_row(self.account(3)))

    def test_accounts_default_to_no_count(self):
        self.assertEqual(model.account_label(model.Account(1, "a@b.com")),
                         "a@b.com")

    def test_apply_counts_stamps_and_clears(self):
        snap = model.Snapshot(accounts=[model.Account(1, "a@b.com"),
                                        model.Account(2, "c@d.com")])
        presence.apply_counts(snap, {"a@b.com": 2})
        self.assertEqual([a.devices for a in snap.accounts], [2, 0])
        presence.apply_counts(snap, {})
        self.assertEqual([a.devices for a in snap.accounts], [0, 0])


class TestClientFreshness(unittest.TestCase):
    """The UI re-checks freshness itself; the state file outlives the app."""

    def setUp(self):
        from smartbar import presence_runner
        self.runner = presence_runner
        self.saved = presence_runner.STATE_FILE
        self.work = tempfile.mkdtemp()
        presence_runner.STATE_FILE = os.path.join(self.work, "state.json")

    def tearDown(self):
        self.runner.STATE_FILE = self.saved
        shutil.rmtree(self.work, ignore_errors=True)

    def write(self, checked_at):
        with open(self.runner.STATE_FILE, "w") as handle:
            json.dump({"counts": {"a@b.com": 2}, "checkedAt": checked_at},
                      handle)

    def test_a_recent_beat_is_shown(self):
        from smartbar import presence_client
        self.write(time.time() - 30)
        self.assertEqual(presence_client.counts(), {"a@b.com": 2})

    def test_yesterdays_file_is_not_an_answer(self):
        # The app was off overnight, or beats stopped happening entirely.
        from smartbar import presence_client
        self.write(time.time() - 86400)
        self.assertEqual(presence_client.counts(), {})

    def test_a_file_with_no_timestamp_is_ignored(self):
        from smartbar import presence_client
        with open(self.runner.STATE_FILE, "w") as handle:
            json.dump({"counts": {"a@b.com": 2}}, handle)
        self.assertEqual(presence_client.counts(), {})

    def test_a_missing_or_corrupt_file_is_not_fatal(self):
        from smartbar import presence_client
        self.assertEqual(presence_client.counts(), {})
        with open(self.runner.STATE_FILE, "w") as handle:
            handle.write("{not json")
        self.assertEqual(presence_client.counts(), {})


class TestKnobs(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in
                      ("SMARTBAR_PRESENCE", "SMARTBAR_PRESENCE_INTERVAL",
                       "SMARTBAR_PRESENCE_TTL")}
        for key in self.saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_off_is_the_kill_switch(self):
        self.assertTrue(presence.enabled())
        for value in ("off", "OFF", " off "):
            os.environ["SMARTBAR_PRESENCE"] = value
            self.assertFalse(presence.enabled())

    def test_opting_out_hides_the_badges_too(self):
        # Not just "stop writing": counts left by an earlier beat must not
        # keep rendering after the switch is thrown.
        from smartbar import presence_client
        os.environ["SMARTBAR_PRESENCE"] = "off"
        self.assertEqual(presence_client.counts(), {})

    def test_defaults_give_three_beats_of_grace(self):
        self.assertEqual(presence.interval(), presence.DEFAULT_INTERVAL)
        self.assertEqual(presence.ttl(), 3 * presence.DEFAULT_INTERVAL)

    def test_nonsense_intervals_fall_back(self):
        os.environ["SMARTBAR_PRESENCE_INTERVAL"] = "banana"
        self.assertEqual(presence.interval(), presence.DEFAULT_INTERVAL)

    def test_beats_cannot_be_made_absurdly_frequent(self):
        os.environ["SMARTBAR_PRESENCE_INTERVAL"] = "1"
        self.assertEqual(presence.interval(), 60.0)

    def test_a_ttl_below_two_beats_would_drop_healthy_devices(self):
        os.environ["SMARTBAR_PRESENCE_INTERVAL"] = "300"
        os.environ["SMARTBAR_PRESENCE_TTL"] = "60"
        self.assertEqual(presence.ttl(), 600.0)


SWIFT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "macos-swift", "Sources", "AISmartbar", "PresenceStatus.swift")


class TestMacAndLinuxAgree(unittest.TestCase):
    """The one place presence exists twice, in two languages.

    Publishing and reading refs is shared Python, so the WIRE cannot drift.
    But the macOS UI re-implements four policy decisions in Swift that the
    Linux UI takes from core/presence.py — the badge format, the kill switch,
    the beat interval and the staleness window. Nothing else in the build
    compares them, so they can silently diverge and give two machines
    different answers from identical config. This reads the Swift source and
    pins the constants to the Python ones.

    Source-scraping is deliberate: it runs in the ordinary unit suite, on
    Linux, with no Swift toolchain — so a Linux-only contributor still cannot
    break the Mac.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(SWIFT_SOURCE):
            raise unittest.SkipTest("no Swift sources in this checkout")
        with open(SWIFT_SOURCE) as handle:
            cls.swift = handle.read()

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in
                      ("SMARTBAR_PRESENCE", "SMARTBAR_PRESENCE_INTERVAL",
                       "SMARTBAR_PRESENCE_TTL")}
        for key in self.saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_the_badge_is_formatted_the_same_on_both(self):
        # model.account_label is what Linux renders; this is what macOS
        # renders. Same address, same count, same string.
        self.assertIn(r'devices > 0 ? "\(email) (\(devices))" : email',
                      self.swift)
        account = model.Account(number=1, email="a@b.c", devices=2)
        self.assertEqual(model.account_label(account), "a@b.c (2)")

    def test_zero_devices_renders_no_badge_on_both(self):
        # The Swift guard is `devices > 0`; Python's is `count > 0`. A "(0)"
        # on one platform and a bare address on the other would be the most
        # visible possible drift.
        account = model.Account(number=1, email="a@b.c", devices=0)
        self.assertEqual(model.account_label(account), "a@b.c")
        self.assertIn("devices > 0 ?", self.swift)

    def test_the_kill_switch_is_spelled_the_same(self):
        self.assertIn('environment["SMARTBAR_PRESENCE"]', self.swift)
        self.assertIn(
            'raw.trimmingCharacters(in: .whitespaces).lowercased() != "off"',
            self.swift)
        # …and Python really does trim and lowercase, so " OFF " agrees.
        for value in ("off", "OFF", " Off "):
            os.environ["SMARTBAR_PRESENCE"] = value
            self.assertFalse(presence.enabled())
        os.environ.pop("SMARTBAR_PRESENCE", None)

    def test_the_beat_interval_default_and_floor_match(self):
        default = re.search(
            r"defaultInterval: TimeInterval = (\d+)", self.swift)
        self.assertIsNotNone(default, "Swift default interval not found")
        self.assertEqual(float(default.group(1)), presence.DEFAULT_INTERVAL)
        floor = re.search(
            r"return max\((\d+), Double\(raw\) \?\? defaultInterval\)",
            self.swift)
        self.assertIsNotNone(floor, "Swift interval floor not found")
        os.environ["SMARTBAR_PRESENCE_INTERVAL"] = "1"
        self.assertEqual(presence.interval(), float(floor.group(1)))
        os.environ.pop("SMARTBAR_PRESENCE_INTERVAL", None)

    def test_the_staleness_window_honours_the_same_override(self):
        # This one HAD drifted: Swift hardcoded `interval * 3` and ignored
        # SMARTBAR_PRESENCE_TTL, so setting it moved Linux's window and not
        # the Mac's. The regression is that the Swift side reads the variable
        # at all, with the same 3x default and 2x floor.
        self.assertIn('environment["SMARTBAR_PRESENCE_TTL"]', self.swift)
        self.assertIn("guard let explicit = Double(raw) else { return 3 * beat }",
                      self.swift)
        self.assertIn("return max(2 * beat, explicit)", self.swift)
        os.environ["SMARTBAR_PRESENCE_INTERVAL"] = "300"
        os.environ["SMARTBAR_PRESENCE_TTL"] = "60"      # below the 2x floor
        self.assertEqual(presence.ttl(), 600.0)
        os.environ["SMARTBAR_PRESENCE_TTL"] = "5000"    # above it
        self.assertEqual(presence.ttl(), 5000.0)
        for key in ("SMARTBAR_PRESENCE_INTERVAL", "SMARTBAR_PRESENCE_TTL"):
            os.environ.pop(key, None)

    def test_the_mac_reads_the_window_rather_than_a_bare_multiple(self):
        # Guards the actual call site, not just the helper: reload() must use
        # Self.ttl, or the helper above would be correct and unused.
        self.assertIn("let window = Self.ttl", self.swift)
        self.assertNotIn("Self.interval * 3", self.swift)


class TestWindowsDeviceRef(unittest.TestCase):
    """The win32 device ref, proven against git itself.

    platform_tag()'s docstring warns that the one mistake that would matter
    is letting the platform become a new REF COMPONENT instead of staying
    inside the cosmetic label: an older mac/linux device's decoder rejects a
    shape it does not recognise, and would quietly stop counting every
    Windows device that beats. TestPlatformInTheLabel proves the "win" tag
    and the prefix in isolation; this proves the part that actually matters
    end to end, the way test_a_prefixed_label_is_still_a_ref_git_accepts
    does for mac/linux/freebsd — build a real ref for a win32 device and
    hand it to `git check-ref-format` and this module's own decoder, rather
    than to a hand-rolled regex that could be wrong the same way the code is.
    """

    def setUp(self):
        self.saved_platform = presence.sys.platform
        self.saved_label = os.environ.get("SMARTBAR_PRESENCE_LABEL")
        os.environ.pop("SMARTBAR_PRESENCE_LABEL", None)

    def tearDown(self):
        presence.sys.platform = self.saved_platform
        if self.saved_label is None:
            os.environ.pop("SMARTBAR_PRESENCE_LABEL", None)
        else:
            os.environ["SMARTBAR_PRESENCE_LABEL"] = self.saved_label

    def test_the_runner_prefixes_win_the_same_way_as_mac_and_linux(self):
        from smartbar import presence_runner
        presence.sys.platform = "win32"
        with mock.patch.object(presence_runner.socket, "gethostname",
                               return_value="DESKTOP-ABC123.localdomain"):
            self.assertEqual(presence_runner.device_label(), "win-desktop-abc123")

    @unittest.skipIf(shutil.which("git") is None, "git not installed")
    def test_a_win_label_produces_a_ref_every_existing_reader_accepts(self):
        from smartbar import presence_runner
        presence.sys.platform = "win32"
        with mock.patch.object(presence_runner.socket, "gethostname",
                               return_value="DESKTOP-ABC123.localdomain"):
            label = presence_runner.device_label()
        ref = presence.encode_ref(presence.new_device_id(), label, 1753380000,
                                  presence.account_key("a@b.com"))
        self.assertEqual(
            subprocess.run(["git", "check-ref-format", ref]).returncode, 0,
            f"git rejected {ref!r} (win32 device label {label!r})")
        decoded = presence.decode_ref(ref)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.label, label)
        # The same ref SHAPE as a mac/linux device: the platform lives in the
        # label component only, never as a component of its own, so the count
        # of "/"-separated parts must not move.
        mac_ref = presence.encode_ref("abc123", "mac-box", 1753380000,
                                      presence.account_key("a@b.com"))
        self.assertEqual(ref.count("/"), mac_ref.count("/"))


if __name__ == "__main__":
    unittest.main()
