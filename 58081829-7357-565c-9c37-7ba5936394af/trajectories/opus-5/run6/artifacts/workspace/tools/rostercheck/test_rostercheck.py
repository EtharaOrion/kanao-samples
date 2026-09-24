#!/usr/bin/env python3
"""test_rostercheck - the crediting schedule as a crewing system settles it.

The seal is recomputed here from the schedule's own description so that a case
stands on its own rather than on the reader under test.

    python3 tools/rostercheck/test_rostercheck.py

Exit 0 when every case holds, 1 otherwise.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROSTERCHECK = os.path.join(HERE, "rostercheck.py")
FINGERPRINT = "7c1d4a90e35b26f8"
MASK64 = (1 << 64) - 1
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def seal(payload):
    h = 0xCBF29CE484222325
    for b in payload:
        h ^= b
        h = (h * 0x100000001B3) & MASK64
    return (((h << 17) | (h >> 47)) & MASK64) ^ 0x517CC1B727220A95


def settle(sectors="MF640:495 MF641:470", category="long-haul", factor="85",
           limit="standard", credit="819", seal_body=True):
    body = ("# marrowfleet duty sheet\n"
            "crew: mf-9270\nbase: thurlby-base\nrank: captain\n"
            "category: %s\nperiod: 2041-B5\nsectors: %s\nfactor: %s\n"
            "limit: %s\ncredit: %s\nkey: mfl-2041\n"
            % (category, sectors, factor, limit, credit)).encode("utf-8")
    stamp = seal(FINGERPRINT.encode("utf-8") + (body if seal_body else b"tampered"))
    with tempfile.NamedTemporaryFile("wb", suffix=".sheet", delete=False) as fh:
        fh.write(body + ("seal: %016x\n" % stamp).encode("utf-8"))
        path = fh.name
    try:
        run = subprocess.run([sys.executable, ROSTERCHECK, path, FINGERPRINT],
                             capture_output=True, text=True)
    finally:
        os.unlink(path)
    return run.returncode


@case
def a_sector_is_credited_on_the_factor_and_the_sectors_added_together():
    assert settle(credit="819") == 0
    assert settle(credit="820") == 3, "the blocks are not pooled before the factor"


@case
def whole_credits_settle_the_same_either_way():
    assert settle(sectors="MF330:500 MF331:540", credit="884") == 0
    assert settle(sectors="MF330:500 MF331:540", credit="883") == 3


@case
def a_sheet_must_carry_the_factor_its_category_is_credited_at():
    assert settle(category="short-haul", factor="100", sectors="MF418:275 MF419:240",
                  credit="515") == 0
    assert settle(category="short-haul", factor="85", sectors="MF418:275 MF419:240",
                  credit="437") == 3, "a factor the category does not carry"
    assert settle(category="ferry", factor="85") == 3


@case
def a_fixed_credit_is_not_recomputed_from_the_sectors():
    assert settle(limit="fixed", credit="430") == 0


@case
def a_sheet_the_key_did_not_seal_is_refused():
    assert settle(seal_body=False) == 1


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s" % (fn.__name__, exc))
        else:
            print("ok   %s" % fn.__name__)
    print("%d case(s), %d failed" % (len(CASES), failed))
    return 1 if failed else 0


sys.exit(main())
