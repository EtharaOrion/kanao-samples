#!/usr/bin/env python3
"""Checks for policy/resolve.py against docs/policy-reconciliation.md.

Run with `python3 policy/resolve_test.py`. Exits nonzero on the first failure.
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import resolve as R  # noqa: E402

RESOLVER = os.path.join(HERE, 'resolve.py')
FEED = os.path.join(HERE, 'authorizations.json')

FAILURES = []


def digest(events):
    return hashlib.sha256(json.dumps(
        events, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False).encode('utf-8')).hexdigest()


def event(authorization, revision, subject, operation='grant',
          start='2026-01-01', end='2100-01-01', parameters=('2042-B2', '430')):
    return {'authorization': authorization, 'revision': revision,
            'subject': subject, 'operation': operation, 'start': start,
            'end': end, 'parameters': list(parameters)}


def check(name, condition, detail=''):
    if condition:
        print('  ok   %s' % name)
    else:
        print('  FAIL %s %s' % (name, detail))
        FAILURES.append(name)


def accepts(name, request, expected):
    try:
        got = R.resolve(copy.deepcopy(request))
    except R.Invalid as exc:
        check(name, False, '-> rejected: %s' % exc)
        return
    check(name, got == expected, '-> %r, wanted %r' % (got, expected))


def rejects(name, request):
    try:
        got = R.resolve(copy.deepcopy(request))
    except R.Invalid:
        check(name, True)
        return
    check(name, False, '-> accepted with %r' % (got,))


def transport(batches, generation=None):
    """batches: {batch: [ [events of part 0], [events of part 1], ... ]}"""
    commits, pages = [], []
    for batch, parts in batches.items():
        flat = [e for part in parts for e in part]
        commits.append({'batch': batch, 'parts': len(parts),
                        'sha256': digest(flat)})
        for index, part in enumerate(parts):
            pages.append({'batch': batch, 'part': index, 'events': part})
    out = {'commits': commits, 'pages': pages}
    if generation is not None:
        out['generation'] = generation
    return out


def request(events=None, tr=None, subjects=('mf-3480', 'mf-6350'),
            now='2026-09-05'):
    out = {'now': now, 'subjects': list(subjects)}
    if tr is not None:
        out['transport'] = tr
    else:
        out['events'] = events or []
    return out


print('events-only projection')
accepts('a plain grant in window is credited',
        request([event('A', '1', 'mf-6350')]),
        {'mf-3480': None, 'mf-6350': ['2042-B2', '430']})
accepts('a higher revision replaces a lower one',
        request([event('A', '2', 'mf-6350', parameters=['2042-B2', '430']),
                 event('A', '10', 'mf-6350', parameters=['2043-B1', '500'])]),
        {'mf-3480': None, 'mf-6350': ['2043-B1', '500']})
accepts('revisions compare numerically, not as strings',
        request([event('A', '9', 'mf-6350', parameters=['nine']),
                 event('A', '10', 'mf-6350', parameters=['ten'])]),
        {'mf-3480': None, 'mf-6350': ['ten']})
accepts('a winning withdrawal contributes no decision',
        request([event('A', '1', 'mf-3480'),
                 event('A', '2', 'mf-3480', operation='withdraw')]),
        {'mf-3480': None, 'mf-6350': None})
accepts('a losing withdrawal does not suppress the winner',
        request([event('A', '2', 'mf-3480', operation='withdraw'),
                 event('A', '3', 'mf-3480', parameters=['x'])]),
        {'mf-3480': ['x'], 'mf-6350': None})
accepts('a correction moving subject does not survive on the former one',
        request([event('A', '1', 'mf-3480', parameters=['old']),
                 event('A', '2', 'mf-6350', parameters=['new'])]),
        {'mf-3480': None, 'mf-6350': ['new']})
accepts('a correction moving out of the window leaves nothing',
        request([event('A', '1', 'mf-3480', parameters=['old']),
                 event('A', '2', 'mf-3480', start='2027-01-01',
                       end='2028-01-01')]),
        {'mf-3480': None, 'mf-6350': None})
accepts('repeated identical revisions are idempotent',
        request([event('A', '1', 'mf-6350'), event('A', '1', 'mf-6350')]),
        {'mf-3480': None, 'mf-6350': ['2042-B2', '430']})
accepts('distinct authorizations on distinct subjects both stand',
        request([event('A', '1', 'mf-3480', parameters=['a']),
                 event('B', '1', 'mf-6350', parameters=['b'])]),
        {'mf-3480': ['a'], 'mf-6350': ['b']})
accepts('revision 0 is canonical',
        request([event('A', '0', 'mf-6350', parameters=['z'])]),
        {'mf-3480': None, 'mf-6350': ['z']})
accepts('unknown subjects are excluded from the projection',
        request([event('A', '1', 'mf-9999', parameters=['x'])]),
        {'mf-3480': None, 'mf-6350': None})
accepts('an empty history yields null for every requested subject',
        request([]), {'mf-3480': None, 'mf-6350': None})

print('the evaluation window is half open')
accepts('now == start is inside',
        request([event('A', '1', 'mf-6350', start='2026-09-05',
                       end='2026-09-06', parameters=['in'])]),
        {'mf-3480': None, 'mf-6350': ['in']})
accepts('now == end is outside',
        request([event('A', '1', 'mf-6350', start='2026-01-01',
                       end='2026-09-05', parameters=['out'])]),
        {'mf-3480': None, 'mf-6350': None})
accepts('now before start is outside',
        request([event('A', '1', 'mf-6350', start='2026-09-06',
                       end='2026-09-07')]),
        {'mf-3480': None, 'mf-6350': None})

print('histories that must be rejected')
rejects('conflicting equal revisions',
        request([event('A', '1', 'mf-6350', parameters=['one']),
                 event('A', '1', 'mf-6350', parameters=['two'])]))
rejects('conflicting equal revisions that do not win',
        request([event('A', '1', 'mf-6350', parameters=['one']),
                 event('A', '1', 'mf-6350', parameters=['two']),
                 event('A', '5', 'mf-6350', parameters=['five'])]))
rejects('two active authorizations on one subject',
        request([event('A', '1', 'mf-6350', parameters=['a']),
                 event('B', '1', 'mf-6350', parameters=['b'])]))
rejects('two active authorizations on an unrequested subject',
        request([event('A', '1', 'mf-9999', parameters=['a']),
                 event('B', '1', 'mf-9999', parameters=['b'])]))
rejects('a superseded record with a bad revision',
        request([event('A', '01', 'mf-6350'), event('A', '2', 'mf-6350')]))
rejects('a withdrawn record with a bad date',
        request([event('A', '1', 'mf-6350', operation='withdraw',
                       start='2026-1-1')]))
rejects('an out-of-window record with start == end',
        request([event('A', '1', 'mf-6350', start='2027-01-01',
                       end='2027-01-01')]))
rejects('start after end', request([event('A', '1', 'mf-6350',
                                          start='2027-01-01',
                                          end='2026-01-01')]))
rejects('an unrequested subject with an invalid record',
        request([event('A', '1', 'mf-9999', operation='suspend')]))
rejects('a nonempty subject is required',
        request([event('A', '1', '')]))
rejects('a nonempty authorization is required',
        request([event('', '1', 'mf-6350')]))
rejects('a negative revision', request([event('A', '-1', 'mf-6350')]))
rejects('an integer revision', request([event('A', 1, 'mf-6350')]))
rejects('parameters must be a list of strings',
        request([event('A', '1', 'mf-6350', parameters=[430])]))
rejects('a bad month', request([event('A', '1', 'mf-6350',
                                      start='2026-13-01')]))
rejects('a bad day', request([event('A', '1', 'mf-6350',
                                    start='2026-02-30')]))
extra = event('A', '1', 'mf-6350')
extra['note'] = 'x'
rejects('an extra event key', request([extra]))
short = event('A', '1', 'mf-6350')
del short['parameters']
rejects('a missing event key', request([short]))
rejects('neither transport nor events',
        {'now': '2026-09-05', 'subjects': ['mf-6350']})
rejects('both transport and events',
        dict(request([]), transport=transport({'b': [[]]})))
rejects('a bad now', request([], now='not-a-date'))
rejects('subjects is not a list of strings',
        {'now': '2026-09-05', 'subjects': ['mf-6350', 7], 'events': []})

print('segmented delivery without a generation (trial)')
accepts('parts concatenate in numeric order, not transport order',
        request(tr=transport({'b': [[event('A', '1', 'mf-6350',
                                           parameters=['first'])],
                                    [event('A', '2', 'mf-6350',
                                           parameters=['second'])]]})),
        {'mf-3480': None, 'mf-6350': ['second']})
accepts('all committed batches belong to the history',
        request(tr=transport({'1': [[event('A', '1', 'mf-3480',
                                           parameters=['a'])]],
                              '2': [[event('B', '1', 'mf-6350',
                                           parameters=['b'])]]})),
        {'mf-3480': ['a'], 'mf-6350': ['b']})
accepts('a later batch may withdraw an earlier grant',
        request(tr=transport({'2': [[event('A', '1', 'mf-3480')]],
                              '10': [[event('A', '9', 'mf-3480',
                                            operation='withdraw')]]})),
        {'mf-3480': None, 'mf-6350': None})

tr = transport({'b': [[event('A', '1', 'mf-6350')]]})
tr['pages'].append(dict(tr['pages'][0]))
accepts('an identical page replay is idempotent', request(tr=tr),
        {'mf-3480': None, 'mf-6350': ['2042-B2', '430']})

tr = transport({'b': [[event('A', '1', 'mf-6350')]]})
tr['commits'].append(dict(tr['commits'][0]))
accepts('an identical commit replay is idempotent', request(tr=tr),
        {'mf-3480': None, 'mf-6350': ['2042-B2', '430']})

tr = transport({'b': [[event('A', '1', 'mf-6350')]]})
tr['pages'].append({'batch': 'b', 'part': 0,
                    'events': [event('A', '1', 'mf-3480')]})
rejects('a conflicting page replay', request(tr=tr))

tr = transport({'b': [[event('A', '1', 'mf-6350')]]})
tr['commits'].append({'batch': 'b', 'parts': 2, 'sha256': 'x'})
rejects('a conflicting commit replay', request(tr=tr))

tr = transport({'b': [[event('A', '1', 'mf-6350')], []]})
tr['pages'] = [p for p in tr['pages'] if p['part'] == 0]
rejects('a missing part', request(tr=tr))

tr = transport({'b': [[event('A', '1', 'mf-6350')]]})
tr['pages'].append({'batch': 'b', 'part': 1, 'events': []})
rejects('an extra part', request(tr=tr))

tr = transport({'b': [[event('A', '1', 'mf-6350')]]})
tr['pages'].append({'batch': 'other', 'part': 0, 'events': []})
rejects('an uncommitted page', request(tr=tr))

tr = transport({'b': [[event('A', '1', 'mf-6350')]]})
tr['commits'][0]['sha256'] = '00' * 32
rejects('a mismatching digest', request(tr=tr))

tr = transport({'b': [[event('A', '1', 'mf-6350')]]})
tr['commits'][0]['parts'] = 0
rejects('parts must be positive', request(tr=tr))

tr = transport({'b': [[event('A', '1', 'mf-6350')]]})
tr['pages'][0]['part'] = -1
rejects('part must be zero based', request(tr=tr))

print('released delivery against the manifests')
evs = [event('A', '1', 'mf-6350', parameters=['manifested'])]
tr = transport({'b': [evs]}, generation='gen1')
manifests = {'generations': {'gen1': {'batches': {'b': digest(evs)}}}}
handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
json.dump(manifests, handle)
handle.close()
os.environ[R.MANIFESTS_ENV] = handle.name
accepts('a generation that matches its manifest', request(tr=tr),
        {'mf-3480': None, 'mf-6350': ['manifested']})
rejects('a generation the manifest does not name',
        request(tr=transport({'b': [evs]}, generation='gen9')))

short_tr = transport({'b': [evs], 'extra': [[]]}, generation='gen1')
rejects('a batch the manifest does not list', request(tr=short_tr))

wide = {'generations': {'gen1': {'batches': {'b': digest(evs),
                                             'absent': digest([])}}}}
handle2 = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
json.dump(wide, handle2)
handle2.close()
os.environ[R.MANIFESTS_ENV] = handle2.name
rejects('a batch the manifest lists that is not committed',
        request(tr=transport({'b': [evs]}, generation='gen1')))

skew = {'generations': {'gen1': {'batches': {'b': '11' * 32}}}}
handle3 = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
json.dump(skew, handle3)
handle3.close()
os.environ[R.MANIFESTS_ENV] = handle3.name
rejects('a committed digest that differs from the manifest',
        request(tr=transport({'b': [evs]}, generation='gen1')))

os.environ[R.MANIFESTS_ENV] = '/nonexistent/manifests.json'
rejects('unreadable manifests when a generation is named',
        request(tr=transport({'b': [evs]}, generation='gen1')))
accepts('a trial delivery needs no manifests',
        request(tr=transport({'b': [evs]})),
        {'mf-3480': None, 'mf-6350': ['manifested']})
del os.environ[R.MANIFESTS_ENV]
for temp in (handle.name, handle2.name, handle3.name):
    os.unlink(temp)

print('the installed feed')
feed = json.load(open(FEED))
outcomes = {}
for candidate in feed['candidates']:
    run = subprocess.run([sys.executable, RESOLVER],
                         input=json.dumps(candidate['request']),
                         text=True, capture_output=True)
    outcomes[candidate['id']] = (run.returncode, run.stdout.strip())
check('close-19 is rejected (manifest lists batch 10, uncommitted)',
      outcomes['close-19'][0] != 0, outcomes['close-19'])
check('close-19 produces no partial result',
      outcomes['close-19'][1] == '', outcomes['close-19'])
check('close-18 is rejected (AUTH-1 revision 2 conflicts)',
      outcomes['close-18'][0] != 0, outcomes['close-18'])
check('close-18 produces no partial result',
      outcomes['close-18'][1] == '', outcomes['close-18'])
check('close-17 is accepted', outcomes['close-17'][0] == 0,
      outcomes['close-17'])
check('close-17 lifts mf-3480 and fixes mf-6350 at 2042-B2/430',
      outcomes['close-17'][1] and json.loads(outcomes['close-17'][1]) ==
      {'mf-3480': None, 'mf-6350': ['2042-B2', '430']},
      outcomes['close-17'])

run = subprocess.run([sys.executable, RESOLVER],
                     input=json.dumps(feed['candidates'][2]['request']),
                     text=True, capture_output=True)
check('the accepted candidate replays identically',
      run.stdout.strip() == outcomes['close-17'][1], run.stdout)

print()
if FAILURES:
    print('%d check(s) failed: %s' % (len(FAILURES), ', '.join(FAILURES)))
    sys.exit(1)
print('all checks passed')
