"""Contract tests for /workspace/policy/resolve.py.

Runs the resolver exactly the way policy-apply does (python3 resolve.py, JSON on
stdin, decisions on stdout) so nothing is mocked. Covers every rule in
docs/policy-reconciliation.md plus the three live candidates.
"""
import copy
import hashlib
import json
import subprocess
import sys

RESOLVE = '/workspace/policy/resolve.py'
FEED = '/workspace/policy/authorizations.json'
sys.path.insert(0, '/workspace/policy')
import resolve as resolver  # noqa: E402


def run(request, env_manifests=None):
    import os
    env = dict(os.environ)
    if env_manifests is None:
        env.pop('MARROWFLEET_POLICY_MANIFESTS', None)
    else:
        env['MARROWFLEET_POLICY_MANIFESTS'] = env_manifests
    return subprocess.run([sys.executable, RESOLVE], input=json.dumps(request),
                          text=True, capture_output=True, env=env)


def digest(events):
    return hashlib.sha256(json.dumps(events, sort_keys=True,
                                     separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def grant(auth, rev, subject, params, start='2026-01-01', end='2100-01-01'):
    return {'authorization': auth, 'revision': rev, 'subject': subject,
            'operation': 'grant', 'start': start, 'end': end,
            'parameters': params}


def ev(events, subjects=('mf-3480', 'mf-6350'), now='2026-09-05'):
    return {'now': now, 'subjects': list(subjects), 'events': events}


FAILURES = []


def expect(label, condition, detail=''):
    if condition:
        print('  ok   %s' % label)
    else:
        print('  FAIL %s %s' % (label, detail))
        FAILURES.append(label)


def expect_exit(label, request, code, env_manifests=None):
    r = run(request, env_manifests)
    expect(label, r.returncode == code,
           'exit %d stderr=%r' % (r.returncode, r.stderr.strip()[:120]))
    return r


print('== live feed candidates (real feed, real manifests) ==')
feed = json.load(open(FEED))
for candidate in feed['candidates']:
    r = run(candidate['request'])
    if candidate['id'] == 'close-19':
        expect('close-19 rejected: g19 omits committed batch 10',
               r.returncode != 0, r.stdout.strip())
        expect('close-19 prints no partial result', not r.stdout.strip())
    elif candidate['id'] == 'close-18':
        expect('close-18 rejected: AUTH-1 revision 2 carried twice, differently',
               r.returncode != 0, r.stdout.strip())
        expect('close-18 prints no partial result', not r.stdout.strip())
    else:
        expect('close-17 accepted', r.returncode == 0, r.stderr.strip())
        if r.returncode == 0:
            expect('close-17 lifts mf-3480 (winning AUTH-1 is a withdrawal)',
                   json.loads(r.stdout) == {'mf-3480': None,
                                            'mf-6350': ['2042-B2', '430']},
                   r.stdout.strip())

print('== events-only semantics ==')
expect_exit('events-only still supported',
            ev([grant('A', '1', 'mf-3480', ['x', '1'])]), 0)
r = run(ev([grant('A', '1', 'mf-3480', ['x', '1'])]))
expect('events-only projection', json.loads(r.stdout)['mf-3480'] == ['x', '1'])
r = run(ev([grant('A', '1', 'mf-3480', ['x', '1'])],
           subjects=['mf-3480', 'nobody']))
expect('subject with no active authorization maps to null',
       json.loads(r.stdout) == {'mf-3480': ['x', '1'], 'nobody': None})
r = run(ev([grant('A', '1', 'mf-3480', ['x', '1'])],
           subjects=['mf-3480', 'zz-unrequested']))
expect('result keys are the requested subjects',
       sorted(json.loads(r.stdout)) == ['mf-3480', 'zz-unrequested'])

print('== revision and correction rules ==')
r = run(ev([grant('A', '2', 'mf-3480', ['old']),
            grant('A', '10', 'mf-3480', ['new'])]))
expect('higher revision fully replaces lower', json.loads(r.stdout)['mf-3480'] == ['new'])
r = run(ev([grant('A', '10', 'mf-3480', ['new']),
            grant('A', '2', 'mf-3480', ['old'])]))
expect('delivery order does not matter', json.loads(r.stdout)['mf-3480'] == ['new'])
r = run(ev([grant('A', '10', 'mf-3480', ['a']), grant('A', '10', 'mf-3480', ['a'])]))
expect('repeated identical revision is idempotent', r.returncode == 0, r.stderr)
r = run(ev([grant('A', '10', 'mf-3480', ['new']),
            grant('A', '2', 'mf-3480', ['old'])]))
expect('delivery order does not matter', json.loads(r.stdout)['mf-3480'] == ['new'])
r = run(ev([grant('A', '10', 'mf-3480', ['a']), grant('A', '10', 'mf-3480', ['a'])]))
expect('repeated identical revision is idempotent', r.returncode == 0, r.stderr)
expect_exit('conflicting equal revisions are invalid',
            ev([grant('A', '10', 'mf-3480', ['a']),
                grant('A', '10', 'mf-3480', ['b'])]), 1)
expect_exit('conflicting equal revisions are invalid even when superseded',
            ev([grant('A', '10', 'mf-3480', ['a']),
                grant('A', '2', 'mf-3480', ['old']),
                grant('A', '2', 'mf-3480', ['different'])]), 1)
r = run(ev([grant('A', '2', 'mf-3480', ['me']),
            grant('A', '10', 'mf-6350', ['moved'])]))
expect('a correction may move to another subject',
       json.loads(r.stdout) == {'mf-3480': None, 'mf-6350': ['moved']}, r.stdout)
r = run(ev([grant('A', '2', 'mf-3480', ['me']),
            dict(grant('A', '10', 'mf-3480', ['me']), operation='withdraw')]))
expect('a winning withdrawal contributes no decision',
       json.loads(r.stdout) == {'mf-3480': None, 'mf-6350': None}, r.stdout)

print('== window rules ==')
r = run(ev([grant('A', '1', 'mf-3480', ['y'], start='2026-01-01',
                  end='2026-09-05')]))
expect('start <= now < end: end is exclusive', json.loads(r.stdout)['mf-3480'] is None)
r = run(ev([grant('A', '1', 'mf-3480', ['y'], start='2026-09-05',
                  end='2100-01-01')]))
expect('start is inclusive', json.loads(r.stdout)['mf-3480'] == ['y'])
r = run(ev([grant('A', '1', 'mf-3480', ['y'])], now='2026-09-05'))
expect('evaluation instant is fixed by the request', r.returncode == 0)
expect_exit('multiple active authorizations for one subject is an error',
            ev([grant('A', '1', 'mf-3480', ['y']), grant('B', '7', 'mf-3480', ['z'])]),
            1)

print('== complete validation, including unrequested subjects ==')
expect_exit('superseded event that is invalid still fails',
            ev([grant('A', '1', 'mf-3480', ['y'], start='2027-01-01',
                      end='2026-01-01'),
                grant('A', '2', 'mf-3480', ['z'])]), 1)
expect_exit('withdrawn event that is invalid still fails',
            ev([dict(grant('A', '1', 'mf-3480', ['y']), operation='withdraw',
                     start='x', end='2100-01-01')]), 1)
expect_exit('unrequested-subject event that is invalid still fails',
            ev([grant('A', '1', 'zz-other', ['y'], start='2030-01-01',
                      end='2030-01-01')]), 1)
expect_exit('out-of-window event that is invalid still fails',
            ev([grant('A', '1', 'mf-3480', ['y'], start='2000-01-01',
                      end='2001-01-01', ) if False else
                dict(grant('A', '1', 'mf-3480', ['y'], start='2000-01-01',
                           end='2001-01-01'), revision='01')]), 1)
expect_exit('operation outside the vocabulary fails',
            ev([dict(grant('A', '1', 'mf-3480', ['y']), operation='suspend')]), 1)
expect_exit('extra event key fails',
            ev([dict(grant('A', '1', 'mf-3480', ['y']), note='hi')]), 1)
expect_exit('missing event key fails',
            ev([{k: v for k, v in grant('A', '1', 'mf-3480', ['y']).items()
                 if k != 'end'}]), 1)
expect_exit('non-string parameter fails',
            ev([{'authorization': 'A', 'revision': '1', 'subject': 'mf-3480',
                 'operation': 'grant', 'start': '2026-01-01',
                 'end': '2100-01-01', 'parameters': [7]}]), 1)
expect_exit('leading-zero revision fails',
            ev([grant('A', '01', 'mf-3480', ['y'])]), 1)
expect_exit('equal start and end fail',
            ev([grant('A', '1', 'mf-3480', ['y'], end='2026-01-01')]), 1)
expect_exit('impossible calendar date fails',
            ev([grant('A', '1', 'mf-3480', ['y'], start='2026-02-30',
                      end='2026-03-01')]), 1)

print('== segmented delivery ==')
e1 = [grant('A', '1', 'mf-3480', ['half-one'])]
e2 = [grant('B', '1', 'mf-6350', ['half-two'])]
both = e1 + e2
transport = {'commits': [{'batch': 'b', 'parts': 2, 'sha256': digest(both)}],
             'pages': [{'batch': 'b', 'part': 0, 'events': e1},
                       {'batch': 'b', 'part': 1, 'events': e2}]}
r = run({'now': '2026-09-05', 'subjects': ['mf-3480', 'mf-6350'],
         'transport': transport})
expect('parts concatenate in numeric part order, not arrival order',
       json.loads(r.stdout) == {'mf-3480': ['half-one'],
                                'mf-6350': ['half-two']}, r.stdout + r.stderr)

shuffled = {'commits': transport['commits'],
            'pages': [transport['pages'][1], transport['pages'][0]]}
r = run({'now': '2026-09-05', 'subjects': ['mf-3480', 'mf-6350'],
         'transport': shuffled})
expect('transport order of pages does not matter',
       json.loads(r.stdout) == {'mf-3480': ['half-one'],
                                'mf-6350': ['half-two']}, r.stdout + r.stderr)

expect_exit('missing part fails',
            {'now': '2026-09-05', 'subjects': ['mf-3480'],
             'transport': {'commits': [{'batch': 'b', 'parts': 2,
                                        'sha256': digest(both)}],
                           'pages': [{'batch': 'b', 'part': 0, 'events': e1}]}}, 1)
expect_exit('extra part fails',
            {'now': '2026-09-05', 'subjects': ['mf-3480'],
             'transport': {'commits': [{'batch': 'b', 'parts': 1,
                                        'sha256': digest(e1)}],
                           'pages': [{'batch': 'b', 'part': 0, 'events': e1},
                                     {'batch': 'b', 'part': 1, 'events': e2}]}}, 1)
expect_exit('uncommitted page fails',
            {'now': '2026-09-05', 'subjects': ['mf-3480'],
             'transport': {'commits': [{'batch': 'b', 'parts': 1,
                                        'sha256': digest(e1)}],
                           'pages': [{'batch': 'b', 'part': 0, 'events': e1},
                                     {'batch': 'c', 'part': 0, 'events': e2}]}}, 1)
bad = copy.deepcopy(transport)
bad['commits'][0]['sha256'] = '0' * 64
expect_exit('mismatching digest fails',
            {'now': '2026-09-05', 'subjects': ['mf-3480'], 'transport': bad}, 1)
replay = copy.deepcopy(transport)
replay['pages'].append(copy.deepcopy(transport['pages'][0]))
r = run({'now': '2026-09-05', 'subjects': ['mf-3480'], 'transport': replay})
expect('identical page replay is idempotent', r.returncode == 0, r.stderr)
conflict = copy.deepcopy(transport)
conflict['pages'].append({'batch': 'b', 'part': 0,
                          'events': [grant('A', '1', 'mf-3480', ['other'])]})
expect_exit('conflicting page replay fails',
            {'now': '2026-09-05', 'subjects': ['mf-3480'], 'transport': conflict}, 1)
commit_replay = copy.deepcopy(transport)
commit_replay['commits'].append(copy.deepcopy(transport['commits'][0]))
r = run({'now': '2026-09-05', 'subjects': ['mf-3480'], 'transport': commit_replay})
expect('identical commit replay is idempotent', r.returncode == 0, r.stderr)
commit_conflict = copy.deepcopy(transport)
commit_conflict['commits'].append({'batch': 'b', 'parts': 2,
                                   'sha256': digest(digest(e1) and e2)})
expect_exit('conflicting commit replay fails',
            {'now': '2026-09-05', 'subjects': ['mf-3480'],
             'transport': commit_conflict}, 1)
expect_exit('events inside a transport are validated too',
            {'now': '2026-09-05', 'subjects': ['mf-3480'],
             'transport': {'commits': [{'batch': 'b', 'parts': 1,
                                        'sha256': digest([grant('A', '01',
                                                           'mf-3480', ['y'])])}],
                           'pages': [{'batch': 'b', 'part': 0,
                                      'events': [grant('A', '01', 'mf-3480',
                                                       ['y'])]}]}}, 1)

print('== manifest enforcement ==')
manifest_file = '/tmp/test-manifests.json'
json.dump({'generations': {'g-test': {'batches': {'b': digest(both), 'c':
                                                  digest(e2)}}}},
          open(manifest_file, 'w'))
ok = copy.deepcopy(transport)
ok['commits'].append({'batch': 'c', 'parts': 1, 'sha256': digest(e2)})
ok['pages'].append({'batch': 'c', 'part': 0, 'events': e2})
ok['generation'] = 'g-test'
r = run({'now': '2026-09-05', 'subjects': ['mf-3480', 'mf-6350'],
         'transport': ok}, env_manifests=manifest_file)
expect('a transport matching its generation is accepted',
       r.returncode == 0 and json.loads(r.stdout) ==
       {'mf-3480': ['half-one'], 'mf-6350': ['half-two']},
       r.stdout + r.stderr)

short = copy.deepcopy(transport)
short['generation'] = 'g-test'
expect_exit('a manifest batch the transport does not commit fails',
            {'now': '2026-09-05', 'subjects': ['mf-3480'], 'transport': short},
            1, env_manifests=manifest_file)
extra = copy.deepcopy(ok)
extra['commits'].append({'batch': 'd', 'parts': 1, 'sha256': digest(e1)})
extra['pages'].append({'batch': 'd', 'part': 0, 'events': e1})
expect_exit('a batch the manifest does not list fails',
            {'now': '2026-09-05', 'subjects': ['mf-3480'], 'transport': extra},
            1, env_manifests=manifest_file)
wrong_digest = copy.deepcopy(ok)
wrong_digest['commits'][0]['sha256'] = digest(digest(e1) and e2) if False else \
    digest([grant('A', '1', 'mf-3480', ['tampered'])])
expect_exit('a committed digest that differs from the manifest fails',
            {'now': '2026-09-05', 'subjects': ['mf-3480'],
             'transport': wrong_digest}, 1, env_manifests=manifest_file)
unknown = copy.deepcopy(transport)
unknown['generation'] = 'g-nope'
expect_exit('a generation the manifest does not name fails',
            {'now': '2026-09-05', 'subjects': ['mf-3480'], 'transport': unknown},
            1, env_manifests=manifest_file)
r = run({'now': '2026-09-05', 'subjects': ['mf-3480'], 'transport': unknown},
        env_manifests='/tmp/does-not-exist.json')
expect('manifest location honours MARROWFLEET_POLICY_MANIFESTS',
       r.returncode == 1 and '/tmp/does-not-exist.json' in r.stderr,
       'exit %d %r' % (r.returncode, r.stderr.strip()[:120]))
trial = copy.deepcopy(transport)
r = run({'now': '2026-09-05', 'subjects': ['mf-3480'], 'transport': trial})
expect('a transport naming no generation is an unreleased trial',
       r.returncode == 0, r.stderr)

print('== no partial results on invalid input ==')
r = run(ev([grant('A', '1', 'mf-3480', ['y']), grant('B', '9', 'mf-3480', ['z'])]))
expect('an error mid-projection leaves nothing printed', r.stdout == '')

print()
if FAILURES:
    print('%d FAILURES: %s' % (len(FAILURES), FAILURES))
    sys.exit(1)
print('all contract checks passed')
