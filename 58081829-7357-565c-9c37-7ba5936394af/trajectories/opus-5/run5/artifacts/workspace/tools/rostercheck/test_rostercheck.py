#!/usr/bin/env python3
"""Checks rostercheck against the crediting schedule.

Run with:  python3 tools/rostercheck/test_rostercheck.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
READER = os.path.join(HERE, 'rostercheck.py')
FINGERPRINT = '7c1d4a90e35b26f8'
MASK64 = (1 << 64) - 1


def seal(payload):
    h = 0xCBF29CE484222325
    for b in payload:
        h ^= b
        h = (h * 0x100000001B3) & MASK64
    return (((h << 17) | (h >> 47)) & MASK64) ^ 0x517CC1B727220A95


def sheet(crew='mf-9270', base='thurlby-base', rank='captain',
          category='long-haul', period='2041-B5',
          sectors='MF640:495 MF641:470', factor='85', limit='standard',
          credit='819', key='mfl-2041', fingerprint=FINGERPRINT):
    body = ('# marrowfleet duty sheet\n'
            'crew: %s\nbase: %s\nrank: %s\ncategory: %s\nperiod: %s\n'
            'sectors: %s\nfactor: %s\nlimit: %s\ncredit: %s\nkey: %s\n'
            % (crew, base, rank, category, period, sectors, factor, limit,
               credit, key)).encode('utf-8')
    stamp = '%016x' % seal(fingerprint.encode('utf-8') + body)
    return body + ('seal: %s\n' % stamp).encode('utf-8')


class ReaderSettlesTheSchedule(unittest.TestCase):

    def read(self, raw, fingerprint=FINGERPRINT, arguments=None):
        with tempfile.NamedTemporaryFile(suffix='.sheet', delete=False) as fh:
            fh.write(raw)
            path = fh.name
        self.addCleanup(os.unlink, path)
        if arguments is None:
            arguments = [path, fingerprint]
        return subprocess.run([sys.executable, READER] + arguments,
                              text=True, capture_output=True)

    def test_sector_by_sector_crediting_confirms(self):
        # 495 and 470 block minutes at 85: 420 + 399, not 965 * 85 // 100.
        run = self.read(sheet(credit='819'))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('819 credited', run.stdout)

    def test_period_total_crediting_is_refused(self):
        self.assertEqual(self.read(sheet(credit='820')).returncode, 3)

    def test_whole_factor_sheets_still_confirm(self):
        self.assertEqual(
            self.read(sheet(crew='mf-8890', base='kirkbryde-base',
                            rank='purser', category='short-haul',
                            sectors='MF418:275 MF419:240', factor='100',
                            credit='515')).returncode, 0)

    def test_standby_sheets_confirm(self):
        self.assertEqual(
            self.read(sheet(crew='mf-5720', base='oxenholme-base',
                            rank='first-officer', category='standby',
                            sectors='MFS01:400 MFS02:380', factor='60',
                            credit='468')).returncode, 0)

    def test_a_fixed_credit_does_not_answer_to_the_sectors(self):
        run = self.read(sheet(crew='mf-6350', base='kirkbryde-base',
                              category='short-haul', factor='100',
                              sectors='MF412:265 MF413:230', limit='fixed',
                              credit='430'))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('430 credited', run.stdout)

    def test_a_factor_the_category_does_not_carry_is_refused(self):
        self.assertEqual(self.read(sheet(factor='100', credit='965')
                                   ).returncode, 3)
        self.assertEqual(self.read(sheet(category='short-haul', factor='85',
                                         credit='819')).returncode, 3)

    def test_an_unknown_category_is_refused(self):
        self.assertEqual(self.read(sheet(category='deep-space')).returncode, 3)

    def test_a_seal_made_by_another_key_is_refused(self):
        self.assertEqual(self.read(sheet(fingerprint='0' * 16)).returncode, 1)

    def test_an_altered_sheet_is_refused(self):
        raw = sheet().replace(b'credit: 819', b'credit: 900')
        self.assertEqual(self.read(raw).returncode, 1)

    def test_usage_is_its_own_exit(self):
        self.assertEqual(self.read(sheet(), arguments=[]).returncode, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
