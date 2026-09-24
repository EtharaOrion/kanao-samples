"""Resolve the current signed authorization snapshot.

The contract this implements is `docs/policy-reconciliation.md`. A request is
either

    {"now": <date>, "subjects": [...], "events": [...]}

or a segmented delivery

    {"now": <date>, "subjects": [...],
     "transport": {"generation": <str>, "commits": [...], "pages": [...]}}

Complete validation of the whole candidate precedes any projection, so a
candidate that is not admissible yields an error and no partial result.
"""
import hashlib
import json
import os
import re
import sys

MANIFESTS_ENV = 'MARROWFLEET_POLICY_MANIFESTS'
DEFAULT_MANIFESTS = '/workspace/policy/manifests.json'

EVENT_KEYS = {'authorization', 'revision', 'subject', 'operation', 'start',
              'end', 'parameters'}
OPERATIONS = ('grant', 'withdraw')
REVISION = re.compile(r'0|[1-9][0-9]*')
DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


class Invalid(Exception):
    """The candidate is not admissible; it contributes no decisions."""


def _nonempty_str(value):
    return isinstance(value, str) and value != ''


def _integer(value, minimum):
    return isinstance(value, int) and not isinstance(value, bool) \
        and value >= minimum


def _date(value):
    if not _nonempty_str(value) or not DATE.fullmatch(value):
        return False
    year, month, day = (int(part) for part in value.split('-'))
    if not 1 <= month <= 12:
        return False
    last = MONTH_DAYS[month - 1]
    if month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        last = 29
    return 1 <= day <= last


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def _digest(events):
    return hashlib.sha256(_canonical(events)).hexdigest()


def _manifests():
    path = os.environ.get(MANIFESTS_ENV) or DEFAULT_MANIFESTS
    try:
        with open(path, 'rb') as handle:
            document = json.loads(handle.read().decode('utf-8'))
    except (OSError, ValueError) as exc:
        raise Invalid('manifests unreadable at %s: %s' % (path, exc))
    generations = document.get('generations') if isinstance(document, dict) \
        else None
    if not isinstance(generations, dict):
        raise Invalid('manifests carry no generations mapping')
    return generations


def _collect_commits(transport):
    commits = transport.get('commits')
    if not isinstance(commits, list) or not commits:
        raise Invalid('transport carries no commits')
    seen = {}
    for commit in commits:
        if not isinstance(commit, dict) or set(commit) != {'batch', 'parts',
                                                           'sha256'}:
            raise Invalid('commit keys are not exactly batch, parts, sha256')
        batch, parts, sha256 = commit['batch'], commit['parts'], commit['sha256']
        if not _nonempty_str(batch):
            raise Invalid('commit batch is not a nonempty string')
        if not _integer(parts, 1):
            raise Invalid('commit parts is not a positive integer')
        if not _nonempty_str(sha256):
            raise Invalid('commit sha256 is not a nonempty string')
        held = seen.get(batch)
        if held is None:
            seen[batch] = (parts, sha256)
        elif held != (parts, sha256):
            raise Invalid('conflicting commit replay for batch %s' % batch)
    return seen


def _collect_pages(transport, commits):
    pages = transport.get('pages')
    if not isinstance(pages, list):
        raise Invalid('transport carries no pages')
    seen = {}
    for page in pages:
        if not isinstance(page, dict) or set(page) != {'batch', 'part',
                                                       'events'}:
            raise Invalid('page keys are not exactly batch, part, events')
        batch, part, events = page['batch'], page['part'], page['events']
        if not _nonempty_str(batch):
            raise Invalid('page batch is not a nonempty string')
        if not _integer(part, 0):
            raise Invalid('page part is not a zero-based integer')
        if not isinstance(events, list):
            raise Invalid('page events is not a list')
        if batch not in commits:
            raise Invalid('page carried for uncommitted batch %s' % batch)
        if part >= commits[batch][0]:
            raise Invalid('batch %s carries extra part %d' % (batch, part))
        held = seen.get((batch, part))
        if held is None:
            seen[(batch, part)] = events
        elif _canonical(held) != _canonical(events):
            raise Invalid('conflicting page replay for batch %s part %d'
                          % (batch, part))
    return seen


def _check_manifest(generation, commits):
    generations = _manifests()
    if generation not in generations:
        raise Invalid('the manifests do not name generation %s' % generation)
    entry = generations[generation]
    batches = entry.get('batches') if isinstance(entry, dict) else None
    if not isinstance(batches, dict):
        raise Invalid('generation %s lists no batches' % generation)
    if set(batches) != set(commits):
        raise Invalid('generation %s lists batches %s but the transport '
                      'commits %s' % (generation, sorted(batches),
                                      sorted(commits)))
    for batch in sorted(batches):
        listed = batches[batch]
        if not _nonempty_str(listed):
            raise Invalid('generation %s lists no digest for batch %s'
                          % (generation, batch))
        if commits[batch][1] != listed:
            raise Invalid('generation %s lists batch %s at %s but it was '
                          'committed at %s'
                          % (generation, batch, listed, commits[batch][1]))


