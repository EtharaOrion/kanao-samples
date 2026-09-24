"""Resolve the current signed authorization snapshot.

The complete candidate is validated before anything is projected: an invalid
history raises, so the controller rejects that candidate and carries on with the
next one.
"""
import hashlib
import json
import os
import sys

DEFAULT_MANIFESTS = '/workspace/policy/manifests.json'
EVENT_KEYS = {'authorization', 'revision', 'subject', 'operation', 'start',
              'end', 'parameters'}
DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


class PolicyError(ValueError):
    """An authorization history that must not be applied."""


def _text(value, what):
    if not isinstance(value, str) or not value:
        raise PolicyError('%s must be a nonempty string' % what)
    return value


def _whole(value, what, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PolicyError('%s must be an integer >= %d' % (what, minimum))
    return value


def _day(value, what):
    _text(value, what)
    fields = value.split('-')
    if [len(f) for f in fields] != [4, 2, 2]:
        raise PolicyError('%s must be an ISO YYYY-MM-DD date' % what)
    if not all(f.isascii() and f.isdigit() for f in fields):
        raise PolicyError('%s must be an ISO YYYY-MM-DD date' % what)
    year, month, day = (int(f) for f in fields)
    if not 1 <= month <= 12 or day < 1:
        raise PolicyError('%s is not a calendar date' % what)
    last = DAYS[month - 1]
    if month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        last = 29
    if day > last:
        raise PolicyError('%s is not a calendar date' % what)
    return value


def _revision(value):
    _text(value, 'revision')
    if not (value.isascii() and value.isdigit()) or value != str(int(value)):
        raise PolicyError('revision must be a canonical nonnegative decimal '
                          'string')
    return int(value)


def _check_event(event):
    if not isinstance(event, dict):
        raise PolicyError('an event must be an object')
    if set(event) != EVENT_KEYS:
        raise PolicyError('event keys must be exactly %s'
                          % ', '.join(sorted(EVENT_KEYS)))
    _text(event['authorization'], 'authorization')
    _text(event['subject'], 'subject')
    _revision(event['revision'])
    if event['operation'] not in ('grant', 'withdraw'):
        raise PolicyError('operation must be grant or withdraw')
    if not _day(event['start'], 'start') < _day(event['end'], 'end'):
        raise PolicyError('start must be strictly before end')
    if not isinstance(event['parameters'], list) or not all(
            isinstance(p, str) for p in event['parameters']):
        raise PolicyError('parameters must be a list of strings')


def _canonical(events):
    return json.dumps(events, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def _generations():
    path = os.environ.get('MARROWFLEET_POLICY_MANIFESTS') or DEFAULT_MANIFESTS
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PolicyError('the release manifests are unreadable: %s' % exc)
    generations = document.get('generations') if isinstance(document, dict) \
        else None
    if not isinstance(generations, dict):
        raise PolicyError('the release manifests name no generations')
    return generations


def _check_generation(generation, committed):
    _text(generation, 'generation')
    generations = _generations()
    if generation not in generations:
        raise PolicyError('the manifests do not name generation %s'
                          % generation)
    record = generations[generation]
    listed = record.get('batches') if isinstance(record, dict) else None
    if not isinstance(listed, dict):
        raise PolicyError('generation %s lists no batches' % generation)
    if set(listed) != set(committed):
        raise PolicyError('generation %s does not commit exactly the batches '
                          'its manifest lists' % generation)
    for batch, digest in listed.items():
        if not isinstance(digest, str) or committed[batch] != digest.lower():
            raise PolicyError('batch %s is not committed at the digest '
                              'generation %s lists for it'
                              % (batch, generation))


def _history(transport):
    if not isinstance(transport, dict):
        raise PolicyError('transport must be an object')
    commits = transport.get('commits')
    pages = transport.get('pages')
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise PolicyError('a transport carries commits and pages')

    committed = {}
    parts_of = {}
    for commit in commits:
        if not isinstance(commit, dict):
            raise PolicyError('a commit must be an object')
        batch = _text(commit.get('batch'), 'batch')
        parts = _whole(commit.get('parts'), 'parts', 1)
        digest = _text(commit.get('sha256'), 'sha256').lower()
        if len(digest) != 64 or digest.strip('0123456789abcdef'):
            raise PolicyError('sha256 must be a hex digest')
        if batch in committed and (committed[batch], parts_of[batch]) \
                != (digest, parts):
            raise PolicyError('batch %s is committed twice, differently'
                              % batch)
        committed[batch] = digest
        parts_of[batch] = parts

    carried = {}
    for page in pages:
        if not isinstance(page, dict):
            raise PolicyError('a page must be an object')
        batch = _text(page.get('batch'), 'batch')
        part = _whole(page.get('part'), 'part', 0)
        events = page.get('events')
        if not isinstance(events, list):
            raise PolicyError('a page carries a list of events')
        for event in events:
            _check_event(event)
        if batch not in committed:
            raise PolicyError('batch %s delivers pages without a commit'
                              % batch)
        if part >= parts_of[batch]:
            raise PolicyError('batch %s delivers part %d, which its commit '
                              'does not declare' % (batch, part))
        held = carried.setdefault(batch, {})
        if part in held:
            if _canonical(held[part]) != _canonical(events):
                raise PolicyError('batch %s part %d is delivered twice, '
                                  'differently' % (batch, part))
        else:
            held[part] = events

    if 'generation' in transport:
        _check_generation(transport['generation'], committed)

    history = []
    for batch in sorted(committed):
        assembled = []
        for part in range(parts_of[batch]):
            if part not in carried.get(batch, {}):
                raise PolicyError('batch %s is missing part %d'
                                  % (batch, part))
            assembled.extend(carried[batch][part])
        if hashlib.sha256(_canonical(assembled)).hexdigest() != committed[batch]:
            raise PolicyError('batch %s does not match its committed digest'
                              % batch)
        history.extend(assembled)
    return history


def _snapshot(events, now):
    revisions = {}
    for event in events:
        by_revision = revisions.setdefault(event['authorization'], {})
        revision = int(event['revision'])
        if revision in by_revision:
            if by_revision[revision] != event:
                raise PolicyError('authorization %s carries conflicting '
                                  'copies of revision %d'
                                  % (event['authorization'], revision))
        else:
            by_revision[revision] = event

    active = {}
    for authorization in sorted(revisions):
        by_revision = revisions[authorization]
        winner = by_revision[max(by_revision)]
        if winner['operation'] != 'grant':
            continue
        if not winner['start'] <= now < winner['end']:
            continue
        subject = winner['subject']
        if subject in active:
            raise PolicyError('subject %s carries more than one active '
                              'authorization' % subject)
        active[subject] = list(winner['parameters'])
    return active


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError('a request must be an object')
    now = _day(request.get('now'), 'now')
    subjects = request.get('subjects')
    if not isinstance(subjects, list):
        raise PolicyError('subjects must be a list')
    for subject in subjects:
        _text(subject, 'subject')

    if ('events' in request) == ('transport' in request):
        raise PolicyError('a request carries either events or a transport')

    if 'transport' in request:
        events = _history(request['transport'])
    else:
        events = request['events']
        if not isinstance(events, list):
            raise PolicyError('events must be a list')
        for event in events:
            _check_event(event)

    active = _snapshot(events, now)
    return {subject: active.get(subject) for subject in subjects}


if __name__ == '__main__':
    try:
        decision = resolve(json.load(sys.stdin))
    except ValueError as exc:
        sys.stderr.write('policy: %s\n' % exc)
        sys.exit(1)
    print(json.dumps(decision, sort_keys=True))
