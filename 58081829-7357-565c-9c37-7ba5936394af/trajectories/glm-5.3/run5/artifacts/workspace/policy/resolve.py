"""Resolve the bureau authorization snapshot for the release controller.

The request is either the original events-only form or a paged transport.  The
resolver deliberately validates the complete history before projecting it: a
bad page or superseded event makes the whole candidate inadmissible.
"""

import datetime
import hashlib
import json
import re
import sys

EVENT_KEYS = frozenset(
    {"authorization", "revision", "subject", "operation", "start", "end", "parameters"}
)
COMMIT_KEYS = frozenset({"batch", "parts", "sha256"})
PAGE_KEYS = frozenset({"batch", "part", "events"})
CANONICAL_REVISION = re.compile(r"(?:0|[1-9][0-9]*)\Z")
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ResolutionError(ValueError):
    """The authorization request is internally invalid."""


def _mapping(value, label):
    if type(value) is not dict:
        raise ResolutionError("%s must be an object" % label)
    return value


def _exact_keys(value, expected, label):
    _mapping(value, label)
    if frozenset(value) != expected:
        raise ResolutionError("%s has the wrong keys" % label)


def _nonempty_string(value, label):
    if type(value) is not str or not value:
        raise ResolutionError("%s must be a nonempty string" % label)
    return value


def _date(value, label):
    if type(value) is not str or not ISO_DATE.fullmatch(value):
        raise ResolutionError("%s must be an ISO YYYY-MM-DD date" % label)
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise ResolutionError("%s is not a valid date" % label) from exc
    if parsed.isoformat() != value:
        raise ResolutionError("%s is not a canonical date" % label)
    return parsed


def _validate_event(event):
    _exact_keys(event, EVENT_KEYS, "event")
    _nonempty_string(event["authorization"], "authorization")
    _nonempty_string(event["subject"], "subject")

    revision = event["revision"]
    if type(revision) is not str or not CANONICAL_REVISION.fullmatch(revision):
        raise ResolutionError("revision must be a canonical nonnegative decimal string")

    if event["operation"] not in ("grant", "withdraw"):
        raise ResolutionError("operation must be grant or withdraw")

    parameters = event["parameters"]
    if type(parameters) is not list or any(type(item) is not str for item in parameters):
        raise ResolutionError("parameters must be a list of strings")

    start = _date(event["start"], "start")
    end = _date(event["end"], "end")
    if start >= end:
        raise ResolutionError("start must be before end")


def _revision_order(revision):
    # Canonical decimal strings of unequal length compare numerically by length.
    return (len(revision), revision)


def _history_from_transport(request):
    transport = request.get("transport")
    _exact_keys(transport, frozenset({"commits", "pages"}), "transport")

    raw_commits = transport["commits"]
    raw_pages = transport["pages"]
    if type(raw_commits) is not list or type(raw_pages) is not list:
        raise ResolutionError("commits and pages must be arrays")

    commits = {}
    for commit in raw_commits:
        _exact_keys(commit, COMMIT_KEYS, "commit")
        batch = _nonempty_string(commit["batch"], "commit batch")
        parts = commit["parts"]
        digest = commit["sha256"]
        if type(parts) is not int or parts < 1:
            raise ResolutionError("commit parts must be a positive integer")
        if type(digest) is not str or not SHA256.fullmatch(digest):
            raise ResolutionError("commit sha256 must be lowercase hexadecimal SHA-256")
        previous = commits.get(batch)
        if previous is not None and previous != commit:
            raise ResolutionError("conflicting replay of commit %r" % batch)
        commits[batch] = commit

    pages = {}
    for page in raw_pages:
        _exact_keys(page, PAGE_KEYS, "page")
        batch = _nonempty_string(page["batch"], "page batch")
        part = page["part"]
        events = page["events"]
        if type(part) is not int or part < 0:
            raise ResolutionError("page part must be a zero-based integer")
        if type(events) is not list:
            raise ResolutionError("page events must be an array")
        identity = (batch, part)
        previous = pages.get(identity)
        if previous is not None and previous != page:
            raise ResolutionError("conflicting replay of page %r part %d" % (batch, part))
        pages[identity] = page

    history = []
    for batch, commit in commits.items():
        expected_parts = range(commit["parts"])
        for part in expected_parts:
            if (batch, part) not in pages:
                raise ResolutionError("batch %r is missing part %d" % (batch, part))
        for identity in pages:
            if identity[0] == batch and identity[1] not in expected_parts:
                raise ResolutionError("batch %r has an extra part %d" % identity)

        events = []
        for part in expected_parts:
            events.extend(pages[(batch, part)]["events"])
        for event in events:
            _validate_event(event)

        encoded = json.dumps(
            events, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != commit["sha256"]:
            raise ResolutionError("batch %r digest mismatch" % batch)
        history.extend(events)

    for identity in pages:
        if identity[0] not in commits:
            raise ResolutionError("page %r part %d is uncommitted" % identity)

    return history


def resolve(request):
    """Return the approved parameter projection for a validated request."""
    _mapping(request, "request")
    now = _date(request.get("now"), "now")

    subjects = request.get("subjects")
    if type(subjects) is not list:
        raise ResolutionError("subjects must be an array")
    for subject in subjects:
        _nonempty_string(subject, "subject")
    if len(subjects) != len(set(subjects)):
        raise ResolutionError("subjects must not repeat")

    if "transport" in request:
        if "events" in request:
            raise ResolutionError("transport must be used instead of events")
        history = _history_from_transport(request)
    else:
        history = request.get("events")
        if type(history) is not list:
            raise ResolutionError("events must be an array")
        for event in history:
            _validate_event(event)

    # An authorization revision is identified by authorization and revision,
    # not by subject or delivery batch.  Equal conflicting records are invalid
    # even when a later revision would otherwise supersede both.
    by_revision = {}
    for event in history:
        identity = (event["authorization"], event["revision"])
        previous = by_revision.get(identity)
        if previous is not None and previous != event:
            raise ResolutionError(
                "conflicting revision %s for authorization %r"
                % (event["revision"], event["authorization"])
            )
        by_revision[identity] = event

    latest = {}
    for (authorization, revision), event in by_revision.items():
        old = latest.get(authorization)
        if old is None or _revision_order(revision) > _revision_order(old["revision"]):
            latest[authorization] = event

    active = {}
    for event in latest.values():
        if event["operation"] != "grant":
            continue
        if not (event["start"] <= now.isoformat() < event["end"]):
            continue
        subject = event["subject"]
        if subject in active:
            raise ResolutionError("multiple active authorizations for subject %r" % subject)
        active[subject] = event["parameters"]

    return {subject: active.get(subject) for subject in subjects}


def _main():
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ResolutionError("duplicate JSON object key %r" % key)
            value[key] = item
        return value

    try:
        request = json.load(sys.stdin, object_pairs_hook=unique_object)
        result = resolve(request)
    except (ResolutionError, json.JSONDecodeError) as exc:
        sys.stderr.write("policy resolver: %s\n" % exc)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
