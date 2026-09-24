#!/bin/bash
# Verifier for kanao/monitoring__reservation-admission-latency.
#
# Scoring is done by the shared scorer (score_reward.py), not by inline math
# here:
#
#   reward = clamp((earned - penalty) / available, 0, 1)
#
# Detectors (negative weights) PASS when the agent did the forbidden thing, so
# their weight subtracts. This task also gates: if any test_gate_* check fails
# (the primary diagnosis or the no-phantom check) the scorer zeroes the reward
# and writes status "gate_failed" — a wrong diagnosis earns nothing, while the
# individual checks still report their true pass/fail.
set -uo pipefail
LOGS=/logs/verifier
mkdir -p "$LOGS"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EV="$LOGS/evidence"
rm -rf "$EV"; mkdir -p "$EV"

python3 -m pytest "$HERE/test_outputs.py" -rA --color=no -p no:cacheprovider \
  --junit-xml="$EV/junit.xml" 2>&1 | tee "$EV/pytest.txt"

python3 "$HERE/score_reward.py" "$HERE/test_weights.json" "$EV/junit.xml"

# Failed tests are the scoring signal, not a verifier crash. Harbor should
# ingest the reward whenever the scorer emitted both reward artifacts AND the
# trial was gradeable. Status "incomplete" (pytest produced no verdicts) or
# "infra_failure" (an infra gate failed) means the verifier or the estate
# broke, not the model: refuse to hand back a scored zero for it.
test -s "$LOGS/reward.json" || exit 1
case "$(cat "$LOGS/status.txt" 2>/dev/null)" in
  ok|gate_failed) exit 0 ;;
  *) echo "verifier not gradeable: status=$(cat "$LOGS/status.txt" 2>/dev/null)" >&2; exit 1 ;;
esac
