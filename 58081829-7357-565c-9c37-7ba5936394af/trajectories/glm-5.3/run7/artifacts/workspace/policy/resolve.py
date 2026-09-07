"""Resolve the current signed authorization snapshot.

The request is either an events-only history or a segmented delivery.  All
validation is completed before any projection is attempted, so a malformed
candidate can never produce a partial decision.
"""
import datetime
import hashlib
import json
import re
import sys

EVENT_KEYS = {
    "authorization", "revision", "subject", "operation", "start", "end",
    "parameters",
}
COMMIT_KEYS = {"batch", "parts", "sha256"}
PAGE_KEYS = {"batch", "part", "events"}
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class InvalidRequest(ValueError):
    """The authorization request violates the delivery contract."""


def _exact_int(value, name, minimum=None):
    if type(value) is not int:
        raise InvalidRequest("%s must be an integer" % name)
    if minimum is not None and value < minimum:
        raise InvalidRequest("%s must be at least %d" % (name, minimum))
    return value


def _identifier(value, name):
    if not isinstance(value, str) or not value:
        raise InvalidRequest("%s must be a nonempty string" % name)
    return value


def _date(value, name):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise InvalidRequest("%s must be an ISO YYYY-MM-DD date" % name)
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidRequest("%s is not a valid date" % name) from exc


def _revision(value):
    if not isinstance(value, str) or not value.isdecimal():
        raise InvalidRequest("revision must be a nonnegative decimal string")
    # isdecimal accepts non-ASCII digits, so restrict to the intended alphabet.
    if not re.fullmatch(r"[0-9]+", value):
        raise InvalidRequest("revision must use ASCII decimal digits")
    if len(value) > 1 and value.startswith("0"):
        raise InvalidRequest("revision has a leading zero")
    return value


def _revision_key(value):
    # Canonical decimals can be compared safely without converting arbitrarily
    # long numbers to machine integers.
    return (len(value), value)


def _validate_event(event):
    if not isinstance(event, dict) or set(event) != EVENT_KEYS:
        raise InvalidRequest("each event must have exactly the event keys")
    _identifier(event["authorization"], "authorization")
    _revision(event["revision"])
    _identifier(event["subject"], "subject")
    if event["operation"] not in ("grant", "withdraw"):
        raise InvalidRequest("operation must be grant or withdraw")
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(
            isinstance(item, str) for item in parameters):
        raise InvalidRequest("parameters must be a list of strings")
    start = _date(event["start"], "start")
    end = _date(event["end"], "end")
    if start >= end:
        raise InvalidRequest("start must be strictly before end")


def _events_only(request):
    if "events" not in request:
        raise InvalidRequest("request must carry events or transport")
    events = request["events"]
    if not isinstance(events, list):
        raise InvalidRequest("events must be a list")
    for event in events:
        _validate_event(event)
    return events


