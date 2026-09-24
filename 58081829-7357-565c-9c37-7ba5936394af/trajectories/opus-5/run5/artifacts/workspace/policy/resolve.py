"""Resolve the current signed authorization snapshot.

The complete candidate is validated before anything is projected: an invalid
history raises PolicyError and yields no partial result.
"""
import hashlib
import json
import os
import sys

EVENT_KEYS = {'authorization', 'revision', 'subject', 'operation', 'start',
              'end', 'parameters'}
DEFAULT_MANIFESTS = '/workspace/policy/manifests.json'


class PolicyError(Exception):
    """The candidate is not an admissible authorization history."""


def _canonical(events):
    return json.dumps(events, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def _nonempty_string(value):
    return isinstance(value, str) and value != ''


def _leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _is_date(value):
    if not isinstance(value, str) or len(value) != 10:
        return False
    if value[4] != '-' or value[7] != '-':
        return False
    year, month, day = value[:4], value[5:7], value[8:10]
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return False
    month, day = int(month), int(day)
    if not 1 <= month <= 12:
        return False
    lengths = [31, 29 if _leap(int(year)) else 28, 31, 30, 31, 30, 31, 31, 30,
               31, 30, 31]
    return 1 <= day <= lengths[month - 1]


def _is_revision(value):
    if not isinstance(value, str) or not value.isdigit():
        return False
    return value == '0' or not value.startswith('0')


def _manifest_batches(generation):
    path = os.environ.get('MARROWFLEET_POLICY_MANIFESTS', DEFAULT_MANIFESTS)
    try:
        with open(path, encoding='utf-8') as handle:
            manifests = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PolicyError('manifests unreadable: %s' % exc)
    if not isinstance(manifests, dict):
        raise PolicyError('manifests malformed')
    generations = manifests.get('generations')
    if not isinstance(generations, dict) or generation not in generations:
        raise PolicyError('generation %r is not named by the manifests'
                          % generation)
    entry = generations[generation]
    if not isinstance(entry, dict):
        raise PolicyError('generation %r is malformed' % generation)
    batches = entry.get('batches')
    if not isinstance(batches, dict) or not all(
            _nonempty_string(batch) and _nonempty_string(digest)
            for batch, digest in batches.items()):
        raise PolicyError('generation %r lists no usable batches' % generation)
    return batches


def _collect_commits(transport):
    commits = transport.get('commits')
    if not isinstance(commits, list):
        raise PolicyError('transport carries no commits')
    seen = {}
    for commit in commits:
        if not isinstance(commit, dict):
            raise PolicyError('commit malformed')
        batch = commit.get('batch')
        parts = commit.get('parts')
        digest = commit.get('sha256')
        if not _nonempty_string(batch):
            raise PolicyError('commit carries no batch')
        if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
            raise PolicyError('commit %r carries no positive part count'
                              % batch)
        if not _nonempty_string(digest):
            raise PolicyError('commit %r carries no digest' % batch)
        known = seen.get(batch)
        if known is None:
            seen[batch] = (parts, digest)
        elif known != (parts, digest):
            raise PolicyError('conflicting commits for batch %r' % batch)
    if not seen:
        raise PolicyError('transport commits nothing')
    return seen


def _collect_pages(transport):
    pages = transport.get('pages')
    if not isinstance(pages, list):
        raise PolicyError('transport carries no pages')
    seen = {}
    for page in pages:
        if not isinstance(page, dict):
            raise PolicyError('page malformed')
        batch = page.get('batch')
        part = page.get('part')
        events = page.get('events')
        if not _nonempty_string(batch):
            raise PolicyError('page carries no batch')
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            raise PolicyError('page of batch %r carries no part number'
                              % batch)
        if not isinstance(events, list):
            raise PolicyError('page %r/%s carries no events' % (batch, part))
        known = seen.get((batch, part))
        if known is None:
            seen[(batch, part)] = events
        elif _canonical(known) != _canonical(events):
            raise PolicyError('conflicting replay of page %r/%s'
                              % (batch, part))
    return seen


def _assemble(transport):
    """Return the complete event history a segmented delivery carries."""
    if not isinstance(transport, dict):
        raise PolicyError('transport malformed')
    commits = _collect_commits(transport)
    pages = _collect_pages(transport)

    generation = transport.get('generation')
    if generation is not None:
        if not _nonempty_string(generation):
            raise PolicyError('transport names no usable generation')
        listed = _manifest_batches(generation)
        if set(listed) != set(commits):
            raise PolicyError('generation %r commits %s, its manifest lists %s'
                              % (generation, sorted(commits), sorted(listed)))
        for batch, (_parts, digest) in commits.items():
            if listed[batch] != digest:
                raise PolicyError('batch %r is committed at a digest the '
                                  'manifest does not list' % batch)

    for batch, part in pages:
        if batch not in commits:
            raise PolicyError('page %r/%s is not committed' % (batch, part))

    history = []
    for batch in sorted(commits):
        parts, digest = commits[batch]
        delivered = sorted(part for held, part in pages if held == batch)
        if delivered != list(range(parts)):
            raise PolicyError('batch %r is delivered as parts %s, not %s'
                              % (batch, delivered, list(range(parts))))
        events = []
        for part in range(parts):
            events.extend(pages[(batch, part)])
        if hashlib.sha256(_canonical(events)).hexdigest() != digest:
            raise PolicyError('batch %r does not match its digest' % batch)
        history.extend(events)
    return history


def _validate_event(event):
    if not isinstance(event, dict):
        raise PolicyError('event malformed')
    if set(event) != EVENT_KEYS:
        raise PolicyError('event carries the keys %s, not %s'
                          % (sorted(event), sorted(EVENT_KEYS)))
    if not _nonempty_string(event['authorization']):
        raise PolicyError('event carries no authorization')
    if not _nonempty_string(event['subject']):
        raise PolicyError('event carries no subject')
    if not _is_revision(event['revision']):
        raise PolicyError('authorization %s carries the revision %r'
                          % (event['authorization'], event['revision']))
    if event['operation'] not in ('grant', 'withdraw'):
        raise PolicyError('authorization %s carries the operation %r'
                          % (event['authorization'], event['operation']))
    if not isinstance(event['parameters'], list) or not all(
            isinstance(item, str) for item in event['parameters']):
        raise PolicyError('authorization %s carries no parameter list'
                          % event['authorization'])
    if not _is_date(event['start']) or not _is_date(event['end']):
        raise PolicyError('authorization %s carries the interval %r..%r'
                          % (event['authorization'], event['start'],
                             event['end']))
    if event['start'] >= event['end']:
        raise PolicyError('authorization %s does not start before it ends'
                          % event['authorization'])


def _current_revisions(history):
    """Return the winning revision of every authorization in the history."""
    seen = {}
    winners = {}
    for event in history:
        _validate_event(event)
        authorization = event['authorization']
        revision = int(event['revision'])
        known = seen.get((authorization, revision))
        if known is None:
            seen[(authorization, revision)] = event
        elif _canonical(known) != _canonical(event):
            raise PolicyError('authorization %s carries conflicting revision %s'
                              % (authorization, event['revision']))
        current = winners.get(authorization)
        if current is None or revision > current[0]:
            winners[authorization] = (revision, event)
    return {authorization: event
            for authorization, (_revision, event) in winners.items()}


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError('request malformed')
    now = request.get('now')
    if not _is_date(now):
        raise PolicyError('request carries no evaluation instant')
    subjects = request.get('subjects')
    if not isinstance(subjects, list) or not all(
            _nonempty_string(subject) for subject in subjects):
        raise PolicyError('request carries no subject list')

    carries_transport = 'transport' in request
    carries_events = 'events' in request
    if carries_transport and carries_events:
        raise PolicyError('request carries both a transport and events')
    if carries_transport:
        history = _assemble(request['transport'])
    elif carries_events:
        history = request['events']
        if not isinstance(history, list):
            raise PolicyError('request carries no event list')
    else:
        raise PolicyError('request carries neither a transport nor events')

    decisions = {}
    for authorization, event in sorted(_current_revisions(history).items()):
        if event['operation'] != 'grant':
            continue
        if not event['start'] <= now < event['end']:
            continue
        subject = event['subject']
        if subject not in subjects:
            continue
        if subject in decisions:
            raise PolicyError('subject %s carries the active authorizations '
                              '%s and %s'
                              % (subject, decisions[subject][0], authorization))
        decisions[subject] = (authorization, event['parameters'])

    return {subject: decisions[subject][1] if subject in decisions else None
            for subject in subjects}


if __name__ == '__main__':
    try:
        print(json.dumps(resolve(json.load(sys.stdin)), sort_keys=True))
    except PolicyError as error:
        sys.stderr.write('resolve: %s\n' % error)
        sys.exit(1)
    except ValueError as error:
        sys.stderr.write('resolve: request is not JSON: %s\n' % error)
        sys.exit(1)
