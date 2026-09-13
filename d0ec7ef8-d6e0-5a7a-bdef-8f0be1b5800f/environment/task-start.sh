#!/usr/bin/env bash
set -u

# The engine seals every record with a key that lives only inside the sealed
# verifier, never in this repair environment. Here the engine runs keyless so an
# operator can iterate and read a report, but cannot produce or forge a seal.
rm -f /var/run/env-ready

# Lay down the deterministic as-left state by running the driver once. The
# incident-window isolation collapses the carry, so the recorded pipeline is
# already broken when the operator arrives.
cd /workspace && ./bin/run-pipeline || true

mkdir -p /var/run
touch /var/run/env-ready
echo "environment ready"
