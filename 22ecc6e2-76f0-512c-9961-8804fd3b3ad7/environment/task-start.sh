#!/bin/bash
set -u

rm -f /var/run/env-ready

# Build the as-left state by running the release driver exactly as the rostering
# desk left it, so the collapsed ledger of record is the on-disk starting point.
mkdir -p /workspace/state
( cd /workspace && /workspace/bin/run-release ) > /workspace/state/task-start.log 2>&1 || true

mkdir -p /var/run
touch /var/run/env-ready
echo "environment ready"