def _history(transport):
    """Every event of the delivered history, in a stable batch order."""
    if not isinstance(transport, dict):
        raise Invalid('transport is not an object')
    allowed = {'commits', 'pages'}
    generation = None
    if 'generation' in transport:
        generation = transport['generation']
        allowed.add('generation')
        if not _nonempty_str(generation):
            raise Invalid('transport generation is not a nonempty string')
    extra = set(transport) - allowed
    if extra:
        raise Invalid('transport carries unexpected keys: %s'
                      % ','.join(sorted(extra)))

    commits = _collect_commits(transport)
    pages = _collect_pages(transport, commits)
    if generation is not None:
        _check_manifest(generation, commits)

    events = []
    for batch in sorted(commits):
        parts, sha256 = commits[batch]
        assembled = []
        for part in range(parts):
            if (batch, part) not in pages:
                raise Invalid('batch %s is missing part %d' % (batch, part))
            assembled.extend(pages[(batch, part)])
        if _digest(assembled) != sha256:
            raise Invalid('batch %s does not match its committed digest'
                          % batch)
        events.extend(assembled)
    return events


def _validate_events(events):
    if not isinstance(events, list):
        raise Invalid('events is not a list')
    for event in events:
        if not isinstance(event, dict) or set(event) != EVENT_KEYS:
            raise Invalid('event keys are not exactly %s'
                          % ', '.join(sorted(EVENT_KEYS)))
        if not _nonempty_str(event['authorization']):
            raise Invalid('event authorization is not a nonempty string')
        if not _nonempty_str(event['subject']):
            raise Invalid('event subject is not a nonempty string')
        revision = event['revision']
        if not _nonempty_str(revision) or not REVISION.fullmatch(revision):
            raise Invalid('revision %r is not a canonical nonnegative decimal '
                          'string' % (revision,))
        if event['operation'] not in OPERATIONS:
            raise Invalid('operation %r is not grant or withdraw'
                          % (event['operation'],))
        parameters = event['parameters']
        if not isinstance(parameters, list) or not all(
                isinstance(item, str) for item in parameters):
            raise Invalid('parameters is not a list of strings')
        if not _date(event['start']) or not _date(event['end']):
            raise Invalid('dates are not ISO YYYY-MM-DD')
        if not event['start'] < event['end']:
            raise Invalid('start %s is not strictly before end %s'
                          % (event['start'], event['end']))


def _winners(events):
    """The corrected snapshot: the highest revision of each authorization.

    Authorizations are identified independently of subjects, so a correction
    that moves an authorization to another subject cannot leave its older
    revision standing on the subject it came from.

    A conflict between two records of the same revision invalidates the whole
    history, whether or not that revision goes on to win, so every revision of
    every authorization is checked before any winner is chosen.
    """
    revisions = {}
    for event in events:
        key = (event['authorization'], int(event['revision']))
        held = revisions.get(key)
        if held is None:
            revisions[key] = event
        elif _canonical(held) != _canonical(event):
            raise Invalid('authorization %s carries conflicting records of '
                          'revision %d' % key)
    selected = {}
    for (authorization, revision), event in revisions.items():
        held = selected.get(authorization)
        if held is None or revision > held[0]:
            selected[authorization] = (revision, event)
    return {name: event for name, (_, event) in selected.items()}


def _decisions(winners, now):
    by_subject = {}
    for authorization in sorted(winners):
        event = winners[authorization]
        if event['operation'] != 'grant':
            continue
        if not event['start'] <= now < event['end']:
            continue
        by_subject.setdefault(event['subject'], []).append(event)
    for subject in sorted(by_subject):
        if len(by_subject[subject]) > 1:
            raise Invalid('subject %s carries %d active authorizations'
                          % (subject, len(by_subject[subject])))
    return {subject: events[0]['parameters']
            for subject, events in by_subject.items()}


def resolve(request):
    if not isinstance(request, dict):
        raise Invalid('request is not an object')
    now = request.get('now')
    if not _date(now):
        raise Invalid('request now is not an ISO YYYY-MM-DD date')
    subjects = request.get('subjects')
    if not isinstance(subjects, list) or not all(
            _nonempty_str(subject) for subject in subjects):
        raise Invalid('request subjects is not a list of nonempty strings')

    if 'transport' in request:
        if 'events' in request:
            raise Invalid('request carries both transport and events')
        events = _history(request['transport'])
    elif 'events' in request:
        events = request['events']
    else:
        raise Invalid('request carries neither transport nor events')

    _validate_events(events)
    decisions = _decisions(_winners(events), now)
    return {subject: decisions.get(subject) for subject in subjects}


def main():
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write('resolve: the request is not JSON: %s\n' % exc)
        return 1
    try:
        result = resolve(request)
    except Invalid as exc:
        sys.stderr.write('resolve: %s\n' % exc)
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
