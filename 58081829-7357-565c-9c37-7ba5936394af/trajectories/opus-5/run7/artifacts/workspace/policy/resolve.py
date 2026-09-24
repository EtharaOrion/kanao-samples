"""Resolve the current signed authorization snapshot.

Reads a resolver request (JSON on stdin, or via resolve(request)) and returns
the winning parameters for every requested subject, or null where no current
authorization applies. The complete authorization history is validated before
any projection is formed; an invalid history raises, and the CLI exits nonzero
without printing a partial result.
"""
import hashlib
import json
import os
import re
import sys

EVENT_KEYS = frozenset((
    'authorization', 'revision', 'subject', 'operation', 'start', 'end',
    'parameters',
))
OPERATIONS = frozenset(('grant', 'withdraw'))
REVISION_RE = re.compile(r'\A(?:0|[1-9][0-9]*)\Z')
DATE_RE = re.compile(r'\A([0-9]{4})-([0-9]{2})-([0-9]{2})\Z')
DEFAULT_MANIFESTS = '/workspace/policy/manifests.json'
MANIFESTS_ENV = 'MARROWFLEET_POLICY_MANIFESTS'


class PolicyError(ValueError):
    """The request, its transport or its authorization history is invalid."""


def _nonempty_string(value):
    return isinstance(value, str) and value != ''


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _check_date(value, what):
    match = DATE_RE.match(value) if isinstance(value, str) else None
    if match is None:
        raise PolicyError('%s is not an ISO YYYY-MM-DD date: %r' % (what, value))
    year, month, day = (int(part) for part in match.groups())
    if not 1 <= month <= 12:
        raise PolicyError('%s has no such month: %r' % (what, value))
    days = [31, 29 if _leap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30,
            31][month - 1]
    if not 1 <= day <= days:
        raise PolicyError('%s has no such day: %r' % (what, value))
    return value


