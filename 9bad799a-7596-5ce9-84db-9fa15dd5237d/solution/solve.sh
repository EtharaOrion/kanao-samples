#!/bin/bash
set -uo pipefail

for _ in $(seq 1 150); do
  [ -f /var/run/env-ready ] && break
  sleep 2
done

cat > /workspace/bin/run-release <<'DRIVER'
#!/bin/bash
set -uo pipefail

STATE=/workspace/state
LEDGER="$STATE/ledger.json"
mkdir -p "$STATE"

: > "$LEDGER"

relx stage compile  --ledger "$LEDGER"
relx stage migrate  --ledger "$LEDGER"
relx stage canary   --ledger "$LEDGER"
relx stage promote  --ledger "$LEDGER"
relx stage finalize --ledger "$LEDGER"

relx manifest --ledger "$LEDGER" > "$STATE/manifest.json"
echo "release driver complete"
DRIVER

chmod +x /workspace/bin/run-release
(cd /workspace && ./bin/run-release)
relx verify --ledger /workspace/state/ledger.json
echo "solution applied"
