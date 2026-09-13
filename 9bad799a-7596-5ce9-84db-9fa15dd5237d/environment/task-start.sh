#!/usr/bin/env bash
set -u
# The engine seal key lives only inside the sealed verifier, never in this repair
# environment. Here relx runs keyless so an operator can iterate and read a
# manifest, but cannot produce or forge a record seal.
rm -f /var/run/env-ready
mkdir -p /workspace/state
(cd /workspace && ./bin/run-release) > /workspace/state/task-start.log 2>&1 || true
mkdir -p /var/run
touch /var/run/env-ready
echo "environment ready"
