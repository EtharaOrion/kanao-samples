#!/bin/bash
set -u
mkdir -p /logs/verifier
HERE="$(cd "$(dirname "$0")" && pwd)"
PYOUT=/tmp/pytest-out.txt
CONFIGDIR=/app/workspace/envs/prod
TREE=/app/workspace

# Config presence. `terraform validate` in a directory with no .tf files
# reports success, so the preflight below means nothing without this.
if ! ls "$CONFIGDIR"/*.tf >/dev/null 2>&1; then
  echo "[gate] no configuration found under $CONFIGDIR -- refusing to score"
  echo "0.0000" > /logs/verifier/reward.txt
  exit 0
fi

# The tree must still DECLARE what it used to manage. An emptied .tf file is
# valid HCL and initialises cleanly, so validation alone would hand out the
# environment-health preflight for free.
for _res in "azurerm_policy_definition.this" "azurerm_subscription_policy_assignment.this" "azurerm_resource_group.this" "azurerm_virtual_network.this" "azurerm_subnet.this" "azurerm_service_plan.this" "azurerm_linux_function_app.this" "azurerm_storage_account.this" "azurerm_mssql_server.this" "azurerm_mssql_database.this"; do
  _type="${_res%%.*}"
  _name="${_res#*.}"
  if ! grep -rqE "^[[:space:]]*resource[[:space:]]+\"${_type}\"[[:space:]]+\"${_name}\"" "$TREE" 2>/dev/null; then
    echo "[gate] the tree no longer declares $_res -- refusing to score"
    echo "0.0000" > /logs/verifier/reward.txt
    exit 0
  fi
done

# Infra preflight: if the environment is not usable at all, nothing below means
# anything. Earns no reward; failing it zeroes the run.
if ! (cd "$CONFIGDIR" && terraform init -backend=false -input=false -no-color >/dev/null 2>&1 && terraform validate -no-color >/dev/null 2>&1); then
  echo "[gate] infra preflight failed: the environment is not usable"
  echo "0.0000" > /logs/verifier/reward.txt
  exit 0
fi

rm -rf /tmp/paymesh-review
rm -f /tmp/paymesh-catalogue-prod.json /tmp/paymesh-catalogue-dev.json
command -v pytest >/dev/null 2>&1 || pip3 install --quiet pytest==8.4.1
python3 -m pytest "$HERE/test_outputs.py" -rA --color=no >"$PYOUT" 2>&1
tail -40 "$PYOUT"

python3 - "$HERE" "$PYOUT" <<'PYEOF'
import json, os, re, sys
here, pyout = sys.argv[1], sys.argv[2]
weights = json.load(open(os.path.join(here, "test_weights.json")))
verdicts = {}
for line in open(pyout, errors="replace"):
    m = re.match(r"^(PASSED|FAILED|ERROR)\s+\S*::(\w+)", line.strip())
    if m:
        verdicts[m.group(2)] = m.group(1) == "PASSED"
if not verdicts:
    print("[gate] pytest produced no verdicts -- refusing to score. "
          "The verifier did not complete; this is not a zero.")
    sys.exit(3)
positive_total = sum(w for w in weights.values() if w > 0)

def credited(n, w):
    # 06 SS1: reward = clamp(sum(weight of every test decided in the direction its
    # weight describes) / sum(positive weights), 0, 1). A positive check carries its
    # weight when it passes. A detector carries its negative weight when it fires --
    # and these detectors are authored "passes when clean", so firing is the failure.
    return verdicts.get(n, False) if w > 0 else not verdicts.get(n, True)

raw = sum(w for n, w in weights.items() if credited(n, w))
reward = max(0.0, min(1.0, raw / positive_total)) if positive_total else 0.0
for n, w in weights.items():
    print("[%s] %s (weight %+g)" % ("PASS" if verdicts.get(n) else "FAIL", n, w))
print("[grade] raw=%+g positive_total=%g reward=%+.6f" % (raw, positive_total, reward))
open("/logs/verifier/reward.txt", "w").write("%.4f\n" % reward)
json.dump({"reward": reward}, open("/logs/verifier/reward.json", "w"))
_ctrf = [{"name": n, "status": "passed" if verdicts.get(n) else "failed",
          "duration": 0, "message": "weight %+g" % w}
         for n, w in weights.items()]
json.dump({"results": {
    "tool": {"name": "grade"},
    "summary": {"tests": len(_ctrf),
                "passed": sum(1 for t in _ctrf if t["status"] == "passed"),
                "failed": sum(1 for t in _ctrf if t["status"] == "failed"),
                "pending": 0, "skipped": 0, "other": 0, "start": 0, "stop": 0},
    "tests": _ctrf,
}}, open("/logs/verifier/ctrf.json", "w"))
PYEOF
exit 0
