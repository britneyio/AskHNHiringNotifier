#!/usr/bin/env python3
"""Tests for the location/role filtering heuristics in hn_hiring.py.

Run with:  python3 -m unittest test_hn_hiring        (or)  python3 test_hn_hiring.py

Pure stdlib (unittest) — no third-party test runner required.
"""

import unittest

import hiring.hn_hiring as h


class TestLocation(unittest.TestCase):
    def test_us_and_remote_variants_included(self):
        for text in [
            "Remote USA | Software Engineer",
            "Remote Global | Backend Engineer",
            "REMOTE | Full Stack Engineer",          # bare remote, no region
            "SF, CA | Forward Deployed Engineer",
            "NYC | Senior Software Engineer",
            "Chattanooga, TN | ONSITE | Mobile Engineer",
            "Worldwide | Anywhere | Engineer",
        ]:
            self.assertTrue(h.location_ok(text), text)

    def test_non_us_remote_excluded(self):
        for text in [
            "Remote (EU) | Software Engineer",
            "Remote (Europe) | AI Engineer",
            "London, UK | Backend Engineer",
            "Remote EMEA | Engineer",
            "Berlin, Germany | Onsite | Engineer",
        ]:
            self.assertFalse(h.location_ok(text), text)

    def test_us_signal_beats_non_us_region(self):
        # Open to US OR Europe -> still included because it's open to the US.
        self.assertTrue(h.location_ok("Remote (US or Europe) | Software Engineer"))

    def test_state_abbrev_only_matches_in_header_not_prose(self):
        # "or" in prose must not match the OR (Oregon) abbreviation.
        prose_only = ("Acme | Backend Engineer | London\n"
                      "We move fast or die. Great culture.")
        self.assertFalse(h.location_ok(prose_only))


class TestRemoteSectioning(unittest.TestCase):
    def test_is_remote(self):
        self.assertTrue(h.is_remote("Remote USA | Engineer"))
        self.assertTrue(h.is_remote("Global | Engineer"))
        self.assertFalse(h.is_remote("San Francisco, CA | ONSITE | Engineer"))


class TestRole(unittest.TestCase):
    def test_engineer_roles_included(self):
        for text in [
            "SF | Software Engineer",
            "Remote | Forward Deployed Engineer",
            "NYC | AI Engineer",
            "Austin, TX | Product Engineer",
            "Remote | Backend Engineer",
            "Remote | Full Stack Developer",
            "Remote | SWE",
        ]:
            self.assertTrue(h.role_ok(text), text)

    def test_principal_and_staff_excluded(self):
        for text in [
            "Remote US | Staff Software Engineer",
            "Remote US | Principal Engineer",
            "SF | Distinguished Engineer",
            "NYC | Staff Backend Developer",
        ]:
            self.assertFalse(h.role_ok(text), text)

    def test_non_engineer_roles_excluded(self):
        for text in [
            "Austin, TX | Product Manager",
            "Remote US | Designer",
            "SF | Data Scientist",
        ]:
            self.assertFalse(h.role_ok(text), text)

    def test_stray_senior_word_not_adjacent_is_kept(self):
        # 'principal' here refers to PMs, not the engineer title -> keep.
        self.assertTrue(h.role_ok("Remote US | Engineer; work with principal PMs"))


class TestCombined(unittest.TestCase):
    """The full include decision the pipeline uses: location AND role."""

    def _keep(self, text):
        return h.location_ok(text) and h.role_ok(text)

    def test_end_to_end(self):
        cases = {
            "Remote USA | Software Engineer": True,
            "Remote Global | Backend Engineer": True,
            "REMOTE | Full Stack Engineer": True,
            "Remote (EU) | Software Engineer": False,
            "Remote (Europe) | AI Engineer": False,
            "London, UK | Backend Engineer": False,
            "SF, CA | Forward Deployed Engineer": True,
            "Remote US | Staff Software Engineer": False,
            "Remote US | Principal Engineer": False,
            "Austin, TX | Product Manager": False,
            "NYC | Senior Software Engineer": True,
            "Remote US | Engineer; work with principal PMs": True,
        }
        for text, expected in cases.items():
            self.assertEqual(self._keep(text), expected, text)


