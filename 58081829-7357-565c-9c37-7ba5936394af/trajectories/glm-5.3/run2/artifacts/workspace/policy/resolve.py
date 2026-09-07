"""Resolve the signed authorization snapshot for the roster release.

The adapter validates a candidate completely before returning a projection.  It
never uses host time and never edits the protected authorization feed.
"""

import datetime as _datetime
import json
import re
import sys


_EVENT_KEYS = frozenset({
    "authorization", "revision", "subject", "operation", "start", "end",
    "parameters",
})
_COMMIT_KEYS = frozenset({"batch", "parts", "sha256"})
_PAGE_KEYS = frozenset({"batch", "part", "events"})
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_REVISION_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class ResolutionError(ValueError):
    """A candidate history or request is invalid."""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _identifier(value, what):
    if not isinstance(value, str) or not value:
        raise ResolutionError("%s must be a nonempty string" % what)


def _date(value, what):
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ResolutionError("%s must be an ISO YYYY-MM-DD date" % what)
    try:
        _datetime.date.fromisoformat(value)
    except ValueError:
        raise ResolutionError("%s is not a valid calendar date" % what) from None


def _revision_key(value):
    # Canonical decimal strings can be compared without conversion and without
    # imposing Python's int-to-str digit limit on signed delivery histories.
    return len(value), value


def _validate_event(event):
    if not isinstance(event, dict) or frozenset(event) != _EVENT_KEYS:
        raise ResolutionError("event keys are invalid")
    _identifier(event["authorization"], "authorization")
    _identifier(event["subject"], "subject")
    revision = event["revision"]
    if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
        raise ResolutionError("revision must be a canonical nonnegative decimal string")
    if event["operation"] not in ("grant", "withdraw"):
        raise ResolutionError("operation must be grant or withdraw")
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(
            isinstance(item, str) for item in parameters):
        raise ResolutionError("parameters must be a list of strings")
    _date(event["start"], "start")
    _date(event["end"], "end")
    if event["start"] >= event["end"]:
        raise ResolutionError("start must be before end")


def _transport_events(transport):
    """Validate segmented delivery and return its complete event history."""
    if not isinstance(transport, dict) or frozenset(transport) != {
            "commits", "pages"}:
        raise ResolutionError("transport must contain commits and pages")
    commits = transport["commits"]
    pages = transport["pages"]
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise ResolutionError("transport commits and pages must be arrays")

    committed = {}
    for commit in commits:
        if not isinstance(commit, dict) or frozenset(commit) != _COMMIT_KEYS:
            raise ResolutionError("commit keys are invalid")
        _identifier(commit["batch"], "batch")
        if not _is_int(commit["parts"]) or commit["parts"] < 1:
            raise ResolutionError("commit parts must be a positive integer")
        digest = commit["sha256"]
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ResolutionError("commit sha256 must be a lowercase hexadecimal digest")
        old = committed.get(commit["batch"])
        if old is not None and old != commit:
            raise ResolutionError("conflicting replay of committed batch %s" % commit["batch"])
        committed[commit["batch"]] = commit

    delivered = {}
    for page in pages:
        if not isinstance(page, dict) or frozenset(page) != _PAGE_KEYS:
            raise ResolutionError("page keys are invalid")
        _identifier(page["batch"], "batch")
        if not _is_int(page["part"]) or page["part"] < 0:
            raise ResolutionError("page part must be a zero-based integer")
        if not isinstance(page["events"], list):
            raise ResolutionError("page events must be an array")
        key = (page["batch"], page["part"])
        old = delivered.get(key)
        if old is not None and old != page:
            raise ResolutionError("conflicting replay of page %s part %d" % key)
        delivered[key] = page

    history = []
    for batch, commit in committed.items():
        # Each commit is a delta: its digest covers only its own ordered parts.
        events = []
        selected = []
        for part in range(commit["parts"]):
            page = delivered.pop((batch, part), None)
            if page is None:
                raise ResolutionError(
                    "committed batch %s is missing part %d" % (batch, part))
            selected.append(page)
        for page in selected:
            events.extend(page["events"])
        encoded = json.dumps(
            events, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        import hashlib
        if hashlib.sha256(encoded).hexdigest() != commit["sha256"]:
            raise ResolutionError("committed batch %s has a mismatching digest" % batch)
        history.extend(events)

    if delivered:
        batch, part = sorted(delivered, key=lambda item: (item[0], item[1]))[0]
        raise ResolutionError("uncommitted page %s part %d" % (batch, part))
    return history


def resolve(request):
    """Return the approved parameter projection for a validated request."""
    if not isinstance(request, dict):
        raise ResolutionError("request must be an object")
    if "transport" in request and "events" in request:
        raise ResolutionError("request cannot carry both events and transport")

    now = request.get("now")
    _date(now, "now")
    subjects = request.get("subjects")
    if not isinstance(subjects, list):
        raise ResolutionError("subjects must be an array")
    for subject in subjects:
        _identifier(subject, "subject")
    if len(set(subjects)) != len(subjects):
        raise ResolutionError("subjects must not be repeated")

    if "transport" in request:
        events = _transport_events(request["transport"])
    else:
        events = request.get("events")
        if not isinstance(events, list):
            raise ResolutionError("events must be an array")

    # Validate every delivered record before resolving or projecting any of it.
    for event in events:
        _validate_event(event)

    # A revision belongs to its authorization.  Repeated identical records are
    # idempotent; equal revisions differing in any field invalidate history.
    winners = {}
    revisions = {}
    for event in events:
        authorization = event["authorization"]
        revision = event["revision"]
        identities = revisions.setdefault(authorization, {})
        previous = identities.get(revision)
        if previous is not None and previous != event:
            raise ResolutionError(
                "conflicting revision %s for authorization %s"
                % (revision, authorization))
        identities[revision] = event
        old = winners.get(authorization)
        if old is None or _revision_key(revision) > _revision_key(old["revision"]):
            winners[authorization] = event

    active = {}
    for authorization, event in sorted(winners.items()):
        if event["operation"] != "grant":
            continue
        if not (event["start"] <= now < event["end"]):
            continue
        subject = event["subject"]
        if subject in active:
            raise ResolutionError(
                "subject %s has multiple active authorizations" % subject)
        active[subject] = event

    return {
        subject: active[subject]["parameters"] if subject in active else None
        for subject in subjects
    }


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        result = resolve(request)
    except (ResolutionError, ValueError, TypeError, KeyError) as exc:
        sys.stderr.write("policy resolver: %s\n" % exc)
        raise SystemExit(2)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
