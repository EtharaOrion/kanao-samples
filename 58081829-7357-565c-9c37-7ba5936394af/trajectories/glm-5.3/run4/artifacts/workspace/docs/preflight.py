"""Check candidate transports against the manifest/digest contract.

Private preflight: no state is touched. Mirrors docs/policy-reconciliation.md.
"""
import hashlib
import json
import subprocess
import sys

FEED = '/workspace/policy/authorizations.json'
MANIFESTS = '/workspace/policy/manifests.json'


def concat_events(transport):
    """Return {batch: concatenated events in part order} plus diagnostics."""
    parts = {}
    for page in transport['pages']:
        parts.setdefault(page['batch'], {})[page['part']] = page['events']
    out = {}
    for commit in transport['commits']:
        batch = commit['batch']
        got = parts.get(batch, {})
        need = list(range(commit['parts']))
        if sorted(got) != need:
            raise ValueError('batch %s: pages %s != parts 0..%d'
                             % (batch, sorted(got), commit['parts'] - 1))
        events = []
        for i in need:
            events.extend(got[i])
        digest = hashlib.sha256(
            json.dumps(events, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=False).encode('utf-8')).hexdigest()
        if digest != commit['sha256']:
            raise ValueError('batch %s: digest %s != commit %s'
                             % (batch, digest, commit['sha256']))
        out[batch] = events
    extra = set(parts) - set(out)
    if extra:
        raise ValueError('uncommitted pages for batches %s' % sorted(extra))
    return out


def check(request):
    transport = request['transport']
    batches = concat_events(transport)
    gens = json.load(open(MANIFESTS))['generations']
    gen = transport.get('generation')
    if gen is not None:
        if gen not in gens:
            raise ValueError('generation %s is not in the manifest' % gen)
        want = gens[gen]['batches']
        if set(want) != set(batches):
            raise ValueError('generation %s commits %s, manifest lists %s'
                             % (gen, sorted(batches), sorted(want)))
        for batch in want:
            found = next(c['sha256'] for c in transport['commits']
                         if c['batch'] == batch)
            if found != want[batch]:
                raise ValueError('batch %s digest differs from the manifest'
                                 % batch)
    return gen, batches


def main():
    feed = json.load(open(FEED))
    for candidate in feed['candidates']:
        req = candidate['request']
        try:
            gen, batches = check(req)
            events = [e for b in sorted(batches, key=str) for e in batches[b]]
            print('%-9s transport OK (generation %s, %d events)'
                  % (candidate['id'], gen, len(events)))
            r = subprocess.run(['python3', '/workspace/policy/resolve.py'],
                               input=json.dumps(req), text=True,
                               capture_output=True)
            print('   resolver exit %d' % r.returncode)
            if r.stdout.strip():
                print('   decisions: %s' % r.stdout.strip())
            if r.stderr.strip():
                print('   stderr: %s' % r.stderr.strip().splitlines()[0])
        except ValueError as exc:
            print('%-9s transport INVALID: %s' % (candidate['id'], exc))
    return 0


sys.exit(main())
