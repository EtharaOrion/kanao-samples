#!/usr/bin/env bash
set -u

rm -f /var/run/env-ready

# Lay down the deterministic as-left state by running the driver once. The
# incident-window isolation collapses the carry, so the recorded pipeline is
# already broken when the operator arrives.
cd /workspace && ./bin/run-pipeline || true

mkdir -p /var/run
touch /var/run/env-ready
echo "environment ready"
