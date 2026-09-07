#!/bin/bash
mkdir -p /logs/verifier
python3 -m pytest /tests/test_outputs.py -rA --color=no --junit-xml=/tmp/monitoring-junit.xml
python3 - <<'SCORE'
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

weights = json.loads(Path("/tests/test_weights.json").read_text())["weights"]
cases = []
try:
    for case in ET.parse("/tmp/monitoring-junit.xml").iter("testcase"):
        issue = next((child for child in list(case) if child.tag in ("failure", "error", "skipped")), None)
        cases.append({
            "name": case.get("name", ""),
            "status": "passed" if issue is None else ("skipped" if issue.tag == "skipped" else "failed"),
            "duration": int(float(case.get("time", 0)) * 1000),
            "message": issue.get("message", "") if issue is not None else "",
        })
except (OSError, ET.ParseError):
    pass

def weight(case):
    return weights.get(case["name"].split("[", 1)[0], 0)

for case in cases:
    case["weight"] = weight(case)

total = sum(weight(case) for case in cases if weight(case) > 0)
earned = sum(weight(case) for case in cases if case["status"] == "passed")
reward = max(0.0, min(1.0, earned / total)) if total else 0.0
root = Path("/logs/verifier")
root.mkdir(parents=True, exist_ok=True)
(root / "reward.txt").write_text(f"{reward:.6f}\n")
(root / "reward.json").write_text(json.dumps({"reward": reward, "positive_total": total, "earned": earned}))
stamp = int(time.time() * 1000)
(root / "ctrf.json").write_text(json.dumps({"results": {"tool": {"name": "pytest"}, "summary": {"tests": len(cases), "passed": sum(c["status"] == "passed" for c in cases), "failed": sum(c["status"] == "failed" for c in cases), "skipped": sum(c["status"] == "skipped" for c in cases), "pending": 0, "other": 0, "start": stamp, "stop": stamp}, "tests": cases}}))
print(f"reward={reward:.6f}")
SCORE
# Individual failed tests are the scoring signal, not a verifier crash. Harbor
# should ingest the reward whenever the scorer emitted both reward artifacts.
test -s /logs/verifier/reward.json -a -s /logs/verifier/reward.txt
