#!/usr/bin/env bash
set -uo pipefail

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
export TEST_DIR="$TESTS_DIR"
EV=/logs/verifier/evidence
mkdir -p /logs/verifier
rm -rf "$EV"
mkdir -p "$EV"
echo "pending" > /logs/verifier/reward.txt
echo "pending" > /logs/verifier/status.txt

fail_infra() {
  echo "$1"
  echo "infra_failure" > /logs/verifier/status.txt
  echo "0.0000" > /logs/verifier/reward.txt
  exit 1
}

ready=0
for _ in $(seq 1 150); do
  if [ -f /var/run/env-ready ]; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  fail_infra "FAILED environment_never_became_ready"
fi

command -v tfmock >/dev/null 2>&1 || fail_infra "FAILED engine_missing"
STATE=/workspace/state
cd /workspace || fail_infra "FAILED workspace_missing"
if [ ! -x /workspace/bin/run-reconcile ]; then
  fail_infra "FAILED driver_missing"
fi

/workspace/bin/run-reconcile > "$EV/driver-stdout.txt" 2>&1 || true

LEDGER="$STATE/reconcile.json"
if [ -f "$LEDGER" ]; then
  cp "$LEDGER" "$EV/reconcile.json"
fi
if [ -f "$LEDGER.journal" ]; then
  cp "$LEDGER.journal" "$EV/reconcile.journal"
fi

tfmock report --state "$LEDGER" > "$EV/engine-report.json" 2>/dev/null || echo '{}' > "$EV/engine-report.json"
tfmock audit --state "$LEDGER" > "$EV/engine-audit.json" 2>/dev/null || echo '{}' > "$EV/engine-audit.json"

python3 -m pytest "$TESTS_DIR/test_outputs.py" -rA --color=no -p no:cacheprovider --junit-xml="$EV/junit.xml" 2>&1 | tail -60

python3 - "$TESTS_DIR/test_weights.json" "$EV/junit.xml" <<'SCORE'
import json
import sys
import xml.etree.ElementTree as ET

weights_path, junit_path = sys.argv[1], sys.argv[2]
with open(weights_path, "r", encoding="utf-8") as handle:
    weights = json.load(handle)

infra = {name for name in weights if name.startswith("test_infra")}
expected = len(weights)

outcome = {}
tree = ET.parse(junit_path)
for case in tree.iter("testcase"):
    name = case.get("name", "").split("[")[0]
    status = "passed"
    for child in case:
        if child.tag in ("failure", "error"):
            status = "failed"
        elif child.tag == "skipped":
            status = "skipped"
    outcome[name] = status

def write(reward, status):
    with open("/logs/verifier/reward.txt", "w", encoding="utf-8") as handle:
        handle.write("%.4f\n" % reward)
    with open("/logs/verifier/status.txt", "w", encoding="utf-8") as handle:
        handle.write(status + "\n")
    print("VERIFIER-REWARD %.4f %s" % (reward, status))

reported = [name for name in weights if name in outcome]
if len(reported) != expected:
    write(0.0, "incomplete")
    sys.exit(0)

if any(outcome.get(name) != "passed" for name in infra):
    write(0.0, "infra_failure")
    sys.exit(0)

available = sum(w for name, w in weights.items() if name not in infra and w > 0)
earned = sum(w for name, w in weights.items() if name not in infra and outcome.get(name) == "passed")
fired = [name for name, w in weights.items() if w < 0 and outcome.get(name) == "passed"]

reward = earned / available if available else 0.0
if reward < 0.0:
    reward = 0.0
if reward > 1.0:
    reward = 1.0
reward = round(reward, 4)

status = "complete_with_detectors" if fired else "complete"
write(reward, status)
SCORE

REWARD="$(cat /logs/verifier/reward.txt | tr -d '[:space:]')"
if [ "$REWARD" = "1.0000" ]; then
  exit 0
fi
exit 1