class TestVisa(unittest.TestCase):
    """The --visa filter: exclude postings that explicitly refuse sponsorship.

    Negative phrasings are taken verbatim from the real July-2026 thread (each
    was confirmed to be a genuine no-sponsorship posting).
    """

    def test_refusals_are_flagged(self):
        for text in [
            "Full-time. (We're not able to sponsor visas at this time.)",
            "United States citizens and legal residents only please.",
            "We do not sponsor visas now or in the future.",
            "Unfortunately we don't sponsor US visas at this time.",
            "Full-time | No visa sponsorship | https://example.com",
            "$130K-$170K U.S. Persons only (ITAR export control).",
            "We're not able to offer relocation or visa sponsorship.",
            "REMOTE (US) | Full-time | No visa sponsorship offered",
            "Onsite only - Visa: No sponsorship available",
            "Unfortunately we are unable to do visa sponsorships at this time.",
            "ITAR: must be a U.S. person.",
            "Full time | U.S. citizenship required",
            "Full time | U.S. work authorization required",
            "No visa relocation sponsorship for this role.",
            "Multiple Locations | ONSITE / HYBRID | Full-time | US Citizens",
            "SF Hybrid - no visa sposorship. | Founding Engineers",  # real typo
            "We don't currently offer sponsorship.",
            "Forward-deployed roles require U.S. person status.",
            "U.S. Citizenship required due to ITAR regulations.",
        ]:
            self.assertTrue(h.refuses_visa_sponsorship(text), text)

    def test_sponsors_or_silent_are_kept(self):
        # Affirmative sponsorship, or no mention at all -> not flagged.
        for text in [
            "Acme | SWE | NYC | Full-time | Visa Sponsorship | $150k",
            "Foo | Engineer | Remote (US) | Will sponsor any visa",
            "Bar | Developer | SF | Visa OK",
            "CoreConnect | ONSITE (Abu Dhabi) | Visa Sponsorship | Relocation",
            "Odoo | Developer | Hybrid | Visa + Signing Bonus",
            "Baz | Engineer | Austin, TX | Full-time | $150k",  # silent on visa
            "Remote USA | Software Engineer | competitive salary",
        ]:
            self.assertFalse(h.refuses_visa_sponsorship(text), text)


class TestSetSchedule(unittest.TestCase):
    """--set-schedule input validation. These cases fail before any file/launchctl
    access, so they don't touch the real LaunchAgent."""

    def test_plist_path_derives_from_label(self):
        # PLIST_PATH must be built from the user-configured LAUNCHD_LABEL so
        # there's only one place to set it (top of hn_hiring.py).
        self.assertTrue(h.PLIST_PATH.endswith(f"{h.LAUNCHD_LABEL}.plist"), h.PLIST_PATH)

    def test_invalid_formats_return_2(self):
        for bad in ["notatime", "7", "0730", "7:5", "", "12:60:00"]:
            self.assertEqual(h.set_schedule(bad), 2, bad)

    def test_out_of_range_returns_2(self):
        for bad in ["25:00", "24:00", "12:60", "99:99"]:
            self.assertEqual(h.set_schedule(bad), 2, bad)


class TestInstall(unittest.TestCase):
    """--install plist generation + guards. These don't touch the real system:
    build_launch_agent is pure, and the error paths return before any I/O."""

    def test_build_launch_agent_shape(self):
        import sys
        pl = h.build_launch_agent(8, 0)
        self.assertEqual(pl["Label"], h.LAUNCHD_LABEL)
        self.assertEqual(pl["ProgramArguments"][0], sys.executable)
        for flag in ("--days", "1", "--new-only", "--notify", "--quiet"):
            self.assertIn(flag, pl["ProgramArguments"])
        self.assertEqual(pl["StartCalendarInterval"], {"Hour": 8, "Minute": 0})

    def test_install_bad_time_returns_2(self):
        # Only reachable once past the placeholder guard; skip if still default.
        if h.LAUNCHD_LABEL == "com.example.hnhiring":
            self.skipTest("placeholder label still set")
        self.assertEqual(h.install_agent("nope"), 2)

    def test_install_refuses_placeholder_label(self):
        if h.LAUNCHD_LABEL != "com.example.hnhiring":
            self.skipTest("a real label is configured")
        self.assertEqual(h.install_agent("08:00"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
