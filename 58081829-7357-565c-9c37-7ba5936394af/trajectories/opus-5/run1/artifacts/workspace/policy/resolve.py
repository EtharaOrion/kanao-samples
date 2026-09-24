"""Resolve the current signed authorization snapshot.

The complete candidate is validated before any projection is formed: a request
that is invalid anywhere yields an error and no result at all.
"""
import datetime
import hashlib
import json
import os
import sys

EVENT_KEYS = {'authorization', 'revision', 'subject', 'operation', 'start',
              'end', 'parameters'}
OPERATIONS = {'grant', 'withdraw'}
DEFAULT_MANIFESTS = '/workspace/policy/manifests.json'


class PolicyError(ValueError):
    """The candidate is not an admissible authorization history."""


def _text(value, what):
    if not isinstance(value, str) or not value:
        raise PolicyError('%s must be a nonempty string' % what)
    return value


def _date(value, what):
    _text(value, what)
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise PolicyError('%s is not an ISO YYYY-MM-DD date: %r' % (what, value))
    if len(value) != 10:
        raise PolicyError('%s is not an ISO YYYY-MM-DD date: %r' % (what, value))
    return value


def _whole(value, what, minimum):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise PolicyError('%s must be an integer >= %d' % (what, minimum))
    return value


def _digest(events):
    body = json.dumps(events, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _manifests():
    path = os.environ.get('MARROWFLEET_POLICY_MANIFESTS') or DEFAULT_MANIFESTS
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PolicyError('the release manifests could not be read: %s' % exc)
    generations = loaded.get('generations') if isinstance(loaded, dict) else None
    if not isinstance(generations, dict):
        raise PolicyError('the release manifests carry no generations')
    return generations


def _validate_event(event):
    if not isinstance(event, dict):
        raise PolicyError('an authorization event must be an object')
    if set(event) != EVENT_KEYS:
        raise PolicyError('event keys are exactly %s, not %s'
                          % (sorted(EVENT_KEYS), sorted(event)))
    _text(event['authorization'], 'authorization')
    _text(event['subject'], 'subject')
    revision = _text(event['revision'], 'revision')
    if not revision.isdigit() or not revision.isascii():
        raise PolicyError('revision is not a decimal string: %r' % revision)
    if revision[0] == '0' and revision != '0':
        raise PolicyError('revision is not canonical: %r' % revision)
    if event['operation'] not in OPERATIONS:
        raise PolicyError('operation must be grant or withdraw, not %r'
                          % (event['operation'],))
    parameters = event['parameters']
    if not isinstance(parameters, list) or not all(isinstance(x, str)
                                                   for x in parameters):
        raise PolicyError('parameters must be a list of strings')
    start = _date(event['start'], 'start')
    end = _date(event['end'], 'end')
    if not start < end:
        raise PolicyError('start must be strictly before end (%s, %s)'
                          % (start, end))


def _batches(transport):
    """Committed batches as {batch: events}, every transport rule enforced."""
    if not isinstance(transport, dict):
        raise PolicyError('transport must be an object')
    commits = transport.get('commits')
    pages = transport.get('pages')
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise PolicyError('transport carries a commits list and a pages list')

    committed = {}
    for commit in commits:
        if not isinstance(commit, dict):
            raise PolicyError('a commit must be an object')
        batch = _text(commit.get('batch'), 'commit batch')
        parts = _whole(commit.get('parts'), 'commit parts', 1)
        sha = _text(commit.get('sha256'), 'commit sha256')
        seen = committed.get(batch)
        if seen is not None and seen != (parts, sha):
            raise PolicyError('conflicting commits for batch %r' % batch)
        committed[batch] = (parts, sha)

    carried = {}
    for page in pages:
        if not isinstance(page, dict):
            raise PolicyError('a page must be an object')
        batch = _text(page.get('batch'), 'page batch')
        part = _whole(page.get('part'), 'page part', 0)
        events = page.get('events')
        if not isinstance(events, list):
            raise PolicyError('page events must be a list')
        if batch not in committed:
            raise PolicyError('page for uncommitted batch %r' % batch)
        if part >= committed[batch][0]:
            raise PolicyError('batch %r commits %d parts but carries part %d'
                              % (batch, committed[batch][0], part))
        seen = carried.get((batch, part))
        if seen is not None and seen != events:
            raise PolicyError('conflicting replay of batch %r part %d'
                              % (batch, part))
        carried[(batch, part)] = events

    history = {}
    for batch, (parts, sha) in committed.items():
        events = []
        for part in range(parts):
            if (batch, part) not in carried:
                raise PolicyError('batch %r is missing part %d' % (batch, part))
            events.extend(carried[(batch, part)])
        if _digest(events) != sha:
            raise PolicyError('batch %r does not match its committed digest'
                              % batch)
        history[batch] = events
    return history


def _check_generation(generation, history, commits):
    _text(generation, 'generation')
    generations = _manifests()
    if generation not in generations:
        raise PolicyError('generation %r is not named by the manifests'
                          % generation)
    entry = generations[generation]
    listed = entry.get('batches') if isinstance(entry, dict) else None
    if not isinstance(listed, dict):
        raise PolicyError('generation %r lists no batches' % generation)
    if set(listed) != set(history):
        raise PolicyError('generation %r commits the wrong batches (missing %s,'
                          ' unlisted %s)' % (generation,
                                             sorted(set(listed) - set(history)),
                                             sorted(set(history) - set(listed))))
    for batch, sha in commits.items():
        if listed[batch] != sha:
            raise PolicyError('batch %r is committed at a digest the manifest '
                              'for %r does not list' % (batch, generation))


def _history(request):
    if ('events' in request) == ('transport' in request):
        raise PolicyError('a request carries either events or a transport')
    if 'events' in request:
        events = request['events']
        if not isinstance(events, list):
            raise PolicyError('events must be a list')
        return list(events)
    transport = request['transport']
    batches = _batches(transport)
    if 'generation' in transport:
        commits = {c['batch']: c['sha256'] for c in transport['commits']}
        _check_generation(transport['generation'], batches, commits)
    events = []
    for batch in sorted(batches):
        events.extend(batches[batch])
    return events


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError('a request must be an object')
    now = _date(request.get('now'), 'now')
    subjects = request.get('subjects')
    if not isinstance(subjects, list):
        raise PolicyError('subjects must be a list')
    for subject in subjects:
        _text(subject, 'requested subject')

    events = _history(request)
    for event in events:
        _validate_event(event)

    revisions = {}
    for event in events:
        key = (event['authorization'], event['revision'])
        seen = revisions.get(key)
        if seen is not None and seen != event:
            raise PolicyError('conflicting events for authorization %s '
                              'revision %s' % key)
        revisions[key] = event

    winners = {}
    for (authorization, revision), event in revisions.items():
        current = winners.get(authorization)
        if current is None or int(revision) > int(current['revision']):
            winners[authorization] = event

    wanted = set(subjects)
    active = {}
    for authorization, event in sorted(winners.items()):
        if event['operation'] != 'grant':
            continue
        if not event['start'] <= now < event['end']:
            continue
        subject = event['subject']
        if subject not in wanted:
            continue
        if subject in active:
            raise PolicyError('subject %s carries more than one current '
                              'authorization (%s, %s)'
                              % (subject, active[subject][0], authorization))
        active[subject] = (authorization, event['parameters'])

    return {subject: (active[subject][1] if subject in active else None)
            for subject in subjects}


if __name__ == '__main__':
    try:
        decision = resolve(json.load(sys.stdin))
    except ValueError as exc:
        sys.stderr.write('resolve: %s\n' % exc)
        sys.exit(1)
    print(json.dumps(decision, sort_keys=True))