def _canonical_events(events):
    return json.dumps(events, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def _load_manifests():
    path = os.environ.get(MANIFESTS_ENV) or DEFAULT_MANIFESTS
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            manifests = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PolicyError('the release manifests are unreadable: %s' % exc)
    generations = manifests.get('generations') if isinstance(manifests, dict) \
        else None
    if not isinstance(generations, dict):
        raise PolicyError('the release manifests name no generations')
    return generations


def _commit_index(commits):
    if not isinstance(commits, list) or not commits:
        raise PolicyError('the transport commits no batch')
    index = {}
    for commit in commits:
        if not isinstance(commit, dict):
            raise PolicyError('a commit is not an object')
        batch = commit.get('batch')
        parts = commit.get('parts')
        digest = commit.get('sha256')
        if not _nonempty_string(batch):
            raise PolicyError('a commit names no batch')
        if not _positive_int(parts):
            raise PolicyError('commit %r does not carry a positive part count'
                              % batch)
        if not _nonempty_string(digest):
            raise PolicyError('commit %r carries no digest' % batch)
        seen = index.get(batch)
        if seen is None:
            index[batch] = (parts, digest)
        elif seen != (parts, digest):
            raise PolicyError('batch %r is committed twice on different terms'
                              % batch)
    return index


def _page_index(pages, commits):
    if not isinstance(pages, list):
        raise PolicyError('the transport carries no pages list')
    index = {batch: {} for batch in commits}
    for page in pages:
        if not isinstance(page, dict):
            raise PolicyError('a page is not an object')
        batch = page.get('batch')
        part = page.get('part')
        events = page.get('events')
        if not _nonempty_string(batch):
            raise PolicyError('a page names no batch')
        if batch not in commits:
            raise PolicyError('batch %r carries pages but is not committed'
                              % batch)
        if not _nonnegative_int(part):
            raise PolicyError('a page of batch %r carries no part number'
                              % batch)
        if part >= commits[batch][0]:
            raise PolicyError('batch %r carries a part beyond its commit: %d'
                              % (batch, part))
        if not isinstance(events, list):
            raise PolicyError('part %d of batch %r carries no events list'
                              % (part, batch))
        seen = index[batch].get(part)
        if seen is None:
            index[batch][part] = events
        elif seen != events:
            raise PolicyError('part %d of batch %r is delivered twice with '
                              'different events' % (part, batch))
    return index


def _check_generation(transport, digests):
    generation = transport['generation']
    if not _nonempty_string(generation):
        raise PolicyError('the transport names no generation')
    manifest = _load_manifests().get(generation)
    if not isinstance(manifest, dict) or not isinstance(
            manifest.get('batches'), dict):
        raise PolicyError('the manifests do not name generation %r'
                          % generation)
    listed = manifest['batches']
    for batch, digest in listed.items():
        if batch not in digests:
            raise PolicyError('generation %r does not commit batch %r'
                              % (generation, batch))
        if not _nonempty_string(digest) or digests[batch] != digest.lower():
            raise PolicyError('batch %r is not at the digest generation %r '
                              'lists' % (batch, generation))
    for batch in digests:
        if batch not in listed:
            raise PolicyError('generation %r does not list committed batch %r'
                              % (generation, batch))


def _history_from_transport(transport):
    if not isinstance(transport, dict):
        raise PolicyError('the transport is not an object')
    commits = _commit_index(transport.get('commits'))
    pages = _page_index(transport.get('pages'), commits)

    history = []
    digests = {}
    for batch in sorted(commits):
        parts, digest = commits[batch]
        events = []
        for part in range(parts):
            if part not in pages[batch]:
                raise PolicyError('batch %r is missing part %d' % (batch, part))
            events.extend(pages[batch][part])
        computed = hashlib.sha256(_canonical_events(events)).hexdigest()
        if computed != digest.lower():
            raise PolicyError('batch %r does not match its committed digest'
                              % batch)
        digests[batch] = computed
        history.extend(events)

    if 'generation' in transport:
        _check_generation(transport, digests)
    return history


def _check_event(event):
    if not isinstance(event, dict):
        raise PolicyError('an authorization record is not an object')
    if frozenset(event) != EVENT_KEYS:
        raise PolicyError('an authorization record does not carry exactly the '
                          'required keys: %r' % sorted(event))
    for field in ('authorization', 'subject'):
        if not _nonempty_string(event[field]):
            raise PolicyError('an authorization record carries no %s' % field)
    if not isinstance(event['revision'], str) or not REVISION_RE.match(
            event['revision']):
        raise PolicyError('authorization %s carries a revision that is not a '
                          'canonical decimal: %r'
                          % (event['authorization'], event['revision']))
    if event['operation'] not in OPERATIONS:
        raise PolicyError('authorization %s carries an unknown operation: %r'
                          % (event['authorization'], event['operation']))
    parameters = event['parameters']
    if not isinstance(parameters, list) or not all(
            isinstance(item, str) for item in parameters):
        raise PolicyError('authorization %s carries parameters that are not a '
                          'list of strings' % event['authorization'])
    start = _check_date(event['start'],
                        'the start of %s' % event['authorization'])
    end = _check_date(event['end'], 'the end of %s' % event['authorization'])
    if not start < end:
        raise PolicyError('authorization %s does not start before it ends'
                          % event['authorization'])


def _winners(history):
    """Reduce the history to the winning revision of each authorization."""
    revisions = {}
    for event in history:
        authorization = event['authorization']
        number = int(event['revision'])
        held = revisions.setdefault(authorization, {})
        if number in held:
            if held[number] != event:
                raise PolicyError('authorization %s carries conflicting '
                                  'revision %s'
                                  % (authorization, event['revision']))
            continue
        held[number] = event
    return [held[max(held)] for held in revisions.values()]


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError('the request is not an object')
    now = _check_date(request.get('now'), 'the evaluation instant')
    subjects = request.get('subjects')
    if not isinstance(subjects, list) or not all(
            _nonempty_string(subject) for subject in subjects):
        raise PolicyError('the request does not name its subjects')

    if 'transport' in request:
        if 'events' in request:
            raise PolicyError('the request carries both a transport and events')
        history = _history_from_transport(request['transport'])
    else:
        history = request.get('events')
        if not isinstance(history, list):
            raise PolicyError('the request carries no events list')

    for event in history:
        _check_event(event)

    active = {}
    for event in _winners(history):
        if event['operation'] != 'grant':
            continue
        subject = event['subject']
        if subject not in subjects:
            continue
        if not event['start'] <= now < event['end']:
            continue
        if subject in active:
            raise PolicyError('subject %s carries more than one active '
                              'authorization' % subject)
        active[subject] = event

    return {subject: active[subject]['parameters'] if subject in active else None
            for subject in subjects}


def main():
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write('resolve: the request is not JSON: %s\n' % exc)
        return 1
    try:
        decision = resolve(request)
    except PolicyError as exc:
        sys.stderr.write('resolve: %s\n' % exc)
        return 1
    print(json.dumps(decision, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