def _canonical_events_bytes(events):
    raw = json.dumps(
        events, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return raw


def _segmented_events(transport):
    if not isinstance(transport, dict) or set(transport) != {"commits", "pages"}:
        raise InvalidRequest("transport must carry exactly commits and pages")
    commits = transport["commits"]
    pages = transport["pages"]
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise InvalidRequest("commits and pages must be lists")

    committed = {}
    for commit in commits:
        if not isinstance(commit, dict) or set(commit) != COMMIT_KEYS:
            raise InvalidRequest("each commit must have exactly batch, parts and sha256")
        batch = _identifier(commit["batch"], "commit batch")
        parts = _exact_int(commit["parts"], "parts", 1)
        digest = commit["sha256"]
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise InvalidRequest("sha256 must be a lowercase hexadecimal digest")
        prior = committed.get(batch)
        if prior is not None and prior != commit:
            raise InvalidRequest("conflicting replay of commit %r" % batch)
        committed[batch] = commit

    replay = {}
    for page in pages:
        if not isinstance(page, dict) or set(page) != PAGE_KEYS:
            raise InvalidRequest("each page must have exactly batch, part and events")
        batch = _identifier(page["batch"], "page batch")
        part = _exact_int(page["part"], "part", 0)
        if not isinstance(page["events"], list):
            raise InvalidRequest("page events must be a list")
        prior = replay.get((batch, part))
        if prior is not None and prior != page:
            raise InvalidRequest("conflicting replay of page %r part %d" % (batch, part))
        replay[(batch, part)] = page

    uncommitted = sorted({batch for batch, _ in replay} - set(committed))
    if uncommitted:
        raise InvalidRequest("uncommitted page batch %r" % uncommitted[0])

    complete = []
    for batch, commit in committed.items():
        expected_parts = set(range(commit["parts"]))
        actual_parts = {part for page_batch, part in replay if page_batch == batch}
        if actual_parts != expected_parts:
            missing = sorted(expected_parts - actual_parts)
            extra = sorted(actual_parts - expected_parts)
            detail = "missing %r" % missing[0] if missing else "extra %r" % extra[0]
            raise InvalidRequest("batch %r has %s part" % (batch, detail))
        assembled = []
        for part in range(commit["parts"]):
            assembled.extend(replay[(batch, part)]["events"])
        digest = hashlib.sha256(_canonical_events_bytes(assembled)).hexdigest()
        if digest != commit["sha256"]:
            raise InvalidRequest("batch %r digest mismatch" % batch)
        complete.extend(assembled)

    for event in complete:
        _validate_event(event)
    return complete


def resolve(request):
    """Return the approved parameter projection for ``request``.

    Invalid or internally inconsistent history raises :class:`InvalidRequest`.
    A valid result contains only requested subjects, including ``None`` when
    no winning authorization currently applies to a requested subject.
    """
    if not isinstance(request, dict):
        raise InvalidRequest("request must be an object")
    now = _date(request.get("now"), "now")
    subjects = request.get("subjects")
    if not isinstance(subjects, list):
        raise InvalidRequest("subjects must be a list")
    for subject in subjects:
        _identifier(subject, "subject")
    if len(set(subjects)) != len(subjects):
        raise InvalidRequest("subjects must not contain duplicates")

    if "transport" in request:
        if "events" in request:
            raise InvalidRequest("request must carry either events or transport")
        events = _segmented_events(request["transport"])
    else:
        events = _events_only(request)

    # Every equal revision in the complete history must be identical, even if
    # a later revision has superseded it.  Only after that whole-history check
    # does the numerically highest revision replace the earlier ones.
    seen = {}
    winners = {}
    for event in events:
        authorization = event["authorization"]
        revision = event["revision"]
        identity = (authorization, revision)
        old = seen.get(identity)
        if old is not None and old != event:
            raise InvalidRequest(
                "conflicting revision %r for authorization %r"
                % (revision, authorization)
            )
        seen[identity] = event
        winner = winners.get(authorization)
        if winner is None or _revision_key(revision) > _revision_key(winner["revision"]):
            winners[authorization] = event

    active_by_subject = {}
    for event in winners.values():
        if event["operation"] != "grant":
            continue
        if not (event["start"] <= now.isoformat() < event["end"]):
            continue
        subject = event["subject"]
        if subject in active_by_subject:
            raise InvalidRequest(
                "multiple active authorizations for subject %r" % subject
            )
        active_by_subject[subject] = event

    return {
        subject: active_by_subject[subject]["parameters"]
        if subject in active_by_subject else None
        for subject in subjects
    }


def main(argv=None):
    try:
        request = json.load(sys.stdin)
        print(json.dumps(resolve(request), sort_keys=True, ensure_ascii=False))
        return 0
    except (InvalidRequest, ValueError, TypeError, json.JSONDecodeError) as exc:
        sys.stderr.write("policy resolver: %s\n" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
