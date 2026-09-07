"""Validate and resolve Marrowfleet's signed authorization history.

The resolver deliberately has no state: a request carries the complete history it
wishes to project.  Validation is complete before any decision is selected.
"""

import datetime
import json
import re
import sys


_EVENT_KEYS = {"authorization", "revision", "subject", "operation", "start", "end", "parameters"}
_COMMIT_KEYS = {"batch", "parts", "sha256"}
_PAGE_KEYS = {"batch", "part", "events"}
_REVISION = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


class InvalidRequest(ValueError):
    """The request or its complete authorization history is invalid."""


def _error(message):
    raise InvalidRequest(message)


def _object(value, where):
    if not isinstance(value, dict):
        _error(where + " must be an object")
    return value


def _keys(value, expected, where):
    _object(value, where)
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        _error(where + " has wrong keys (missing=" + repr(missing) + ", extra=" + repr(extra) + ")")


def _nonempty_string(value, where):
    if not isinstance(value, str) or not value:
        _error(where + " must be a nonempty string")
    return value


def _integer(value, where):
    if isinstance(value, bool) or not isinstance(value, int):
        _error(where + " must be an integer")
    return value


def _date(value, where):
    if not isinstance(value, str):
        _error(where + " must be an ISO date")
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError:
        _error(where + " is not an ISO date")
    if parsed.isoformat() != value:
        _error(where + " is not formatted as YYYY-MM-DD")
    return parsed


def _string_list(value, where):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _error(where + " must be a list of strings")
    return value


def _validate_event(event, where):
    _keys(event, _EVENT_KEYS, where)
    _nonempty_string(event["authorization"], where + ".authorization")
    revision = _nonempty_string(event["revision"], where + ".revision")
    if not _REVISION.fullmatch(revision):
        _error(where + ".revision must be a canonical nonnegative decimal")
    _nonempty_string(event["subject"], where + ".subject")
    if event["operation"] not in ("grant", "withdraw"):
        _error(where + ".operation must be grant or withdraw")
    start = _date(event["start"], where + ".start")
    end = _date(event["end"], where + ".end")
    if start >= end:
        _error(where + " must have start strictly before end")
    _string_list(event["parameters"], where + ".parameters")
    return event


def _revision_less(left, right):
    # Canonical decimal strings compare safely by length then lexically.
    return (len(left), left) < (len(right), right)


def _canonical_events_bytes(events):
    text = json.dumps(events, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def _events_delivery(request):
    _keys(request, {"now", "subjects", "events"}, "request")
    events = request["events"]
    if not isinstance(events, list):
        _error("request.events must be a list")
    for index, event in enumerate(events):
        _validate_event(event, "event[" + str(index) + "]")
    return events


def _transport_delivery(request):
    _keys(request, {"now", "subjects", "transport"}, "request")
    transport = _object(request["transport"], "request.transport")
    _keys(transport, {"commits", "pages"}, "request.transport")

    raw_commits = transport["commits"]
    raw_pages = transport["pages"]
    if not isinstance(raw_commits, list):
        _error("request.transport.commits must be a list")
    if not isinstance(raw_pages, list):
        _error("request.transport.pages must be a list")

    commits = {}
    for index, commit in enumerate(raw_commits):
        where = "commit[" + str(index) + "]"
        _keys(commit, _COMMIT_KEYS, where)
        batch = _nonempty_string(commit["batch"], where + ".batch")
        parts = _integer(commit["parts"], where + ".parts")
        digest = _nonempty_string(commit["sha256"], where + ".sha256")
        if parts < 1:
            _error(where + ".parts must be positive")
        if not _SHA256.fullmatch(digest):
            _error(where + ".sha256 must be 64 lowercase hexadecimal digits")
        prior = commits.get(batch)
        if prior is not None and prior != commit:
            _error("conflicting replay for commit batch " + repr(batch))
        commits[batch] = commit

    pages = {}
    for index, page in enumerate(raw_pages):
        where = "page[" + str(index) + "]"
        _keys(page, _PAGE_KEYS, where)
        batch = _nonempty_string(page["batch"], where + ".batch")
        part = _integer(page["part"], where + ".part")
        if part < 0:
            _error(where + ".part must be nonnegative")
        if not isinstance(page["events"], list):
            _error(where + ".events must be a list")
        for event_index, event in enumerate(page["events"]):
            _validate_event(event, where + ".events[" + str(event_index) + "]")
        identity = (batch, part)
        prior = pages.get(identity)
        if prior is not None and prior != page:
            _error("conflicting replay for page " + repr(identity))
        pages[identity] = page

    history = []
    import hashlib
    for batch in sorted(commits):
        commit = commits[batch]
        expected_parts = commit["parts"]
        assembled = []
        for part in range(expected_parts):
            page = pages.pop((batch, part), None)
            if page is None:
                _error("missing part " + str(part) + " for commit batch " + repr(batch))
            assembled.extend(page["events"])
        digest = hashlib.sha256(_canonical_events_bytes(assembled)).hexdigest()
        if digest != commit["sha256"].lower():
            _error("digest mismatch for commit batch " + repr(batch))
        history.extend(assembled)

    if pages:
        _error("uncommitted pages remain: " + repr(sorted(pages)))
    return history


def resolve(request):
    """Return the approved parameters for each requested subject, or None."""
    request = _object(request, "request")
    if "events" in request and "transport" in request:
        _error("request must carry either events or transport, not both")
    if "events" not in request and "transport" not in request:
        _error("request must carry events or transport")

    now_text = request.get("now")
    now = _date(now_text, "request.now")
    subjects = request.get("subjects")
    if not isinstance(subjects, list):
        _error("request.subjects must be a list")
    requested = []
    for index, subject in enumerate(subjects):
        name = _nonempty_string(subject, "request.subjects[" + str(index) + "]")
        requested.append(name)

    if "events" in request:
        history = _events_delivery(request)
    else:
        history = _transport_delivery(request)

    # A revision belongs to its authorization.  Equal revisions must be the
    # identical event; replay can occur anywhere in the delivery.
    winners = {}
    revisions = {}
    for event in history:
        authorization = event["authorization"]
        revision = event["revision"]
        prior_revision = revisions.get((authorization, revision))
        if prior_revision is not None and event != prior_revision:
            _error("conflicting revision for authorization " + repr(authorization))
        revisions[(authorization, revision)] = event
        prior = winners.get(authorization)
        if prior is None or _revision_less(prior["revision"], revision):
            winners[authorization] = event

    active = {}
    for authorization in sorted(winners):
        event = winners[authorization]
        if event["operation"] != "grant":
            continue
        if not (event["start"] <= now_text < event["end"]):
            continue
        subject = event["subject"]
        if subject in active:
            _error("multiple active authorizations for subject " + repr(subject))
        active[subject] = event["parameters"]

    return {subject: active.get(subject) for subject in requested}


def _main():
    try:
        request = json.load(sys.stdin)
        result = resolve(request)
    except Exception as exc:
        sys.stderr.write("policy-resolve: " + str(exc) + "\n")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
