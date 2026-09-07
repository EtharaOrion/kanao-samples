"""Validate and resolve the current signed authorization snapshot."""

import json
import re
import sys
from datetime import date

_EVENT_KEYS = frozenset(
    ("authorization", "revision", "subject", "operation", "start", "end", "parameters")
)
_COMMIT_KEYS = frozenset(("batch", "parts", "sha256"))
_PAGE_KEYS = frozenset(("batch", "part", "events"))
_REVISION = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _object(value, description):
    if not isinstance(value, dict):
        raise ValueError(description + " is not an object")
    return value


def _keys(value, required, description):
    _object(value, description)
    keys = set(value)
    if keys != set(required):
        missing = sorted(set(required) - keys)
        extra = sorted(keys - set(required))
        raise ValueError(
            "%s has invalid keys (missing=%r, extra=%r)" % (description, missing, extra)
        )


def _nonempty_string(value, description):
    if not isinstance(value, str) or not value:
        raise ValueError(description + " is not a nonempty string")
    return value


def _integer(value, description):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(description + " is not an integer")
    return value


def _date(value, description):
    _nonempty_string(value, description)
    if not _DATE.fullmatch(value):
        raise ValueError(description + " is not an ISO YYYY-MM-DD date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(description + " is not an ISO YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise ValueError(description + " is not a canonical ISO date")
    return parsed


def _event(value, number):
    description = "event %d" % number
    _keys(value, _EVENT_KEYS, description)
    _nonempty_string(value["authorization"], description + ".authorization")
    revision = _nonempty_string(value["revision"], description + ".revision")
    if not _REVISION.fullmatch(revision):
        raise ValueError(description + ".revision is not a canonical decimal")
    _nonempty_string(value["subject"], description + ".subject")
    if value["operation"] not in ("grant", "withdraw"):
        raise ValueError(description + ".operation is invalid")
    start = _date(value["start"], description + ".start")
    end = _date(value["end"], description + ".end")
    if start >= end:
        raise ValueError(description + " does not have start before end")
    parameters = value["parameters"]
    if not isinstance(parameters, list) or any(
        not isinstance(item, str) for item in parameters
    ):
        raise ValueError(description + ".parameters is not a list of strings")
    return value


def _events_only(request):
    if "events" not in request:
        raise ValueError("request has neither transport nor events")
    events = request["events"]
    if not isinstance(events, list):
        raise ValueError("events is not an array")
    return [_event(event, index) for index, event in enumerate(events)]


def _segmented(request):
    if "events" in request:
        raise ValueError("request carries both transport and events")
    transport = _object(request["transport"], "transport")
    keys = set(transport)
    if keys != {"commits", "pages"}:
        raise ValueError(
            "transport has invalid keys (missing=%r, extra=%r)"
            % (sorted({"commits", "pages"} - keys), sorted(keys - {"commits", "pages"}))
        )

    commits = transport["commits"]
    pages = transport["pages"]
    if not isinstance(commits, list):
        raise ValueError("transport.commits is not an array")
    if not isinstance(pages, list):
        raise ValueError("transport.pages is not an array")

    committed = {}
    for index, raw in enumerate(commits):
        description = "commit %d" % index
        _keys(raw, _COMMIT_KEYS, description)
        batch = _nonempty_string(raw["batch"], description + ".batch")
        parts = _integer(raw["parts"], description + ".parts")
        if parts < 1:
            raise ValueError(description + ".parts is not positive")
        digest = _nonempty_string(raw["sha256"], description + ".sha256")
        if not _SHA256.fullmatch(digest):
            raise ValueError(description + ".sha256 is not a lowercase SHA-256 digest")
        previous = committed.get(batch)
        if previous is not None and previous != raw:
            raise ValueError("conflicting replay for batch %r" % batch)
        committed[batch] = raw

    delivered = {}
    for index, raw in enumerate(pages):
        description = "page %d" % index
        _keys(raw, _PAGE_KEYS, description)
        batch = _nonempty_string(raw["batch"], description + ".batch")
        part = _integer(raw["part"], description + ".part")
        if part < 0:
            raise ValueError(description + ".part is negative")
        events = raw["events"]
        if not isinstance(events, list):
            raise ValueError(description + ".events is not an array")
        validated = [_event(event, index * 100000 + position) for position, event in enumerate(events)]
        page = dict(raw)
        page["events"] = validated
        previous = delivered.get((batch, part))
        if previous is not None and previous != page:
            raise ValueError("conflicting replay for batch %r part %d" % (batch, part))
        delivered[(batch, part)] = page

    uncommitted = sorted({batch for batch, _ in delivered if batch not in committed})
    if uncommitted:
        raise ValueError("uncommitted pages for batches %r" % uncommitted)

    history = []
    for batch, commit in committed.items():
        expected_parts = set(range(commit["parts"]))
        present_parts = {part for page_batch, part in delivered if page_batch == batch}
        missing = sorted(expected_parts - present_parts)
        extra = sorted(present_parts - expected_parts)
        if missing or extra:
            raise ValueError(
                "batch %r has invalid parts (missing=%r, extra=%r)"
                % (batch, missing, extra)
            )
        events = []
        for part in range(commit["parts"]):
            events.extend(delivered[(batch, part)]["events"])
        encoded = json.dumps(
            events,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        import hashlib

        if hashlib.sha256(encoded).hexdigest() != commit["sha256"]:
            raise ValueError("batch %r digest mismatch" % batch)
        history.extend(events)
    return history


def resolve(request):
    """Return the approved authorization parameters for each requested subject."""
    _object(request, "request")
    now = _date(request.get("now"), "request.now")

    subjects = request.get("subjects")
    if not isinstance(subjects, list):
        raise ValueError("request.subjects is not an array")
    requested = []
    seen = set()
    for index, subject in enumerate(subjects):
        _nonempty_string(subject, "request.subjects[%d]" % index)
        # JSON object keys collapse repeated request projections; repeated
        # subject spellings therefore remain a valid idempotent request.
        seen.add(subject)
        if subject not in requested:
            requested.append(subject)

    if "transport" in request:
        history = _segmented(request)
    else:
        history = _events_only(request)

    # An authorization identity is revised as a whole. Repeated identical
    # revisions are delivery replays; conflicting equal revisions are invalid.
    seen_revisions = {}
    winners = {}
    for event in history:
        authorization = event["authorization"]
        revision = int(event["revision"])
        key = (authorization, revision)
        previous = seen_revisions.get(key)
        if previous is not None and event != previous:
            raise ValueError(
                "conflicting revision %s for authorization %r"
                % (event["revision"], authorization)
            )
        seen_revisions[key] = event
        current = winners.get(authorization)
        if current is None or revision > int(current["revision"]):
            winners[authorization] = event

    # Resolve all authorizations before projection, so corrections remove an
    # old subject and a withdrawal removes an older grant entirely.
    active = {}
    for event in winners.values():
        if event["operation"] != "grant":
            continue
        if not (date.fromisoformat(event["start"]) <= now < date.fromisoformat(event["end"])):
            continue
        subject = event["subject"]
        if subject in active:
            raise ValueError("multiple active authorizations for subject %r" % subject)
        active[subject] = event["parameters"]

    return {subject: active.get(subject) for subject in requested}


if __name__ == "__main__":
    result = resolve(json.load(sys.stdin))
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
