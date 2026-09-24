#!/usr/bin/env python3
"""The crediting schedule, checked against the reader the bureau publishes.

docs/crediting-schedule.md is what a base's crewing system settles a sheet
against, and rostercheck is that schedule written out. Each case here is a
sentence of the schedule. The seal is computed here rather than imported so the
reader is exercised exactly as a crewing system runs it.

    python3 tools/rostercheck/test_rostercheck.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
READER = os.path.join(HERE, "rostercheck.py")
FINGERPRINT = "7c1d4a90e35b26f8"
MASK64 = (1 << 64) - 1


def seal(payload):
    h = 0xCBF29CE484222325
    for b in payload:
        h ^= b
        h = (h * 0x100000001B3) & MASK64
    return (((h << 17) | (h >> 47)) & MASK64) ^ 0x517CC1B727220A95


def sheet(sectors="MF640:495 MF641:470", factor="85", category="long-haul",
          limit="standard", credit="819", fingerprint=FINGERPRINT):
    body = ("# marrowfleet duty sheet\n"
            "crew: mf-9270\nbase: thurlby-base\nrank: captain\n"
            "category: %s\nperiod: 2041-B5\nsectors: %s\nfactor: %s\n"
            "limit: %s\ncredit: %s\nkey: mfl-2041\n"
            % (category, sectors, factor, limit, credit)).encode("utf-8")
    return body + ("seal: %016x\n" % seal(fingerprint.encode("utf-8") + body)).encode("utf-8")


class Reader(unittest.TestCase):
    def settle(self, raw, fingerprint=FINGERPRINT):
        handle = tempfile.NamedTemporaryFile(suffix=".sheet", delete=False)
        handle.write(raw)
        handle.close()
        try:
            return subprocess.run([sys.executable, READER, handle.name,
                                   fingerprint], capture_output=True, text=True)
        finally:
            os.unlink(handle.name)

    def test_a_sector_is_credited_then_the_sectors_are_added_together(self):
        """495 and 470 minutes at 85: 420 + 399, not 965 taken on 85."""
        self.assertEqual(self.settle(sheet(credit="819")).returncode, 0)
        self.assertEqual(self.settle(sheet(credit="820")).returncode, 3)

    def test_a_crew_member_is_credited_at_their_category_factor(self):
        self.assertEqual(self.settle(sheet(category="short-haul", factor="100",
                                           credit="965")).returncode, 0)
        self.assertEqual(self.settle(sheet(category="standby", factor="60",
                                           credit="579")).returncode, 0)

    def test_a_sheet_carrying_another_factor_is_not_this_schedule(self):
        """965 at 100 is arithmetic the schedule does not describe for long-haul."""
        run = self.settle(sheet(factor="100", credit="965"))
        self.assertEqual(run.returncode, 3)
        self.assertIn("factor its category", run.stderr)

    def test_a_sheet_carrying_no_known_category_is_not_this_schedule(self):
        self.assertEqual(self.settle(sheet(category="ferry")).returncode, 3)

    def test_a_fixed_limitation_settles_without_the_sectors(self):
        self.assertEqual(self.settle(sheet(limit="fixed",
                                           credit="430")).returncode, 0)

    def test_a_sheet_that_says_neither_standard_nor_fixed_does_not_settle(self):
        run = self.settle(sheet(limit="carried", credit="1"))
        self.assertEqual(run.returncode, 3)
        self.assertIn("whether a duty limitation stands", run.stderr)

    def test_a_credit_that_is_not_a_figure_does_not_settle(self):
        self.assertEqual(self.settle(sheet(credit="")).returncode, 3)
        self.assertEqual(self.settle(sheet(credit="eight")).returncode, 3)

    def test_a_seal_made_by_another_key_does_not_settle(self):
        run = self.settle(sheet(), fingerprint="0" * 16)
        self.assertEqual(run.returncode, 1)
        self.assertIn("seal", run.stderr)

    def test_a_sheet_edited_after_sealing_does_not_settle(self):
        raw = sheet().replace(b"credit: 819", b"credit: 900")
        self.assertEqual(self.settle(raw).returncode, 1)

    def test_usage(self):
        self.assertEqual(subprocess.run([sys.executable, READER],
                                        capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
