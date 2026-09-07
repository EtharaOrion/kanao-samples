"""Resolve the current signed authorization snapshot.

Validation is complete before any projection: an invalid history raises, with
no partial result. Authorizations are resolved independently of subjects, so a
correction that moves an authorization to another subject retires its older
revision on the former subject.
"""
import hashlib
import json
import re
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation",
              "start", "end", "parameters"}
REVISION = re.compile(r"^(0|[1-9][0-9]*)$")
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class PolicyError(ValueError):
    pass


def _canonical(events):
    return json.dumps(events, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def _nonempty(value):
    return isinstance(value, str) and value != ''


def _check_date(value, field):
    if not _nonempty(value) or not DATE.match(value):
        raise PolicyError('bad %s' % field)
    try:
        y, m, d = (int(p) for p in value.split('-'))
        import datetime
        datetime.date(y, m, d)
    except Exception:
        raise PolicyError('bad %s' % field)


def _validate_event(event):
    if not isinstance(event, dict) or set(event) != EVENT_KEYS:
        raise PolicyError('event keys are exactly the seven required keys')
    if not _nonempty(event['authorization']):
        raise PolicyError('bad authorization')
    if not _nonempty(event['subject']):
        raise PolicyError('bad subject')
    if not isinstance(event['revision'], str) or not REVISION.match(event['revision']):
        raise PolicyError('revision must be canonical nonnegative decimal')
    if event['operation'] not in ('grant', 'withdraw'):
        raise PolicyError('operation must be grant or withdraw')
    _check_date(event['start'], 'start')
    _check_date(event['end'], 'end')
    if not event['start'] < event['end']:
        raise PolicyError('start must be strictly before end')
    params = event['parameters']
    if not isinstance(params, list) or not all(isinstance(x, str) for x in params):
        raise PolicyError('parameters must be a list of strings')


def _assemble(transport):
    if not isinstance(transport, dict):
        raise PolicyError('bad transport')
    commits, pages = transport.get('commits'), transport.get('pages')
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise PolicyError('bad transport')

    declared = {}
    for commit in commits:
        if not isinstance(commit, dict):
            raise PolicyError('bad commit')
        batch, parts, digest = commit.get('batch'), commit.get('parts'), commit.get('sha256')
        if not _nonempty(batch) or not _nonempty(digest):
            raise PolicyError('bad commit')
        if not isinstance(parts, int) or isinstance(parts, bool) or parts <= 0:
            raise PolicyError('parts must be a positive integer')
        if batch in declared:
            if declared[batch] != (parts, digest):
                raise PolicyError('conflicting commit replay for batch %s' % batch)
        else:
            declared[batch] = (parts, digest)

    received = {}
    for page in pages:
        if not isinstance(page, dict):
            raise PolicyError('bad page')
        batch, part, events = page.get('batch'), page.get('part'), page.get('events')
        if not _nonempty(batch):
            raise PolicyError('bad page')
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            raise PolicyError('part must be a zero-based integer')
        if not isinstance(events, list):
            raise PolicyError('page events must be a list')
        slot = received.setdefault(batch, {})
        if part in slot:
            if _canonical(slot[part]) != _canonical(events):
                raise PolicyError('conflicting page replay for batch %s part %s'
                                  % (batch, part))
        else:
            slot[part] = events

    for batch in received:
        if batch not in declared:
            raise PolicyError('uncommitted pages for batch %s' % batch)

    history = []
    for batch in sorted(declared):
        parts, digest = declared[batch]
        slot = received.get(batch, {})
        if set(slot) != set(range(parts)):
            raise PolicyError('missing or extra parts for batch %s' % batch)
        events = []
        for index in range(parts):
            events.extend(slot[index])
        if hashlib.sha256(_canonical(events)).hexdigest() != digest:
            raise PolicyError('digest mismatch for batch %s' % batch)
        history.extend(events)
    return history


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError('bad request')
    now = request.get('now')
    _check_date(now, 'now')
    subjects = request.get('subjects')
    if not isinstance(subjects, list) or not all(_nonempty(s) for s in subjects):
        raise PolicyError('bad subjects')

    if 'transport' in request:
        if 'events' in request:
            raise PolicyError('request carries both events and transport')
        history = _assemble(request['transport'])
    else:
        history = request.get('events')
        if not isinstance(history, list):
            raise PolicyError('bad events')

    # Every event in the complete history must be valid, including superseded,
    # withdrawn, out-of-window and unrequested-subject records.
    for event in history:
        _validate_event(event)

    # Resolve revisions per authorization, independently of subject.
    # Conflicts are detected across every event at a given revision, not just
    # against the running maximum, so arrival order cannot hide them.
    by_revision = {}
    for event in history:
        key = (event['authorization'], int(event['revision']))
        seen = by_revision.get(key)
        if seen is None:
            by_revision[key] = event
        elif _canonical(seen) != _canonical(event):
            raise PolicyError('conflicting revision %s for %s'
                              % (event['revision'], event['authorization']))

    winners = {}
    for (authorization, revision), event in by_revision.items():
        current = winners.get(authorization)
        if current is None or revision > int(current['revision']):
            winners[authorization] = event

    # Project the corrected snapshot: active grants only.
    active = {}
    for key in sorted(winners):
        event = winners[key]
        if event['operation'] != 'grant':
            continue
        if not (event['start'] <= now < event['end']):
            continue
        subject = event['subject']
        if subject in active:
            raise PolicyError('multiple active authorizations for %s' % subject)
        active[subject] = event

    return {subject: (active[subject]['parameters'] if subject in active else None)
            for subject in subjects}


if __name__ == '__main__':
    try:
        print(json.dumps(resolve(json.load(sys.stdin)), sort_keys=True))
    except Exception as exc:
        sys.stderr.write('resolve: %s\n' % exc)
        sys.exit(1)
