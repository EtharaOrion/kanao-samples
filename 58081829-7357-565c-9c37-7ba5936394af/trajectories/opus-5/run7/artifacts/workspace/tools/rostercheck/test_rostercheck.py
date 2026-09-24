#!/usr/bin/env python3
"""Checks for rostercheck.py - the reader the bases' crewing systems settle with.

The reader has to settle a duty credit the way the crediting schedule settles
it: each sector credited on its category's factor, the sector credits added
together, and no sheet accepted that carries a factor its category does not.

Run: python3 tools/rostercheck/test_rostercheck.py
"""
import glob
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
READER = os.path.join(HERE, 'rostercheck.py')
FINGERPRINT = '7c1d4a90e35b26f8'
DROPS = '/srv/bases'


def _load():
    """rostercheck exits on import; run it as a usage call and keep its names."""
    spec = importlib.util.spec_from_file_location('mf_rostercheck', READER)
    module = importlib.util.module_from_spec(spec)
    argv, stderr = sys.argv, sys.stderr
    sys.argv = ['rostercheck']
    sys.stderr = open(os.devnull, 'w')
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    finally:
        sys.stderr.close()
        sys.argv, sys.stderr = argv, stderr
    return module


reader = _load()


def reseal(head, fingerprint=FINGERPRINT):
    """Build a sheet from ordered (key, value) lines and seal it."""
    body = '# marrowfleet duty sheet\n' + ''.join(
        '%s: %s\n' % pair for pair in head)
    stamp = '%016x' % reader.seal(fingerprint.encode('utf-8') +
                                  body.encode('utf-8'))
    return body + 'seal: %s\n' % stamp


def sheet(category='long-haul', factor='85', limit='standard', credit='819',
          sectors='MF640:495 MF641:470'):
    return reseal([('crew', 'mf-9270'), ('base', 'thurlby-base'),
                   ('rank', 'captain'), ('category', category),
                   ('period', '2041-B5'), ('sectors', sectors),
                   ('factor', factor), ('limit', limit), ('credit', credit),
                   ('key', 'mfl-2041')])


def check(text):
    handle = tempfile.NamedTemporaryFile('w', suffix='.sheet', delete=False)
    handle.write(text)
    handle.close()
    try:
        return subprocess.run([sys.executable, READER, handle.name,
                               FINGERPRINT], text=True, capture_output=True)
    finally:
        os.unlink(handle.name)


class Crediting(unittest.TestCase):
    def test_sector_credits_are_added_together(self):
        self.assertEqual(reader.credited([495, 470], 85), 819)

    def test_aggregating_the_blocks_first_is_not_the_schedule(self):
        self.assertNotEqual(reader.credited([495, 470], 85),
                            sum([495, 470]) * 85 // 100)

    def test_whole_factors_are_unchanged(self):
        self.assertEqual(reader.credited([275, 240], 100), 515)
        self.assertEqual(reader.credited([400, 380], 60), 468)

    def test_a_sheet_credited_per_sector_is_confirmed(self):
        self.assertEqual(check(sheet(credit='819')).returncode, 0)

    def test_a_sheet_credited_on_the_aggregate_is_refused(self):
        done = check(sheet(credit='820'))
        self.assertEqual(done.returncode, 3)
        self.assertIn('not the credit the sectors give', done.stderr)


class Factors(unittest.TestCase):
    def test_a_factor_the_category_does_not_carry_is_refused(self):
        done = check(sheet(category='long-haul', factor='100', credit='965'))
        self.assertEqual(done.returncode, 3)
        self.assertIn('factor its category is credited at', done.stderr)

    def test_a_fixed_sheet_must_still_carry_its_category_factor(self):
        done = check(sheet(category='short-haul', factor='85', limit='fixed',
                           credit='430'))
        self.assertEqual(done.returncode, 3)

    def test_an_unknown_category_is_refused(self):
        self.assertEqual(check(sheet(category='ferry')).returncode, 3)


class Seals(unittest.TestCase):
    def test_an_altered_credit_breaks_the_seal(self):
        done = check(sheet().replace('credit: 819', 'credit: 820'))
        self.assertEqual(done.returncode, 1)
        self.assertIn('seal was not made by that key', done.stderr)

    def test_another_key_does_not_confirm(self):
        text = sheet()
        handle = tempfile.NamedTemporaryFile('w', suffix='.sheet', delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        done = subprocess.run([sys.executable, READER, handle.name,
                               '0000000000000000'], text=True,
                              capture_output=True)
        self.assertEqual(done.returncode, 1)

    def test_usage(self):
        self.assertEqual(subprocess.run([sys.executable, READER],
                                        capture_output=True).returncode, 2)


class ReleasedDrops(unittest.TestCase):
    """What each base is actually holding, read the way its crewing officer does."""

    def test_every_sheet_in_every_drop_is_confirmed(self):
        sheets = sorted(glob.glob(os.path.join(DROPS, '*', '*.sheet')))
        if not sheets:
            self.skipTest('no base drops on this host')
        for path in sheets:
            with self.subTest(sheet=path):
                done = subprocess.run([sys.executable, READER, path,
                                       FINGERPRINT], text=True,
                                      capture_output=True)
                self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
