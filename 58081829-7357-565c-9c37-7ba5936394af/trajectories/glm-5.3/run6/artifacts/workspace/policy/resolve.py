#!/usr/bin/env python3
"""Resolve the current signed authorization snapshot.

The request is either an events-only history or a committed, segmented delivery.
Validation is deliberately complete before any projection is returned.
"""

import datetime
import hashlib
import json
import re
import sys


_EVENT_KEYS = frozenset(
    {"authorization", "revision", "subject", "operation", "start", "end", "parameters"}
)
_COMMIT_KEYS = frozenset({"batch", "parts", "sha256"})
_PAGE_KEYS = frozenset({"batch", "part", "events"})
_REVISION = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


class InvalidSnapshot(ValueError):
    """The authorization request is not internally consistent."""


def _string(value, label):
    if not isinstance(value, str):
        raise InvalidSnapshot("%s must be a string" % label)
    return value


def _identifier(value, label):
    _string(value, label)
    if not value:
        raise InvalidSnapshot("%s must not be empty" % label)
    return value


def _date(value, label):
    _string(value, label)
    if not _DATE.fullmatch(value):
        raise InvalidSnapshot("%s must be an ISO YYYY-MM-DD date" % label)
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidSnapshot("%s is not a valid date" % label) from exc


def _subjects(value):
    if not isinstance(value, list):
        raise InvalidSnapshot("subjects must be a list")
    result = []
    for index, subject in enumerate(value):
        _identifier(subject, "subjects[%d]" % index)
        if subject in result:
            raise InvalidSnapshot("subjects contains %s more than once" % subject)
        result.append(subject)
    return result


def _validate_event(event):
    if not isinstance(event, dict):
        raise InvalidSnapshot("each event must be an object")
    keys = frozenset(event)
    if keys != _EVENT_KEYS:
        missing = sorted(_EVENT_KEYS - keys)
        extra = sorted(keys - _EVENT_KEYS)
        raise InvalidSnapshot(
            "event keys are invalid (missing=%s, extra=%s)" % (missing, extra)
        )

    _identifier(event["authorization"], "event.authorization")
    _identifier(event["subject"], "event.subject")

    revision = event["revision"]
    _string(revision, "event.revision")
    if not _REVISION.fullmatch(revision):
        raise InvalidSnapshot("event.revision must be a canonical nonnegative integer")

    if event["operation"] not in ("grant", "withdraw"):
        raise InvalidSnapshot("event.operation must be grant or withdraw")

    start = _date(event["start"], "event.start")
    end = _date(event["end"], "event.end")
    if start >= end:
        raise InvalidSnapshot("event.start must be before event.end")

    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(
        isinstance(parameter, str) for parameter in parameters
    ):
        raise InvalidSnapshot("event.parameters must be a list of strings")
    return event


def _events_only(request):
    if "transport" in request:
        raise InvalidSnapshot("request cannot carry both events and transport")
    events = request.get("events")
    if not isinstance(events, list):
        raise InvalidSnapshot("events must be a list")
    return [_validate_event(event) for event in events]


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidSnapshot("%s must be a positive integer" % label)
    return value


def _nonnegative_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidSnapshot("%s must be a nonnegative integer" % label)
    return value


