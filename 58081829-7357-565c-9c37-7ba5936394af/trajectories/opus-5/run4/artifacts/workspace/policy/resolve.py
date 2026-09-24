"""Resolve the current signed authorization snapshot.

Contract: docs/policy-reconciliation.md. A request carries either an ``events``
list or a segmented ``transport`` delivery. The complete history is validated
before anything is projected; an invalid history raises and yields no result.
"""
import hashlib
import json
import os
import re
import sys

EVENT_KEYS = {'authorization', 'revision', 'subject', 'operation', 'start',
              'end', 'parameters'}
REVISION = re.compile(r'\A(?:0|[1-9][0-9]*)\Z')
DATE = re.compile(r'\A([0-9]{4})-([0-9]{2})-([0-9]{2})\Z')
DEFAULT_MANIFESTS = '/workspace/policy/manifests.json'
MONTH_LENGTHS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


class PolicyError(Exception):
    """The delivery, or the authorization history it carries, is not admissible."""


def _text(value, what):
    if not isinstance(value, str) or not value:
        raise PolicyError('%s must be a nonempty string' % what)
    return value


def _date(value, what):
    match = DATE.match(value) if isinstance(value, str) else None
    if match is None:
        raise PolicyError('%s must be an ISO YYYY-MM-DD date' % what)
    year, month, day = (int(part) for part in match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= MONTH_LENGTHS[month - 1]:
        raise PolicyError('%s is not a calendar date' % what)
    if month == 2 and day == 29 and not (
            year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        raise PolicyError('%s is not a calendar date' % what)
    return value


def _digest(events):
    body = json.dumps(events, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _manifests():
    path = os.environ.get('MARROWFLEET_POLICY_MANIFESTS') or DEFAULT_MANIFESTS
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PolicyError('release manifests are unreadable: %s' % exc)
    generations = document.get('generations') if isinstance(document, dict) else None
    if not isinstance(generations, dict):
        raise PolicyError('release manifests carry no generations')
    return generations


def _commits(transport):
    raw = transport.get('commits')
    if not isinstance(raw, list):
        raise PolicyError('transport carries no commits')
    commits = {}
    for commit in raw:
        if not isinstance(commit, dict) or set(commit) != {'batch', 'parts', 'sha256'}:
            raise PolicyError('a commit carries exactly batch, parts and sha256')
        batch = _text(commit.get('batch'), 'commit batch')
        parts = commit.get('parts')
        if not isinstance(parts, int) or isinstance(parts, bool) or parts <= 0:
            raise PolicyError('commit %s: parts must be a positive integer' % batch)
        sha256 = _text(commit.get('sha256'), 'commit sha256')
        held = commits.get(batch)
        if held is not None and held != (parts, sha256):
            raise PolicyError('commit %s replayed with conflicting terms' % batch)
        commits[batch] = (parts, sha256)
    if not commits:
        raise PolicyError('transport commits no batch')
    return commits


def _pages(transport, commits):
    raw = transport.get('pages')
    if not isinstance(raw, list):
        raise PolicyError('transport carries no pages')
    pages = {}
    for page in raw:
        if not isinstance(page, dict) or set(page) != {'batch', 'part', 'events'}:
            raise PolicyError('a page carries exactly batch, part and events')
        batch = _text(page.get('batch'), 'page batch')
        part = page.get('part')
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            raise PolicyError('page %s: part must be a zero-based integer' % batch)
        events = page.get('events')
        if not isinstance(events, list):
            raise PolicyError('page %s/%d carries no event list' % (batch, part))
        if batch not in commits:
            raise PolicyError('page %s/%d belongs to an uncommitted batch'
                              % (batch, part))
        if part >= commits[batch][0]:
            raise PolicyError('page %s/%d lies beyond the committed part count'
                              % (batch, part))
        held = pages.get((batch, part))
        if held is not None and held != events:
            raise PolicyError('page %s/%d replayed with conflicting events'
                              % (batch, part))
        pages[(batch, part)] = events
    return pages


def _generation(transport, commits):
    generation = transport.get('generation')
    if generation is None:
        return
    generation = _text(generation, 'transport generation')
    generations = _manifests()
    if generation not in generations:
        raise PolicyError('generation %s is not a released generation' % generation)
    listed = generations[generation]
    listed = listed.get('batches') if isinstance(listed, dict) else None
    if not isinstance(listed, dict):
        raise PolicyError('generation %s lists no batches' % generation)
    committed = {batch: sha256 for batch, (_, sha256) in commits.items()}
    if committed != listed:
        raise PolicyError('generation %s must commit exactly the batches its '
                          'manifest lists, at the digests it lists' % generation)


def _assemble(transport):
    commits = _commits(transport)
    pages = _pages(transport, commits)
    _generation(transport, commits)
    history = []
    for batch in sorted(commits):
        parts, sha256 = commits[batch]
        events = []
        for part in range(parts):
            if (batch, part) not in pages:
                raise PolicyError('batch %s is missing part %d' % (batch, part))
            events.extend(pages[(batch, part)])
        if _digest(events) != sha256.lower():
            raise PolicyError('batch %s does not match its committed digest' % batch)
        history.extend(events)
    return history


def _history(request):
    if 'transport' in request:
        if 'events' in request:
            raise PolicyError('a request carries a transport or events, not both')
        transport = request['transport']
        if not isinstance(transport, dict):
            raise PolicyError('transport must be an object')
        return _assemble(transport)
    events = request.get('events')
    if not isinstance(events, list):
        raise PolicyError('a request must carry events or a transport')
    return events


def _check(event):
    if not isinstance(event, dict) or set(event) != EVENT_KEYS:
        raise PolicyError('an event carries exactly %s'
                          % ', '.join(sorted(EVENT_KEYS)))
    _text(event['authorization'], 'authorization')
    _text(event['subject'], 'subject')
    revision = event['revision']
    if not isinstance(revision, str) or not REVISION.match(revision):
        raise PolicyError('revision must be a canonical nonnegative decimal string')
    if event['operation'] not in ('grant', 'withdraw'):
        raise PolicyError('operation must be grant or withdraw')
    parameters = event['parameters']
    if not isinstance(parameters, list) or not all(
            isinstance(item, str) for item in parameters):
        raise PolicyError('parameters must be a list of strings')
    if not _date(event['start'], 'start') < _date(event['end'], 'end'):
        raise PolicyError('start must fall strictly before end')


def _snapshot(history):
    """One winning revision per authorization, subject and interval included."""
    revisions = {}
    for event in history:
        name = event['authorization']
        revision = int(event['revision'])
        held = revisions.get((name, revision))
        if held is not None and held != event:
            raise PolicyError('authorization %s carries conflicting revision %s'
                              % (name, event['revision']))
        revisions[(name, revision)] = event
    winners = {}
    for (name, revision), event in revisions.items():
        if name not in winners or revision > winners[name][0]:
            winners[name] = (revision, event)
    return [event for _, event in winners.values()]


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError('a request must be an object')
    now = _date(request.get('now'), 'now')
    subjects = request.get('subjects')
    if not isinstance(subjects, list) or not all(
            isinstance(item, str) and item for item in subjects):
        raise PolicyError('subjects must be a list of nonempty strings')

    history = _history(request)
    for event in history:
        _check(event)

    requested = set(subjects)
    active = {}
    for event in _snapshot(history):
        subject = event['subject']
        if subject not in requested or event['operation'] == 'withdraw':
            continue
        if not event['start'] <= now < event['end']:
            continue
        if subject in active:
            raise PolicyError('subject %s carries more than one active '
                              'authorization' % subject)
        active[subject] = event

    return {subject: active[subject]['parameters'] if subject in active else None
            for subject in subjects}


if __name__ == '__main__':
    try:
        decision = resolve(json.load(sys.stdin))
    except PolicyError as error:
        sys.stderr.write('resolve: %s\n' % error)
        sys.exit(1)
    except ValueError as error:
        sys.stderr.write('resolve: the request is not JSON: %s\n' % error)
        sys.exit(1)
    print(json.dumps(decision, sort_keys=True))
