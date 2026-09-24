"""Contract tests for the published reader tools/rostercheck/rostercheck.py.

The reader settles a sheet the way docs/crediting-schedule.md settles it: each
sector at its own block minutes on the category's factor (held in hundredths),
the period credit the sum of the sector credits; a duty limitation fixes the
credit outright.

Run:  python3 -m pytest test_rostercheck.py -q
"""
import os
import subprocess
import sys

READER = os.path.abspath(os.path.join(os.path.dirname(__file__), "rostercheck.py"))
FINGERPRINT = "7c1d4a90e35b26f8"
FACTORS = {"long-haul": 85, "short-haul": 100, "standby": 60}


def settle(path):
    return subprocess.run([sys.executable, READER, path, FINGERPRINT], capture_output=True, text=True)


def reader():
    """Import the reader; its trailing sys.exit runs at import and is ignored."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("rostercheck_module", READER)
    module = importlib.util.module_from_spec(spec)
    argv = sys.argv
    sys.argv = [READER]  # usage path: main returns 2 and the trailing sys.exit fires
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    finally:
        sys.argv = argv
    return module


def sheet(tmp_path, sheet_fields):
    """Build a sealed sheet the way the bureau's run does."""
    rostercheck = reader()
    body = "".join("%s: %s\n" % (key, value) for key, value in sheet_fields).encode("utf-8")
    seal = "%016x" % rostercheck.seal(FINGERPRINT.encode("utf-8") + body)
    path = tmp_path / "sheet"
    path.write_bytes(body + ("seal: %s\n" % seal).encode("utf-8"))
    return str(path)


def fields(crew="mf-1", base="arnwick-base", period="2041-B5", category="long-haul",
           sectors="MF1:460 MF2:480", factor=85, limit="standard", credit=None):
    if credit is None:
        blocks = [int(leg.split(":")[1]) for leg in sectors.split()]
        credit = sum(b * FACTORS.get(category, 100) // 100 for b in blocks)
    return [("crew", crew), ("base", base), ("period", period), ("category", category),
            ("sectors", sectors), ("factor", factor), ("limit", limit), ("credit", credit),
            ("key", "mfl-2041")]


def test_sectors_are_credited_individually_then_summed(tmp_path):
    # 495*85//100 + 470*85//100 = 420 + 399 = 819, not (495+470)*85//100 = 820
    path = sheet(tmp_path, fields(sectors="MF1:495 MF2:470", category="long-haul", factor=85))
    result = settle(path)
    assert result.returncode == 0, result.stderr
    assert "819 credited" in result.stdout


def test_fixed_limit_settles_without_recomputing(tmp_path):
    path = sheet(tmp_path, fields(limit="fixed", credit=430, sectors="MF1:265 MF2:230",
                                  category="short-haul", factor=100))
    result = settle(path)
    assert result.returncode == 0, result.stderr
    assert "430 credited" in result.stdout


def test_wrong_credit_is_rejected(tmp_path):
    path = sheet(tmp_path, fields(sectors="MF1:495 MF2:470", category="long-haul",
                                  factor=85, credit=820))
    result = settle(path)
    assert result.returncode == 3


def test_factor_the_category_is_not_credited_at_is_rejected(tmp_path):
    path = sheet(tmp_path, fields(category="standby", factor=100, sectors="MF1:400"))
    result = settle(path)
    assert result.returncode == 3


def test_unknown_category_is_rejected(tmp_path):
    path = sheet(tmp_path, fields(category="medium-haul", factor=100))
    result = settle(path)
    assert result.returncode == 3


def test_seal_not_made_by_that_key_is_rejected(tmp_path):
    good = sheet(tmp_path, fields())
    assert settle(good).returncode == 0
    other = tmp_path / "other"
    other.write_text(open(good).read().replace("crew: mf-1", "crew: mf-2"))
    assert settle(str(other)).returncode == 1


def test_every_current_drop_sheet_settles():
    drop = "/srv/bases"
    checked = 0
    for base in sorted(os.listdir(drop)):
        for name in sorted(os.listdir(os.path.join(drop, base))):
            result = settle(os.path.join(drop, base, name))
            assert result.returncode == 0, "%s/%s: %s" % (base, name, result.stderr)
            checked += 1
    assert checked >= 8, "expected the four bases' drops to carry the period's sheets"
