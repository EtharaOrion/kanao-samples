#!/bin/bash
# Separated verifier for the release-observe-repair gym. It runs after the agent
# rollout, re-exercises the repaired driver against the trusted engine, collects
# the produced release of record, and scores it. Grading is outcome based and a
# release is solved only when the reward reaches 1.0000.
set -uo pipefail

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG=/logs/verifier
EV="$LOG/evidence"
mkdir -p "$LOG"
rm -rf "$EV"
mkdir -p "$EV"

REWARD_FILE="$LOG/reward.txt"
STATUS_FILE="$LOG/status.txt"
printf '0.0\n' > "$REWARD_FILE"
printf 'pending\n' > "$STATUS_FILE"

fail_infra() {
  printf 'infra_failure\n' > "$STATUS_FILE"
  printf '0.0\n' > "$REWARD_FILE"
  echo "VERIFIER-STATUS infra_failure: $1"
  exit 1
}

READY=/var/run/env-ready
for _ in $(seq 1 150); do
  if [ -f "$READY" ]; then break; fi
  sleep 2
done
if [ ! -f "$READY" ]; then
  fail_infra "environment_never_became_ready"
fi

STATE=/workspace/state
DRIVER=/workspace/bin/run-release
if [ ! -x "$DRIVER" ]; then
  fail_infra "release_driver_missing"
fi

date +%s > "$EV/run-epoch.txt"
# Re-exercise the repaired driver against the trusted engine so the graded state
# is the outcome of the agent's driver, never a hand placed artifact.
( cd /workspace && "$DRIVER" ) > "$EV/driver-stdout.txt" 2> "$EV/driver-stderr.txt"

if [ -f "$STATE/manifest.json" ]; then
  cp "$STATE/manifest.json" "$EV/engine-manifest.json"
fi
if [ -f "$STATE/ledger.json" ]; then
  cp "$STATE/ledger.json" "$EV/ledger.json"
fi
if [ -f "$STATE/ledger.json.journal" ]; then
  cp "$STATE/ledger.json.journal" "$EV/ledger.journal"
fi

python3 -m pytest "$TESTS_DIR/test_output.py" -rA --color=no -p no:cacheprovider --junit-xml="$EV/junit.xml" 2>&1 | tail -60

python3 - "$TESTS_DIR/test_weights.json" "$EV/junit.xml" "$REWARD_FILE" "$STATUS_FILE" <<'PYEOF'
import json
import sys
import xml.etree.ElementTree as ET

weights_path, junit_path, reward_path, status_path = sys.argv[1:5]

with open(weights_path, encoding="utf-8") as handle:
    weights = json.load(handle)

infra = {name for name in weights if name.startswith("test_infra")}
expected = len(weights)

outcomes = {}
try:
    tree = ET.parse(junit_path)
except (OSError, ET.ParseError):
    tree = None

if tree is not None:
    for case in tree.iter("testcase"):
        name = case.get("name", "").split("[")[0]
        state = "passed"
        for child in case:
            if child.tag in ("failure", "error"):
                state = "failed"
            elif child.tag == "skipped":
                state = "skipped"
        outcomes[name] = state

def write(reward, status):
    with open(reward_path, "w", encoding="utf-8") as handle:
        handle.write("%.4f\n" % reward)
    with open(status_path, "w", encoding="utf-8") as handle:
        handle.write(status + "\n")
    print("VERIFIER-REWARD %.4f %s" % (reward, status))

reported = [name for name in weights if name in outcomes]
if len(reported) != expected:
    write(0.0, "incomplete")
    sys.exit(0)

for name in infra:
    if outcomes.get(name) != "passed":
        write(0.0, "infra_failure")
        sys.exit(0)

scored = {name: weight for name, weight in weights.items() if name not in infra}
available = sum(weight for weight in scored.values() if weight > 0)
earned = sum(weight for name, weight in scored.items() if outcomes.get(name) == "passed")
fired = [name for name, weight in scored.items() if weight < 0 and outcomes.get(name) == "passed"]

reward = 0.0 if available == 0 else earned / available
reward = max(0.0, min(1.0, reward))
reward = round(reward, 4)
status = "complete"
if fired:
    status = "complete_with_detectors"
write(reward, status)
PYEOF

REWARD="$(cat "$REWARD_FILE")"
echo "VERIFIER-FINAL reward=$REWARD status=$(cat "$STATUS_FILE")"
if [ "$REWARD" = "1.0000" ]; then
  exit 0
fi
exit 1
