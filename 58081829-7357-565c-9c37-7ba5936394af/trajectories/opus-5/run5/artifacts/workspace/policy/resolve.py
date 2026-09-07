#!/usr/bin/env python3
"""Resolve the current signed authorization snapshot.

Contract: docs/policy-reconciliation.md.

Complete validation of the whole candidate precedes any projection. Any fault
raises PolicyError, which the CLI turns into a nonzero exit with no partial
result on stdout.
"""
import hashlib
import json
import re
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation",
              "start", "end", "parameters"}
OPERATIONS = {"grant", "withdraw"}
REVISION_RE = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
DATE_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class PolicyError(Exception):
    """An invalid request. Never a partial result."""


def _nonempty_str(value):
    return isinstance(value, str) and value != ""


def _valid_date(value):
    if not _nonempty_str(value) or not DATE_RE.match(value):
        return False
    year, month, day = int(value[0:4]), int(value[5:7]), int(value[8:10])
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return False
    if month in (4, 6, 9, 11) and day > 30:
        return False
    if month == 2:
        leap = (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
        if day > (29 if leap else 28):
            return False
    return True


def _canonical(events):
    """The digest pre-image: the event array as UTF-8 JSON, sorted keys."""
    return json.dumps(events, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def validate_event(event):
    if not isinstance(event, dict):
        raise PolicyError("event is not an object")
    if set(event) != EVENT_KEYS:
        raise PolicyError("event keys are not exactly the required set")
    if not _nonempty_str(event["authorization"]):
        raise PolicyError("authorization must be a nonempty string")
    if not _nonempty_str(event["subject"]):
        raise PolicyError("subject must be a nonempty string")
    revision = event["revision"]
    if not _nonempty_str(revision) or not REVISION_RE.match(revision):
        raise PolicyError("revision must be a canonical nonnegative decimal")
    if event["operation"] not in OPERATIONS:
        raise PolicyError("operation must be grant or withdraw")
    for field in ("start", "end"):
        if not _valid_date(event[field]):
            raise PolicyError("%s must be an ISO date" % field)
    if not event["start"] < event["end"]:
        raise PolicyError("start must be strictly before end")
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(isinstance(p, str)
                                                   for p in parameters):
        raise PolicyError("parameters must be a list of strings")


def assemble(transport):
    """Rebuild the complete event history from committed, paged batches."""
    if not isinstance(transport, dict):
        raise PolicyError("transport must be an object")
    if set(transport) - {"commits", "pages"}:
        raise PolicyError("unexpected transport member")
    commits = transport.get("commits")
    pages = transport.get("pages")
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise PolicyError("commits and pages must be lists")

    declared = {}
    for commit in commits:
        if not isinstance(commit, dict) or set(commit) != {"batch", "parts",
                                                           "sha256"}:
            raise PolicyError("malformed commit")
        batch, parts, digest = commit["batch"], commit["parts"], commit["sha256"]
        if not _nonempty_str(batch):
            raise PolicyError("commit batch must be a nonempty string")
        if isinstance(parts, bool) or not isinstance(parts, int) or parts < 1:
            raise PolicyError("commit parts must be a positive integer")
        if not _nonempty_str(digest):
            raise PolicyError("commit sha256 must be a nonempty string")
        if batch in declared:
            if declared[batch] != (parts, digest):
                raise PolicyError("conflicting replay of commit %s" % batch)
            continue
        declared[batch] = (parts, digest)

    collected = {}
    for page in pages:
        if not isinstance(page, dict) or set(page) != {"batch", "part",
                                                       "events"}:
            raise PolicyError("malformed page")
        batch, part, events = page["batch"], page["part"], page["events"]
        if not _nonempty_str(batch):
            raise PolicyError("page batch must be a nonempty string")
        if isinstance(part, bool) or not isinstance(part, int) or part < 0:
            raise PolicyError("page part must be a zero-based integer")
        if not isinstance(events, list):
            raise PolicyError("page events must be a list")
        slot = collected.setdefault(batch, {})
        if part in slot:
            if _canonical(slot[part]) != _canonical(events):
                raise PolicyError("conflicting replay of %s part %d"
                                  % (batch, part))
            continue
        slot[part] = events

    for batch in collected:
        if batch not in declared:
            raise PolicyError("uncommitted pages for batch %s" % batch)

    history = []
    for batch in sorted(declared):
        parts, digest = declared[batch]
        slot = collected.get(batch, {})
        if set(slot) != set(range(parts)):
            raise PolicyError("batch %s is missing or has extra parts" % batch)
        events = []
        for part in range(parts):
            events.extend(slot[part])
        if hashlib.sha256(_canonical(events)).hexdigest() != digest:
            raise PolicyError("digest mismatch for batch %s" % batch)
        history.extend(events)
    return history


def corrected_snapshot(history):
    """Highest revision of each authorization, fully replacing earlier ones.

    Equal revisions of one authorization must agree byte-for-byte wherever they
    appear in the history, not merely at the revision that ends up winning: a
    conflicting pair anywhere invalidates the candidate.
    """
    seen = {}
    for event in history:
        key = (event["authorization"], int(event["revision"]))
        held = seen.get(key)
        if held is None:
            seen[key] = event
        elif _canonical(event) != _canonical(held):
            raise PolicyError(
                "conflicting revision %s of %s"
                % (event["revision"], event["authorization"]))

    winning = {}
    for (name, revision), event in seen.items():
        held = winning.get(name)
        if held is None or revision > held[0]:
            winning[name] = (revision, event)
    return {name: held[1] for name, held in winning.items()}


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError("request must be an object")

    now = request.get("now")
    if not _valid_date(now):
        raise PolicyError("now must be an ISO date")

    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not all(_nonempty_str(s)
                                                 for s in subjects):
        raise PolicyError("subjects must be a list of nonempty strings")

    has_transport = "transport" in request
    has_events = "events" in request
    if has_transport and has_events:
        raise PolicyError("request carries both events and transport")
    if has_transport:
        history = assemble(request["transport"])
    elif has_events:
        history = request["events"]
        if not isinstance(history, list):
            raise PolicyError("events must be a list")
    else:
        raise PolicyError("request carries neither events nor transport")

    # Every event in the complete history must be valid, including superseded,
    # withdrawn, out-of-window and unrequested-subject records.
    for event in history:
        validate_event(event)

    snapshot = corrected_snapshot(history)

    # Projection only after the whole candidate has been validated.
    active = {}
    for name, event in snapshot.items():
        if event["operation"] != "grant":
            continue
        if not (event["start"] <= now < event["end"]):
            continue
        subject = event["subject"]
        if subject in active:
            raise PolicyError(
                "multiple active authorizations for subject %s" % subject)
        active[subject] = event["parameters"]

    return {subject: active.get(subject) for subject in subjects}


def main():
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write("resolve: malformed request: %s\n" % exc)
        return 2
    try:
        result = resolve(request)
    except PolicyError as exc:
        sys.stderr.write("resolve: %s\n" % exc)
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
