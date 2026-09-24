"""Resolve the current signed authorization snapshot.

Implements docs/policy-reconciliation.md. The resolver validates the complete
candidate before projecting anything; any breach raises InvalidHistory and the
CLI exits nonzero without printing a result.
"""
import hashlib
import json
import os
import sys

MANIFEST_FILE = os.environ.get('MARROWFLEET_POLICY_MANIFESTS',
                               '/workspace/policy/manifests.json')

EVENT_KEYS = {'authorization', 'revision', 'subject', 'operation', 'start',
              'end', 'parameters'}
OPERATIONS = {'grant', 'withdraw'}
DATE_LEN = len('YYYY-MM-DD')


class InvalidHistory(Exception):
    """The delivered history, transport or manifest record is not sound."""


def fail(message):
    raise InvalidHistory(message)


def as_nonempty_string(value, what):
    if not isinstance(value, str) or not value:
        fail('%s is not a nonempty string' % what)
    return value


def canonical_revision(value):
    """A canonical nonnegative decimal string, or InvalidHistory."""
    if not isinstance(value, str) or not value.isdigit() or (
            value != '0' and value.startswith('0')):
        fail('revision %r is not a canonical nonnegative decimal' % (value,))
    return int(value)


def as_date(value, what):
    if not isinstance(value, str) or len(value) != DATE_LEN or value[4] != '-' \
            or value[7] != '-':
        fail('%s %r is not an ISO YYYY-MM-DD date' % (what, value))
    try:
        parts = (int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        fail('%s %r is not an ISO YYYY-MM-DD date' % (what, value))
    year, month, day = parts
    if not 1 <= month <= 12:
        fail('%s %r is not a calendar date' % (what, value))
    if not 1 <= day <= [31, 29 if year % 4 == 0 and (year % 100 != 0
                                                     or year % 400 == 0)
                        else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][
                            month - 1]:
        fail('%s %r is not a calendar date' % (what, value))
    return value


def as_parameters(value):
    if not isinstance(value, list):
        fail('parameters is not a list')
    for item in value:
        as_nonempty_string(item, 'a parameter')
    return value


def validate_event(event, index):
    if not isinstance(event, dict) or set(event) != EVENT_KEYS:
        fail('event %d does not carry exactly the event keys' % index)
    as_nonempty_string(event['authorization'], 'an authorization id')
    canonical_revision(event['revision'])
    as_nonempty_string(event['subject'], 'a subject')
    if event['operation'] not in OPERATIONS:
        fail('event %d has operation %r' % (index, event['operation']))
    as_parameters(event['parameters'])
    start = as_date(event['start'], 'start')
    end = as_date(event['end'], 'end')
    if not start < end:
        fail('event %d has start %s not strictly before end %s'
             % (index, start, end))
    return event


def batch_digest(events):
    return hashlib.sha256(json.dumps(
        events, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False).encode('utf-8')).hexdigest()


def transport_events(transport):
    """Validate the segmented delivery and return the ordered event list."""
    if not isinstance(transport, dict):
        fail('transport is not an object')
    for key in ('commits', 'pages'):
        if not isinstance(transport[key], list):
            fail('transport %s is not a list' % key)
    pages = {}
    for page in transport['pages']:
        if not isinstance(page, dict):
            fail('a page is not an object')
        for key in ('batch', 'part', 'events'):
            if key not in page:
                fail('a page does not carry %s' % key)
        batch = as_nonempty_string(page['batch'], 'a page batch')
        part = page['part']
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            fail('batch %s part %r is not a zero-based integer' % (batch, part))
        if not isinstance(page['events'], list):
            fail('batch %s part %d events is not a list' % (batch, part))
        key = (batch, part)
        if key in pages:
            if page['events'] != pages[key]:
                fail('batch %s part %d is replayed with different events'
                     % key)
            continue  # identical replay is idempotent
        pages[key] = page['events']
    events_by_batch = {}
    for commit in transport['commits']:
        if not isinstance(commit, dict):
            fail('a commit is not an object')
        for key in ('batch', 'parts', 'sha256'):
            if key not in commit:
                fail('a commit does not carry %s' % key)
        batch = as_nonempty_string(commit['batch'], 'a commit batch')
        parts = commit['parts']
        if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
            fail('batch %s parts %r is not a positive integer' % (batch, parts))
        want = list(range(parts))
        got = sorted(part for b, part in pages if b == batch)
        if got != want:
            fail('batch %s carries parts %s against %d committed'
                 % (batch, got, parts))
        events = []
        for part in want:
            events.extend(pages[(batch, part)])
        digest = batch_digest(events)
        if digest != commit['sha256']:
            fail('batch %s digest %s does not match its commit'
                 % (batch, digest))
        replayed = events_by_batch.get(batch)
        if replayed is None:
            events_by_batch[batch] = events
        elif replayed != events:  # an identical replay of a commit is idempotent
            fail('batch %s is committed with conflicting contents' % batch)
    for batch in sorted(set(b for b, _ in pages) - set(events_by_batch)):
        fail('batch %s has pages but no commit' % batch)
    check_generation(transport, events_by_batch)
    return [event for batch in sorted(events_by_batch,
                                      key=lambda b: (len(b), b))
            for event in events_by_batch[batch]]


def check_generation(transport, events_by_batch):
    """A released delivery must commit exactly its generation's batches."""
    generation = transport.get('generation')
    if generation is None:
        return
    as_nonempty_string(generation, 'a generation')
    with open(MANIFEST_FILE, encoding='utf-8') as handle:
        manifests = json.load(handle)
    generations = manifests.get('generations')
    if not isinstance(generations, dict) or generation not in generations:
        fail('generation %s is not named by the manifest' % generation)
    listed = generations[generation].get('batches')
    if not isinstance(listed, dict):
        fail('generation %s carries no batch table' % generation)
    committed = {commit['batch']: commit['sha256']
                 for commit in transport['commits']}
    if set(committed) != set(listed):
        fail('generation %s commits %s against the manifest batches %s'
             % (generation, sorted(committed), sorted(listed)))
    for batch, digest in listed.items():
        if committed[batch] != digest:
            fail('batch %s digest differs from the manifest' % batch)


def events_from_request(request):
    """Deliver the history the request carries, events only or by transport."""
    if not isinstance(request, dict):
        fail('the request is not an object')
    if 'transport' in request:
        return transport_events(request['transport'])
    if 'events' in request:
        events = request['events']
        if not isinstance(events, list):
            fail('events is not a list')
        return events
    fail('the request carries neither events nor a transport')


def project(request, events):
    """Form the corrected authorization snapshot, then project it."""
    now = as_date(request.get('now'), 'now')
    subjects = request.get('subjects')
    if not isinstance(subjects, list):
        fail('subjects is not a list')
    for subject in subjects:
        as_nonempty_string(subject, 'a requested subject')

    carried = {}
    for index, event in enumerate(events):
        record = validate_event(event, index)
        authorization = record['authorization']
        revision = canonical_revision(record['revision'])
        previous = carried.get((authorization, revision))
        if previous is None:
            carried[(authorization, revision)] = record
        elif previous != record:
            fail('authorization %s revision %s is carried with conflicting '
                 'records' % (authorization, record['revision']))

    by_authorization = {}
    for (authorization, revision), record in carried.items():
        winner = by_authorization.get(authorization)
        if winner is None or revision > winner[0]:
            by_authorization[authorization] = (revision, record)

    active = {}
    for revision, record in by_authorization.values():
        if record['operation'] == 'withdraw':
            continue  # a winning withdrawal contributes no active decision
        if not record['start'] <= now < record['end']:
            continue
        if record['subject'] in active:
            fail('subject %s carries more than one active authorization'
                 % record['subject'])
        active[record['subject']] = record['parameters']
    return {subject: active.get(subject) for subject in subjects}


def resolve(request):
    events = events_from_request(request)
    for index, event in enumerate(events):
        validate_event(event, index)
    return project(request, events)


if __name__ == '__main__':
    try:
        result = resolve(json.load(sys.stdin))
    except InvalidHistory as error:
        sys.stderr.write('resolve: %s\n' % error)
        sys.exit(1)
    except (ValueError, KeyError, TypeError, OSError) as error:
        sys.stderr.write('resolve: malformed request: %s\n' % error)
        sys.exit(1)
    print(json.dumps(result, sort_keys=True))