def _transport(transport):
    if not isinstance(transport, dict) or "commits" not in transport or "pages" not in transport:
        raise InvalidSnapshot("transport must carry commits and pages")
    commits = transport["commits"]
    pages = transport["pages"]
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise InvalidSnapshot("transport commits and pages must be lists")

    # Commits and pages may be replayed. Equal replays collapse; unequal
    # replays of the same identity invalidate the whole delivery.
    commit_by_batch = {}
    for index, commit in enumerate(commits):
        if not isinstance(commit, dict):
            raise InvalidSnapshot("commits[%d] must be an object" % index)
        if frozenset(commit) != _COMMIT_KEYS:
            raise InvalidSnapshot("commits[%d] has invalid keys" % index)
        batch = _identifier(commit["batch"], "commits[%d].batch" % index)
        _positive_integer(commit["parts"], "commits[%d].parts" % index)
        digest = _string(commit["sha256"], "commits[%d].sha256" % index)
        if not _SHA256.fullmatch(digest):
            raise InvalidSnapshot("commits[%d].sha256 is not a SHA-256 digest" % index)
        previous = commit_by_batch.get(batch)
        if previous is not None and previous != commit:
            raise InvalidSnapshot("conflicting replay of commit batch %s" % batch)
        commit_by_batch[batch] = commit

    page_by_identity = {}
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise InvalidSnapshot("pages[%d] must be an object" % index)
        if frozenset(page) != _PAGE_KEYS:
            raise InvalidSnapshot("pages[%d] has invalid keys" % index)
        batch = _identifier(page["batch"], "pages[%d].batch" % index)
        _nonnegative_integer(page["part"], "pages[%d].part" % index)
        if not isinstance(page["events"], list):
            raise InvalidSnapshot("pages[%d].events must be a list" % index)
        identity = (batch, page["part"])
        previous = page_by_identity.get(identity)
        if previous is not None and previous != page:
            raise InvalidSnapshot("conflicting replay of page %s part %d" % identity)
        page_by_identity[identity] = page

    for (batch, part), page in page_by_identity.items():
        if batch not in commit_by_batch:
            raise InvalidSnapshot("page %s is not committed" % batch)
        if part >= commit_by_batch[batch]["parts"]:
            raise InvalidSnapshot(
                "batch %s has extra part %d" % (batch, part)
            )

    history = []
    for batch in commit_by_batch:
        commit = commit_by_batch[batch]
        expected_parts = commit["parts"]
        assembled = []
        for part in range(expected_parts):
            page = page_by_identity.get((batch, part))
            if page is None:
                raise InvalidSnapshot(
                    "batch %s is missing part %d of %d" % (batch, part, expected_parts)
                )
            assembled.extend(page["events"])
        raw = json.dumps(
            assembled, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != commit["sha256"].lower():
            raise InvalidSnapshot("batch %s does not match its SHA-256 commit" % batch)
        history.extend(assembled)

    return [_validate_event(event) for event in history]


def _corrected_snapshot(events):
    """Return the highest revision of each authorization."""
    by_revision = {}
    for event in events:
        identity = (event["authorization"], int(event["revision"]))
        previous = by_revision.get(identity)
        if previous is not None and previous != event:
            raise InvalidSnapshot(
                "conflicting revision %s of authorization %s"
                % (event["revision"], event["authorization"])
            )
        by_revision[identity] = event

    authorizations = {identity[0] for identity in by_revision}
    winners = []
    for authorization in authorizations:
        revisions = [
            identity[1] for identity in by_revision if identity[0] == authorization
        ]
        winners.append(by_revision[(authorization, max(revisions))])
    return winners


def resolve(request):
    if not isinstance(request, dict):
        raise InvalidSnapshot("request must be an object")
    now = _date(request.get("now"), "request.now")
    subjects = _subjects(request.get("subjects"))

    if "events" in request:
        history = _events_only(request)
    elif "transport" in request:
        history = _transport(request["transport"])
    else:
        raise InvalidSnapshot("request must carry events or transport")

    winners = _corrected_snapshot(history)
    active = {}
    for event in winners:
        if event["operation"] != "grant":
            continue
        if not (event["start"] <= now.isoformat() < event["end"]):
            continue
        subject = event["subject"]
        if subject in active:
            raise InvalidSnapshot(
                "multiple active authorizations for subject %s" % subject
            )
        active[subject] = event

    return {
        subject: active[subject]["parameters"] if subject in active else None
        for subject in subjects
    }


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        result = resolve(request)
    except (json.JSONDecodeError, InvalidSnapshot, TypeError, ValueError) as exc:
        print("policy resolver: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
