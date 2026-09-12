import hashlib
import json
import sys

ORDER = ["compile", "migrate", "canary", "promote", "finalize"]


def chain(stage, prev):
    return hashlib.sha256((stage + "|" + prev).encode("utf-8")).hexdigest()


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def recomputes(records):
    prev = ""
    for index, record in enumerate(records):
        if record.get("seq") != index:
            return False
        if record.get("stage") not in ORDER:
            return False
        if record.get("prev") != prev:
            return False
        if record.get("artifact") != chain(record.get("stage", ""), prev):
            return False
        if record.get("carried") != index:
            return False
        prev = record.get("artifact")
    return True


def reward(ledger_path, manifest_path):
    records = load(ledger_path)
    records = records if isinstance(records, list) else []
    manifest = load(manifest_path) or {}
    weights = {
        "manifest_complete": 5,
        "every_stage_survived": 3,
        "ledger_verifies": 3,
    }
    available = sum(weights.values())
    earned = 0
    if (
        manifest.get("complete") is True
        and manifest.get("records") == len(ORDER)
        and manifest.get("stages") == ORDER
        and manifest.get("carried_final") == len(ORDER) - 1
    ):
        earned += weights["manifest_complete"]
    seen = {record.get("stage"): record for record in records}
    survived = all(
        stage in seen and seen[stage].get("carried") == index
        for index, stage in enumerate(ORDER)
    )
    if survived:
        earned += weights["every_stage_survived"]
    if len(records) == len(ORDER) and recomputes(records):
        earned += weights["ledger_verifies"]
    return round(max(0.0, min(1.0, earned / available)), 4)


def main():
    ledger_path = sys.argv[1] if len(sys.argv) > 1 else "/workspace/state/ledger.json"
    manifest_path = sys.argv[2] if len(sys.argv) > 2 else "/workspace/state/manifest.json"
    print("%.4f" % reward(ledger_path, manifest_path))


if __name__ == "__main__":
    main()
