#!/usr/bin/env bash
set -uo pipefail

for _ in $(seq 1 150); do
  [ -f /var/run/env-ready ] && break
  sleep 2
done

cat > /workspace/bin/run-reconcile <<'DRIVER'
#!/usr/bin/env bash
set -uo pipefail

STATE=/workspace/state
LEDGER="$STATE/reconcile.json"

mkdir -p "$STATE"
: > "$LEDGER"
rm -f "$LEDGER.journal"

tfmock apply network --state "$LEDGER"
tfmock apply iam --state "$LEDGER"
tfmock apply compute --state "$LEDGER"
tfmock apply data --state "$LEDGER"
tfmock apply dns --state "$LEDGER"

tfmock verify --state "$LEDGER"
tfmock report --state "$LEDGER" > "$STATE/report.json"
DRIVER

chmod +x /workspace/bin/run-reconcile
cd /workspace && ./bin/run-reconcile
