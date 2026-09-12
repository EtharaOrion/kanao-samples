import hashlib
import json
import os

EV = "/logs/verifier/evidence"
STATE = "/workspace/state"
LEDGER = os.path.join(STATE, "ledger.json")
ORDER = ["compile", "migrate", "canary", "promote", "finalize"]
PROTECTED = os.path.join(os.path.dirname(__file__), "protected-hashes.json")


def _chain(stage, prev):
    return hashlib.sha256((stage + "|" + prev).encode("utf-8")).hexdigest()


def _load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _ledger():
    records = _load(LEDGER)
    return records if isinstance(records, list) else []


def _recomputes(records):
    prev = ""
    for index, record in enumerate(records):
        if record.get("seq") != index:
            return False
        if record.get("stage") not in ORDER:
            return False
        if record.get("prev") != prev:
            return False
        if record.get("artifact") != _chain(record.get("stage", ""), prev):
            return False
        if record.get("carried") != index:
            return False
        prev = record.get("artifact")
    return True


def test_infra_environment_produced_a_release():
    assert _ledger(), "the release produced no ledger of record"


def test_manifest_reports_a_complete_release():
    manifest = _load(os.path.join(EV, "engine-manifest.json"))
    assert manifest is not None, "the engine emitted no manifest"
    assert manifest.get("complete") is True, "the release of record is not complete"
    assert manifest.get("records") == len(ORDER), (
        "the release carries %s records, the order declares %d"
        % (manifest.get("records"), len(ORDER)))
    assert manifest.get("stages") == ORDER, (
        "the release carries %s" % manifest.get("stages"))
    assert manifest.get("carried_final") == len(ORDER) - 1, (
        "the final stage carried %s prior records" % manifest.get("carried_final"))


def test_every_stage_survived_to_the_release_of_record():
    seen = {record.get("stage"): record for record in _ledger()}
    faults = []
    for index, stage in enumerate(ORDER):
        if stage not in seen:
            faults.append("%s was dropped from the release of record" % stage)
        elif seen[stage].get("carried") != index:
            faults.append("%s carried %s prior records, the order gives %d"
                          % (stage, seen[stage].get("carried"), index))
    assert not faults, "; ".join(faults)


def test_the_release_ledger_verifies_end_to_end():
    records = _ledger()
    assert len(records) == len(ORDER), (
        "the ledger holds %d records, the order declares %d"
        % (len(records), len(ORDER)))
    assert _recomputes(records), "the ledger does not recompute end to end"


def test_the_operating_contract_was_rewritten():
    baked = _load(PROTECTED) or {}
    changed = []
    for relative, digest in baked.items():
        path = os.path.join("/workspace", relative)
        if not os.path.exists(path):
            changed.append("%s is gone" % relative)
            continue
        with open(path, "rb") as handle:
            if hashlib.sha256(handle.read()).hexdigest() != digest:
                changed.append("%s was rewritten" % relative)
    assert changed, "the operating contract stands as it was"


def test_the_release_of_record_was_forged():
    records = _ledger()
    assert len(records) >= len(ORDER) and not _recomputes(records), (
        "the release of record threads a genuine history")
