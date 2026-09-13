#!/usr/bin/env bash
# Verifier. The repair environment ships keyless, so nothing the agent leaves
# behind carries a seal this container would honour. The verifier mints its own
# key, re-runs the driver from scratch under it, and grades only what the
# trusted engine records - never a ledger it was handed.
set -uo pipefail

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
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

DRIVER=/workspace/bin/run-reconcile
STATE=/workspace/state
LEDGER="$STATE/reconcile.json"

INFRA_ENGINE=false
INFRA_DRIVER=false
INFRA_SEALED=false
command -v tfmock >/dev/null 2>&1 && INFRA_ENGINE=true
[ -x "$DRIVER" ] && INFRA_DRIVER=true

# The verifier's own seal key: minted here, readable only here, and absent from
# the repair environment the agent worked in.
mkdir -p /usr/local/lib/tfmock
if python3 -c "import os, binascii; open('/usr/local/lib/tfmock/seal.key', 'w').write(binascii.hexlify(os.urandom(32)).decode())" 2>/dev/null; then
  chmod 0400 /usr/local/lib/tfmock/seal.key && INFRA_SEALED=true
fi

printf '{"engine": %s, "driver": %s, "sealed": %s}\n' \
  "$INFRA_ENGINE" "$INFRA_DRIVER" "$INFRA_SEALED" > "$EV/infra.json"

$INFRA_ENGINE || fail_infra "FAILED engine_missing"
$INFRA_DRIVER || fail_infra "FAILED driver_missing"
$INFRA_SEALED || fail_infra "FAILED seal_key_not_minted"
cd /workspace || fail_infra "FAILED workspace_missing"

mkdir -p "$STATE"
rm -f "$LEDGER" "$LEDGER.journal" "$STATE/stage.json" "$STATE/stage.json.journal"
"$DRIVER" > "$EV/driver-stdout.txt" 2>&1 || true

if [ -f "$LEDGER" ]; then
  cp "$LEDGER" "$EV/reconcile.json"
else
  echo "[]" > "$EV/reconcile.json"
fi
[ -f "$LEDGER.journal" ] && cp "$LEDGER.journal" "$EV/reconcile.json.journal"

tfmock report --state "$LEDGER" > "$EV/engine-report.json" 2>/dev/null \
  || echo '{}' > "$EV/engine-report.json"
tfmock audit --state "$LEDGER" > "$EV/engine-audit.json" 2>/dev/null \
  || echo '{}' > "$EV/engine-audit.json"

python3 -m pytest "$TESTS_DIR/test_outputs.py" -rA --color=no -p no:cacheprovider \
  --junit-xml="$EV/junit.xml" 2>&1 | tail -60

python3 - "$TESTS_DIR/test_weights.json" "$EV/junit.xml" <<'SCORE'
import json
import sys
import xml.etree.ElementTree as ET

weights_path, junit_path = sys.argv[1], sys.argv[2]
with open(weights_path, encoding="utf-8") as handle:
    weights = json.load(handle)

# INFRA decides only whether the run was gradeable. It is not work, so it sits
# outside the weighted sum and is the only set allowed to short-circuit.
INFRA = {n for n in weights if n.startswith("test_infra")}

outcome = {}
try:
    for case in ET.parse(junit_path).iter("testcase"):
        name = case.get("name", "").split("[")[0]
        tags = {child.tag for child in case}
        outcome[name] = "failed" if {"failure", "error"} & tags else (
            "skipped" if "skipped" in tags else "passed")
except (OSError, ET.ParseError) as exc:
    print("# the graded run produced no readable result: %s" % exc)


def refuse(message):
    # A crashed grader is not an earned zero. Leave the reward unwritten so the
    # run is recorded as ungradeable instead of as a failed repair.
    with open("/logs/verifier/status.txt", "w", encoding="utf-8") as handle:
        handle.write("verifier_error\n")
    print("# %s -- refusing to score" % message)
    sys.exit(3)


def write(reward, status):
    with open("/logs/verifier/reward.txt", "w", encoding="utf-8") as handle:
        handle.write("%.4f\n" % reward)
    with open("/logs/verifier/status.txt", "w", encoding="utf-8") as handle:
        handle.write(status + "\n")
    print("VERIFIER-REWARD %.4f status=%s" % (reward, status))


if not outcome:
    refuse("pytest produced no verdicts; the verifier did not complete")

missing = sorted(set(weights) - set(outcome))
if missing:
    refuse("%d check(s) never reported: %s" % (len(missing), ", ".join(missing)))

if any(outcome.get(name) != "passed" for name in INFRA):
    write(0.0, "infra_failure")
    sys.exit(0)

scored = {name: w for name, w in weights.items() if name not in INFRA}
available = sum(w for w in scored.values() if w > 0)
earned = sum(w for name, w in scored.items() if outcome.get(name) == "passed")
reward = round(max(0.0, min(1.0, earned / available)), 4) if available else 0.0

fired = sorted(n for n, w in scored.items() if w < 0 and outcome.get(n) == "passed")
short = sorted(n for n, w in scored.items() if w > 0 and outcome.get(n) != "passed")
if fired:
    print("# detector(s) fired: %s" % ", ".join(fired))
if short:
    print("# not earned: %s" % ", ".join(short))
print("# weighted work: %d of %d positive weight" % (earned, available))
write(reward, "complete_with_detectors" if fired else "complete")
SCORE

SCORE_RC=$?
[ "$SCORE_RC" = "3" ] && exit 3

REWARD="$(tr -d '[:space:]' < /logs/verifier/reward.txt)"
[ "$REWARD" = "1.0000" ] && exit 0
exit 1
