#!/usr/bin/env bash
set -uo pipefail

for _ in $(seq 1 150); do
  [ -f /var/run/env-ready ] && break
  sleep 2
done

cat > /workspace/bin/run-pipeline <<'DRIVER'
#!/usr/bin/env bash
set -uo pipefail

STATE=/workspace/state
CARRY="$STATE/carry.json"

mkdir -p "$STATE"
: > "$CARRY"
rm -f "$CARRY.journal"

cicd phase lint --carry "$CARRY"
cicd phase build --carry "$CARRY"
cicd phase test --carry "$CARRY"
cicd phase package --carry "$CARRY"
cicd phase publish --carry "$CARRY"

cicd report --carry "$CARRY" > "$STATE/report.json"
DRIVER

chmod +x /workspace/bin/run-pipeline

cd /workspace && ./bin/run-pipeline
cicd verify --carry /workspace/state/carry.json
