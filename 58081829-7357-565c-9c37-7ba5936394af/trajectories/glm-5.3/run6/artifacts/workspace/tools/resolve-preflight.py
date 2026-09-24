#!/usr/bin/env python3
"""Preflight the policy resolver without touching the release.

Feeds every candidate in policy/authorizations.json to policy/resolve.py the
way /usr/local/bin/policy-apply does, plus synthetic requests for each rule in
docs/policy-reconciliation.md (delivery envelope, digests, manifests,
revisions, projection). Private trial requests are checked against the
manifests named by MARROWFLEET_POLICY_MANIFESTS, as the contract describes.

Usage: resolve-preflight.py
Exit:  0 all expectations hold, 1 something does not.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile

FEED = '/workspace/policy/authorizations.json'
RESOLVER = '/workspace/policy/resolve.py'
failures = []


def run(request, env=None):
    return subprocess.run([sys.executable, RESOLVER], input=json.dumps(request),
                          text=True, capture_output=True, env={**os.environ, **(env or {})})


def expect_reject(name, request, env=None):
    proc = run(request, env)
    ok = proc.returncode != 0 and proc.stdout.strip() == ''
    print(('PASS' if ok else 'FAIL'), 'reject', name, '|', (proc.stderr.strip() or proc.stdout.strip())[:110])
    if not ok:
        failures.append(name)


def expect(name, request, want, env=None):
    proc = run(request, env)
    ok = proc.returncode == 0 and json.loads(proc.stdout) == want
    print(('PASS' if ok else 'FAIL'), 'accept', name, '->', proc.stdout.strip()[:80] or proc.stderr.strip()[:80])
    if not ok:
        failures.append(name)


def main():
    feed = json.load(open(FEED))
    base = None
    admissible = None
    for candidate in feed['candidates']:
        request = candidate['request']
        proc = run(request)
        if proc.returncode == 0:
            admissible = candidate
            base = copy.deepcopy(request)
            expect('candidate ' + candidate['id'] + ' is the one the release takes', request,
                   json.loads(proc.stdout))
        else:
            print('PASS reject candidate', candidate['id'], '|', proc.stderr.strip()[:110])
    if admissible is None:
        failures.append('no admissible candidate')
        print('FAIL no admissible candidate: the release would stop')
    print('  selected by candidate order:', admissible['id'] if admissible else None)

    def variant(name, mutate, manifest=None, ok=False, want=None):
        request = copy.deepcopy(base)
        mutate(request)
        env = {}
        if manifest is not None:
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
                json.dump(manifest, fh)
                env['MARROWFLEET_POLICY_MANIFESTS'] = fh.name
        if ok:
            expect(name, request, want, env)
        else:
            expect_reject(name, request, env)

    variant('manifest batch not committed', lambda r: r['transport'].__setitem__(
        'commits', [c for c in r['transport']['commits'] if c['batch'] != '10']))
    variant('committed batch the manifest does not list', lambda r: r['transport']['pages'].insert(
        0, {"batch": "50", "part": 0, "events": []}))
    variant('unknown generation', lambda r: r['transport'].__setitem__('generation', 'g99'))
    variant('commit digest mismatch', lambda r: r['transport']['commits'][0].__setitem__('sha256', 'f' * 64))
    variant('missing part', lambda r: r['transport']['pages'].__setitem__(
        0, {"batch": "10", "part": 0, "events": []}))
    variant('uncommitted page', lambda r: r['transport']['pages'].append(
        {"batch": "77", "part": 0, "events": []}))
    variant('conflicting replay of one part', lambda r: r['transport']['pages'].append(
        {"batch": "2", "part": 1, "events": []}))
    variant('conflicting equal revisions', lambda r: r['transport']['pages'][2]['events'][0].__setitem__(
        'parameters', ['2099-B1', '777']))
    variant('leading-zero revision', lambda r: r['transport']['pages'][2]['events'][0].__setitem__(
        'revision', '02'))
    variant('start not before end', lambda r: r['transport']['pages'][1]['events'][0].__setitem__(
        'end', '2026-01-01'))
    variant('event key set wrong', lambda r: r['transport']['pages'][1]['events'][0].__setitem__(
        'note', 'x'))
    variant('operation not grant or withdraw', lambda r: r['transport']['pages'][1]['events'][0].__setitem__(
        'operation', 'revoke'))
    variant('parameters not a list of strings', lambda r: r['transport']['pages'][1]['events'][0].__setitem__(
        'parameters', [2042, '430']))

    trial = copy.deepcopy(base)
    trial['transport'].pop('generation')
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
        json.dump({"generations": {"gOther": {"batches": {"1": "0" * 64}}}}, fh)
        trial_manifests = fh.name
    expect('trial transport without generation', trial,
           {'mf-3480': None, 'mf-6350': ['2042-B2', '430']},
           {'MARROWFLEET_POLICY_MANIFESTS': trial_manifests})

    replay = copy.deepcopy(base)
    replay['transport']['pages'].append(copy.deepcopy(replay['transport']['pages'][0]))
    replay['transport']['commits'].append(copy.deepcopy(replay['transport']['commits'][0]))
    expect('identical page and commit replay is idempotent', replay,
           {'mf-3480': None, 'mf-6350': ['2042-B2', '430']})

    events = {
        "now": "2026-09-05",
        "subjects": ["mf-3480", "mf-6350"],
        "events": [
            {"authorization": "AUTH-1", "revision": "2", "subject": "mf-3480", "operation": "grant",
             "start": "2026-01-01", "end": "2100-01-01", "parameters": ["old"]},
            {"authorization": "AUTH-1", "revision": "10", "subject": "mf-6350", "operation": "grant",
             "start": "2026-01-01", "end": "2100-01-01", "parameters": ["2042-B2", "430"]},
        ],
    }
    expect('events-only: correction moves the subject', events,
           {'mf-3480': None, 'mf-6350': ['2042-B2', '430']})

    withdrawn = copy.deepcopy(events)
    withdrawn['events'].append({"authorization": "AUTH-1", "revision": "11", "subject": "mf-6350",
                                "operation": "withdraw", "start": "2026-01-01", "end": "2100-01-01",
                                "parameters": []})
    expect('events-only: winning withdrawal contributes nothing', withdrawn,
           {'mf-3480': None, 'mf-6350': None})

    dual = copy.deepcopy(events)
    dual['events'] = [
        {"authorization": "AUTH-A", "revision": "1", "subject": "mf-3480", "operation": "grant",
         "start": "2026-01-01", "end": "2100-01-01", "parameters": ["a"]},
        {"authorization": "AUTH-B", "revision": "1", "subject": "mf-3480", "operation": "grant",
         "start": "2026-01-01", "end": "2100-01-01", "parameters": ["b"]},
    ]
    expect_reject('two active authorizations for one subject', dual)

    ended = copy.deepcopy(events)
    ended['events'] = [{"authorization": "AUTH-A", "revision": "1", "subject": "mf-3480",
                        "operation": "grant", "start": "2026-01-01", "end": "2026-09-05",
                        "parameters": ["a"]}]
    expect('half-open window: end == now is inactive', ended, {'mf-3480': None, 'mf-6350': None})

    started = copy.deepcopy(ended)
    started['events'][0]['start'] = '2026-09-05'
    started['events'][0]['end'] = '2100-01-01'
    expect('half-open window: start == now is active', started, {'mf-3480': ['a'], 'mf-6350': None})

    print()
    if failures:
        print('FAILURES:', ', '.join(failures))
        return 1
    print('preflight clean')
    return 0


if __name__ == '__main__':
    sys.exit(main())
