"""Resolve a signed authorization snapshot.

The request is either an events-only history or a segmented transport.  The
projection is deliberately kept separate from validation: no decision is
returned unless the complete history is admissible.
"""
import datetime
import hashlib
import json
import re
import sys


_EVENT_KEYS = {
    "authorization", "revision", "subject", "operation", "start", "end",
    "parameters",
}
_COMMIT_KEYS = {"batch", "parts", "sha256"}
_PAGE_KEYS = {"batch", "part", "events"}
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_REVISION_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class PolicyError(ValueError):
    """The authorization request is internally inconsistent or malformed."""


def _object(value, label):
    if not isinstance(value, dict):
        raise PolicyError("%s must be an object" % label)
    return value


def _keys(value, expected, label):
    _object(value, label)
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise PolicyError("%s has invalid keys (missing %s, extra %s)"
                          % (label, missing, extra))


def _string(value, label):
    if not isinstance(value, str) or not value:
        raise PolicyError("%s must be a nonempty string" % label)
    return value


def _date(value, label):
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise PolicyError("%s must be an ISO YYYY-MM-DD date" % label)
    try:
        datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise PolicyError("%s is not a valid date" % label) from exc
    return value


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PolicyError("%s must be a positive integer" % label)
    return value


def _nonnegative_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PolicyError("%s must be a nonnegative integer" % label)
    return value


def _validate_event(value, number):
    event = _object(value, "event %d" % number)
    _keys(event, _EVENT_KEYS, "event %d" % number)
    _string(event["authorization"], "event %d authorization" % number)

    revision = event["revision"]
    if (not isinstance(revision, str)
            or not _REVISION_RE.fullmatch(revision)):
        raise PolicyError("event %d has a non-canonical revision" % number)

    _string(event["subject"], "event %d subject" % number)
    if event["operation"] not in ("grant", "withdraw"):
        raise PolicyError("event %d has an invalid operation" % number)

    start = _date(event["start"], "event %d start" % number)
    end = _date(event["end"], "event %d end" % number)
    if start >= end:
        raise PolicyError("event %d start is not before end" % number)

    parameters = event["parameters"]
    if (not isinstance(parameters, list)
            or not all(isinstance(item, str) for item in parameters)):
        raise PolicyError("event %d has invalid parameters" % number)
    return event


def _transport_events(transport):
    transport = _object(transport, "transport")
    _keys(transport, {"commits", "pages"}, "transport")

    raw_commits = transport["commits"]
    if not isinstance(raw_commits, list):
        raise PolicyError("transport commits must be an array")
    commits = {}
    for number, value in enumerate(raw_commits, 1):
        label = "commit %d" % number
        commit = _object(value, label)
        _keys(commit, _COMMIT_KEYS, label)
        batch = _string(commit["batch"], "%s batch" % label)
        _positive_integer(commit["parts"], "%s parts" % label)
        digest = _string(commit["sha256"], "%s sha256" % label)
        if not _SHA256_RE.fullmatch(digest):
            raise PolicyError("%s sha256 is not a lowercase SHA-256 digest"
                              % label)
        prior = commits.get(batch)
        if prior is not None and prior != commit:
            raise PolicyError("conflicting replay of commit %r" % batch)
        if prior is None:
            commits[batch] = commit

    raw_pages = transport["pages"]
    if not isinstance(raw_pages, list):
        raise PolicyError("transport pages must be an array")
    pages = {}
    for number, value in enumerate(raw_pages, 1):
        label = "page %d" % number
        page = _object(value, label)
        _keys(page, _PAGE_KEYS, label)
        batch = _string(page["batch"], "%s batch" % label)
        part = _nonnegative_integer(page["part"], "%s part" % label)
        if not isinstance(page["events"], list):
            raise PolicyError("%s events must be an array" % label)
        for event_number, event in enumerate(page["events"], 1):
            _validate_event(event, event_number)
        key = (batch, part)
        prior = pages.get(key)
        if prior is not None and prior != page:
            raise PolicyError("conflicting replay of page %r part %d"
                              % (batch, part))
        if prior is None:
            pages[key] = page

    uncommitted = sorted({batch for batch, _ in pages} - set(commits))
    if uncommitted:
        raise PolicyError("uncommitted page batch %r" % uncommitted[0])

    history = []
    for batch in sorted(commits):
        commit = commits[batch]
        expected_parts = set(range(commit["parts"]))
        actual_parts = {part for page_batch, part in pages
                        if page_batch == batch}
        if actual_parts != expected_parts:
            missing = sorted(expected_parts - actual_parts)
            extra = sorted(actual_parts - expected_parts)
            raise PolicyError("batch %r has missing parts %s or extra parts %s"
                              % (batch, missing, extra))
        events = []
        for part in range(commit["parts"]):
            events.extend(pages[(batch, part)]["events"])
        encoded = json.dumps(events, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
        actual_digest = hashlib.sha256(encoded).hexdigest()
        if actual_digest != commit["sha256"]:
            raise PolicyError("batch %r digest mismatch" % batch)
        history.extend(events)
    return history


def _events_events(value):
    if not isinstance(value, list):
        raise PolicyError("events must be an array")
    for number, event in enumerate(value, 1):
        _validate_event(event, number)
    return value


def resolve(request):
    """Return the approved parameters, or null, for each requested subject."""
    request = _object(request, "request")
    supplied = set(request)
    if "transport" in request:
        if "events" in request:
            raise PolicyError("request cannot carry both events and transport")
        expected = {"now", "subjects", "transport"}
    else:
        expected = {"now", "subjects", "events"}
    if supplied != expected:
        raise PolicyError("request has invalid keys")

    now = _date(request["now"], "now")
    subjects = request["subjects"]
    if not isinstance(subjects, list):
        raise PolicyError("subjects must be an array")
    for number, subject in enumerate(subjects, 1):
        _string(subject, "subject %d" % number)

    if "transport" in request:
        history = _transport_events(request["transport"])
    else:
        history = _events_events(request["events"])

    # Equal revisions are replay only when identical.  The greatest numeric
    # revision of each authorization replaces all of its earlier revisions.
    revisions = {}
    for event in history:
        key = (event["authorization"], int(event["revision"]))
        prior = revisions.get(key)
        if prior is not None and prior != event:
            raise PolicyError("conflicting revision %s of authorization %r"
                              % (event["revision"], event["authorization"]))
        if prior is None:
            revisions[key] = event

    winners = {}
    for (_, revision), event in revisions.items():
        authorization = event["authorization"]
        old = winners.get(authorization)
        if old is None or revision > int(old["revision"]):
            winners[authorization] = event

    active = {}
    for event in winners.values():
        if (event["operation"] == "grant"
                and event["start"] <= now < event["end"]):
            subject = event["subject"]
            if subject in active:
                raise PolicyError("multiple active authorizations for subject %r"
                                  % subject)
            active[subject] = event

    return {subject: active[subject]["parameters"] if subject in active else None
            for subject in subjects}


def _main():
    try:
        request = json.load(sys.stdin)
        result = resolve(request)
    except (json.JSONDecodeError, PolicyError, TypeError, ValueError) as exc:
        sys.stderr.write("policy resolver: %s\n" % exc)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
