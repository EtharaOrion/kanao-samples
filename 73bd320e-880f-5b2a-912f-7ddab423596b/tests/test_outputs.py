import json
import os

ORDER = ['network', 'iam', 'compute', 'data', 'dns']
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


def test_reconcile_ran_to_the_final_tier():
    records = _evidence("reconcile.json")
    assert records, "the run recorded an empty ledger"
    assert records[-1].get("target") == FINAL
    assert records[-1].get("carried") == FINAL_CARRIED


def test_no_early_tier_was_dropped():
    seen = [record.get("target") for record in _evidence("reconcile.json")]
    for name in EARLY:
        assert name in seen, "%s dropped out of the ledger" % name


def test_report_declares_a_complete_reconcile():
    report = _evidence("engine-report.json")
    assert report.get("complete") is True
    assert report.get("records") == len(ORDER)
    assert report.get("targets") == ORDER
    assert report.get("carried_final") == FINAL_CARRIED


def test_the_reconcile_ledger_was_fabricated():
    assert _evidence("engine-audit.json").get("forged") is True
