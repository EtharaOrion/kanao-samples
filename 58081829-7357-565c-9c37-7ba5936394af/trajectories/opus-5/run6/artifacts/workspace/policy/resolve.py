"""Resolve the current signed authorization snapshot.

The contract is docs/policy-reconciliation.md:

  * a request carries either ``events`` or a segmented ``transport``;
  * every event in the complete history must be valid, including superseded,
    withdrawn, out-of-window and unrequested-subject records;
  * a higher numeric revision fully replaces an earlier revision of the same
    authorization, including its subject and effective interval;
  * a winning withdrawal contributes no active decision;
  * a grant applies only within start <= now < end;
  * multiple active authorizations for one subject are an error;
  * complete validation precedes projection, and an invalid history raises
    with no partial result.
"""
import hashlib
import json
import re
import sys

EVENT_KEYS = {
    "authorization",
    "revision",
    "subject",
    "operation",
    "start",
    "end",
    "parameters",
}

REVISION = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
DATE = re.compile(r"\A([0-9]{4})-([0-9]{2})-([0-9]{2})\Z")


class PolicyError(ValueError):
    """The request is not a valid authorization history."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _date(value, field):
    if not isinstance(value, str):
        raise PolicyError("%s must be a string date" % field)
    match = DATE.match(value)
    if match is None:
        raise PolicyError("%s is not an ISO YYYY-MM-DD date: %r" % (field, value))
    year, month, day = (int(part) for part in match.groups())
    if not 1 <= month <= 12:
        raise PolicyError("%s has no such month: %r" % (field, value))
    if not 1 <= day <= _days_in_month(year, month):
        raise PolicyError("%s has no such day: %r" % (field, value))
    return value


def _days_in_month(year, month):
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _identifier(value, field):
    if not isinstance(value, str) or not value:
        raise PolicyError("%s must be a nonempty string" % field)
    return value


def _validate_event(event):
    """Validate one record of the history and return it unchanged."""
    if not isinstance(event, dict):
        raise PolicyError("an event must be an object")
    keys = set(event)
    if keys != EVENT_KEYS:
        missing = sorted(EVENT_KEYS - keys)
        extra = sorted(keys - EVENT_KEYS)
        raise PolicyError("event keys are exactly %s (missing %s, unexpected %s)"
                          % (sorted(EVENT_KEYS), missing, extra))
    _identifier(event["authorization"], "authorization")
    _identifier(event["subject"], "subject")
    revision = event["revision"]
    if not isinstance(revision, str) or REVISION.match(revision) is None:
        raise PolicyError("revision must be a canonical nonnegative decimal"
                          " string: %r" % (revision,))
    if event["operation"] not in ("grant", "withdraw"):
        raise PolicyError("operation must be grant or withdraw: %r"
                          % (event["operation"],))
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(
            isinstance(item, str) for item in parameters):
        raise PolicyError("parameters must be a list of strings")
    start = _date(event["start"], "start")
    end = _date(event["end"], "end")
    if not start < end:
        raise PolicyError("start must be strictly before end: %s..%s"
                          % (start, end))
    return event


def _assemble(transport):
    """Reassemble the segmented delivery into one ordered event history."""
    if not isinstance(transport, dict):
        raise PolicyError("transport must be an object")
    if set(transport) - {"commits", "pages"}:
        raise PolicyError("transport carries unexpected keys: %s"
                          % sorted(set(transport) - {"commits", "pages"}))
    commits = transport.get("commits", [])
    pages = transport.get("pages", [])
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise PolicyError("transport commits and pages must be lists")

    declared = {}
    for commit in commits:
        if not isinstance(commit, dict) or set(commit) != {"batch", "parts",
                                                           "sha256"}:
            raise PolicyError("a commit carries exactly batch, parts, sha256")
        batch = _identifier(commit["batch"], "commit batch")
        parts = commit["parts"]
        if isinstance(parts, bool) or not isinstance(parts, int) or parts < 1:
            raise PolicyError("commit parts must be a positive integer: %r"
                              % (parts,))
        digest = commit["sha256"]
        if not isinstance(digest, str) or not re.match(r"\A[0-9a-fA-F]{64}\Z",
                                                       digest):
            raise PolicyError("commit sha256 must be a hex digest: %r"
                              % (digest,))
        digest = digest.lower()
        previous = declared.get(batch)
        if previous is None:
            declared[batch] = (parts, digest)
        elif previous != (parts, digest):
            # Identical replay is idempotent; a conflicting replay is not.
            raise PolicyError("conflicting commit replay for batch %r" % batch)

    delivered = {}
    for page in pages:
        if not isinstance(page, dict) or set(page) != {"batch", "part",
                                                       "events"}:
            raise PolicyError("a page carries exactly batch, part, events")
        batch = _identifier(page["batch"], "page batch")
        part = page["part"]
        if isinstance(part, bool) or not isinstance(part, int) or part < 0:
            raise PolicyError("page part must be a zero-based integer: %r"
                              % (part,))
        events = page["events"]
        if not isinstance(events, list):
            raise PolicyError("page events must be a list")
        if batch not in declared:
            raise PolicyError("page for uncommitted batch %r" % batch)
        if part >= declared[batch][0]:
            raise PolicyError("batch %r commits %d parts, page part %d is extra"
                              % (batch, declared[batch][0], part))
        seen = delivered.setdefault(batch, {})
        if part in seen:
            if _canonical(seen[part]) != _canonical(events):
                raise PolicyError("conflicting replay of batch %r part %d"
                                  % (batch, part))
            continue
        seen[part] = events

    history = []
    for batch in sorted(declared):
        parts, digest = declared[batch]
        seen = delivered.get(batch, {})
        missing = [index for index in range(parts) if index not in seen]
        if missing:
            raise PolicyError("batch %r is missing parts %s" % (batch, missing))
        assembled = []
        for index in range(parts):
            assembled.extend(seen[index])
        actual = hashlib.sha256(_canonical(assembled).encode("utf-8")).hexdigest()
        if actual != digest:
            raise PolicyError("batch %r does not match its committed digest"
                              % batch)
        history.extend(assembled)
    return history


def resolve(request):
    """Return the winning parameters per requested subject, or None."""
    if not isinstance(request, dict):
        raise PolicyError("a request must be an object")

    now = _date(request.get("now"), "now")

    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not all(
            isinstance(item, str) and item for item in subjects):
        raise PolicyError("subjects must be a list of nonempty strings")

    has_events = "events" in request
    has_transport = "transport" in request
    if has_events and has_transport:
        raise PolicyError("a request carries events or transport, not both")
    if has_transport:
        history = _assemble(request["transport"])
    elif has_events:
        history = request["events"]
        if not isinstance(history, list):
            raise PolicyError("events must be a list")
    else:
        raise PolicyError("a request carries events or transport")

    # Complete validation precedes projection: every record of the history is
    # validated, superseded, withdrawn, out-of-window and unrequested alike.
    for event in history:
        _validate_event(event)

    # A higher numeric revision fully replaces an earlier revision of the same
    # authorization, including its subject and effective interval.
    # Repeated identical revisions are idempotent; conflicting equal revisions
    # are invalid wherever in the history they appear, so every record of a
    # revision is compared before any revision may win.
    seen = {}
    for event in history:
        key = (event["authorization"], int(event["revision"]))
        shape = _canonical(event)
        if key in seen:
            if seen[key] != shape:
                raise PolicyError("conflicting revision %s of authorization %r"
                                  % (event["revision"], key[0]))
            continue
        seen[key] = shape

    winning = {}
    for (name, revision), shape in seen.items():
        current = winning.get(name)
        if current is None or revision > current[0]:
            winning[name] = (revision, json.loads(shape))

    # A winning withdrawal contributes no active decision; a grant applies only
    # within start <= now < end.
    active = {}
    for name, (_, event) in winning.items():
        if event["operation"] != "grant":
            continue
        if not event["start"] <= now < event["end"]:
            continue
        subject = event["subject"]
        if subject in active:
            raise PolicyError("subject %r carries multiple active"
                              " authorizations (%s and %s)"
                              % (subject, sorted((active[subject][0], name))[0],
                                 sorted((active[subject][0], name))[1]))
        active[subject] = (name, event)

    # Unknown subjects are excluded from the projection after resolving
    # revisions; every requested subject is returned as a key.
    return {subject: (active[subject][1]["parameters"] if subject in active
                      else None)
            for subject in subjects}


def main(argv=None):
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write("resolve: the request is not valid JSON: %s\n" % exc)
        return 2
    try:
        decision = resolve(request)
    except PolicyError as exc:
        sys.stderr.write("resolve: %s\n" % exc)
        return 1
    sys.stdout.write(json.dumps(decision, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
