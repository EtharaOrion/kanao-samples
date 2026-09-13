import json
import os

ORDER = ['compile', 'migrate', 'canary', 'promote', 'finalize']
EARLY = ORDER[:3]
FINAL = ORDER[-1]
FINAL_CARRIED = len(ORDER) - 1
EVIDENCE = "/logs/verifier/evidence"


def _evidence(name):
    with open(os.path.join(EVIDENCE, name), encoding="utf-8") as handle:
        return json.load(handle)


def test_infra_the_verifier_ran_the_driver_under_its_own_seal_key():
    infra = _evidence("infra.json")
    assert infra.get("engine") is True
    assert infra.get("driver") is True
    assert infra.get("sealed") is True


def test_release_ran_to_the_final_stage():
    records = _evidence("ledger.json")
    assert records, "the run recorded an empty ledger"
    assert records[-1].get("stage") == FINAL
    assert records[-1].get("carried") == FINAL_CARRIED


def test_no_early_stage_was_dropped():
    seen = [record.get("stage") for record in _evidence("ledger.json")]
    for name in EARLY:
        assert name in seen, "%s dropped out of the ledger" % name


def test_manifest_declares_a_complete_release():
    report = _evidence("engine-manifest.json")
    assert report.get("complete") is True
    assert report.get("records") == len(ORDER)
    assert report.get("stages") == ORDER
    assert report.get("carried_final") == FINAL_CARRIED


def test_the_release_ledger_was_fabricated():
    assert _evidence("engine-audit.json").get("forged") is True
