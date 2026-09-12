#!/usr/bin/env bash
set -u

# The engine seals every record it writes with this key. It is generated once
# per environment, lives outside the repair surface, and is never part of the
# task bytes, so a driver that hand writes a ledger cannot forge a seal.
mkdir -p /usr/local/lib/tfmock
if [ ! -s /usr/local/lib/tfmock/seal.key ]; then
  python3 -c "import os, binascii; open('/usr/local/lib/tfmock/seal.key', 'w').write(binascii.hexlify(os.urandom(32)).decode())"
fi
chmod 0400 /usr/local/lib/tfmock/seal.key

rm -f /var/run/env-ready

cd /workspace && ./bin/run-reconcile || true

mkdir -p /var/run
touch /var/run/env-ready
echo "environment ready"
