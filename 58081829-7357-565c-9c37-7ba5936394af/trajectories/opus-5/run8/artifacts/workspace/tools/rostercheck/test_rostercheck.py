"""Contract tests for the reader the bureau publishes to the bases.

A base's crewing system settles a sheet with this reader before a crew member is
rostered onto a flight, so the reader has to settle a duty credit the way
`docs/crediting-schedule.md` settles it - no wider and no narrower.
"""
import os
import subprocess
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
READER = os.path.join(HERE, "rostercheck.py")
FINGERPRINT = "7c1d4a90e35b26f8"

CONFIRMED, SEAL, USAGE, CREDIT = 0, 1, 2, 3


def load():
    """Import the reader without running its command line."""
    with open(READER, "r", encoding="utf-8") as handle:
        source = handle.read()
    source = source.replace("sys.exit(main(sys.argv[1:]))", "")
    module = types.ModuleType("rostercheck_under_test")
    module.__file__ = READER
    exec(compile(source, READER, "exec"), module.__dict__)
    return module


RC = load()


def sheet(tmp_path, name="mf-9270", base="thurlby-base", rank="captain",
          category="long-haul", period="2041-B5",
          sectors="MF640:495 MF641:470", factor="85", limit="standard",
          credit="819", key="mfl-2041", fingerprint=FINGERPRINT):
    body = ("# marrowfleet duty sheet\n"
            "crew: %s\nbase: %s\nrank: %s\ncategory: %s\nperiod: %s\n"
            "sectors: %s\nfactor: %s\nlimit: %s\ncredit: %s\nkey: %s\n"
            % (name, base, rank, category, period, sectors, factor, limit,
               credit, key))
    stamped = "%016x" % RC.seal(fingerprint.encode("utf-8")
                                + body.encode("utf-8"))
    path = tmp_path / ("marrowfleet_%s.sheet" % name)
    path.write_text(body + "seal: %s\n" % stamped, encoding="utf-8")
    return str(path)


def settle(path, fingerprint=FINGERPRINT):
    return subprocess.run([sys.executable, READER, path, fingerprint],
                          text=True, capture_output=True)


# --- the crediting rule -----------------------------------------------------

def test_a_sector_is_credited_then_the_credits_are_added():
    assert RC.credited([495, 470], 85) == 819


def test_totalling_the_block_minutes_first_is_not_the_schedules_rule():
    assert sum([495, 470]) * 85 // 100 == 820
    assert RC.credited([495, 470], 85) != 820


def test_a_factor_of_one_hundred_leaves_the_block_minutes_as_they_stand():
    assert RC.credited([210, 245, 190], 100) == 645


def test_the_standby_factor_credits_at_sixty_hundredths():
    assert RC.credited([400, 380], 60) == 468


def test_the_schedules_published_factors_are_the_ones_the_reader_holds():
    assert RC.FACTORS == {"long-haul": 85, "short-haul": 100, "standby": 60}
    assert RC.CREDIT_BASE == 100


# --- settling a sheet -------------------------------------------------------

def test_a_sheet_at_the_schedules_credit_is_confirmed(tmp_path):
    assert settle(sheet(tmp_path)).returncode == CONFIRMED


def test_a_sheet_carrying_the_aggregate_rounded_credit_is_refused(tmp_path):
    got = settle(sheet(tmp_path, credit="820"))
    assert got.returncode == CREDIT
    assert "not the credit the sectors give" in got.stderr


def test_a_seal_made_by_another_key_is_refused(tmp_path):
    got = settle(sheet(tmp_path), fingerprint="0000000000000000")
    assert got.returncode == SEAL
    assert "was not made by that key" in got.stderr


def test_a_sheet_sealed_for_another_fingerprint_is_refused(tmp_path):
    path = sheet(tmp_path, fingerprint="0000000000000000")
    assert settle(path).returncode == SEAL


def test_an_altered_body_breaks_the_seal(tmp_path):
    path = sheet(tmp_path)
    with open(path, "r", encoding="utf-8") as handle:
        body = handle.read()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body.replace("credit: 819", "credit: 900"))
    assert settle(path).returncode == SEAL


def test_a_category_the_schedule_does_not_price_is_refused(tmp_path):
    got = settle(sheet(tmp_path, category="ultra-haul", factor="85"))
    assert got.returncode == CREDIT
    assert "the factor its category is credited at" in got.stderr


def test_a_factor_the_category_does_not_carry_is_refused(tmp_path):
    # 965 block minutes at 100 hundredths credits 965, so a sheet can be
    # internally consistent and still not be a sheet the schedule describes.
    got = settle(sheet(tmp_path, category="long-haul", factor="100",
                       credit="965"))
    assert got.returncode == CREDIT
    assert "the factor its category is credited at" in got.stderr


def test_a_missing_factor_is_refused(tmp_path):
    assert settle(sheet(tmp_path, factor="")).returncode == CREDIT


def test_a_fixed_credit_is_not_settled_against_the_sectors(tmp_path):
    # Where a duty limitation stands, the figure is the credit and the sectors
    # do not come into it.
    path = sheet(tmp_path, name="mf-6350", base="kirkbryde-base",
                 category="short-haul", factor="100",
                 sectors="MF412:265 MF413:230", limit="fixed", credit="430")
    assert settle(path).returncode == CONFIRMED


def test_a_fixed_sheet_still_has_to_carry_its_categorys_factor(tmp_path):
    path = sheet(tmp_path, name="mf-6350", category="short-haul", factor="85",
                 limit="fixed", credit="430")
    assert settle(path).returncode == CREDIT


def test_the_reader_reports_what_it_confirmed(tmp_path):
    got = settle(sheet(tmp_path))
    assert got.stdout.strip() == "thurlby-base mf-9270 2041-B5: 819 credited"


def test_usage_without_a_fingerprint_is_refused(tmp_path):
    got = subprocess.run([sys.executable, READER, sheet(tmp_path)],
                         text=True, capture_output=True)
    assert got.returncode == USAGE


# --- every sheet the bureau has staged ---------------------------------------

def staged():
    root = "/var/lib/marrowfleet/sheets"
    if not os.path.isdir(root):
        return []
    return [os.path.join(root, leaf) for leaf in sorted(os.listdir(root))
            if leaf.endswith(".sheet")]


@pytest.mark.skipif(not staged(), reason="no sheets are staged")
def test_every_staged_sheet_settles_with_the_published_reader():
    refused = [(path, settle(path).stderr.strip()) for path in staged()
               if settle(path).returncode != CONFIRMED]
    assert refused == []
