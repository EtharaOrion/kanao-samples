"""Resolve the current signed authorization snapshot.

Implements the contract in docs/policy-reconciliation.md.
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

REVISION_RE = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
DATE_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class PolicyError(ValueError):
    """Raised when the request or the history it carries is not valid."""


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _nonempty_str(value):
    return isinstance(value, str) and value != ""


def _check_date(value, field):
    if not _nonempty_str(value) or not DATE_RE.match(value):
        raise PolicyError(field + " is not an ISO YYYY-MM-DD date")
    year, month, day = (int(part) for part in value.split("-"))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise PolicyError(field + " is not a real calendar date")
    return value


def _validate_event(event):
    if not isinstance(event, dict):
        raise PolicyError("an event is not an object")
    if set(event) != EVENT_KEYS:
        raise PolicyError("event keys are not exactly the seven required")
    if not _nonempty_str(event["authorization"]):
        raise PolicyError("authorization is not a nonempty string")
    if not _nonempty_str(event["subject"]):
        raise PolicyError("subject is not a nonempty string")
    revision = event["revision"]
    if not _nonempty_str(revision) or not REVISION_RE.match(revision):
        raise PolicyError("revision is not a canonical nonnegative decimal")
    if event["operation"] not in ("grant", "withdraw"):
        raise PolicyError("operation is neither grant nor withdraw")
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(
        isinstance(item, str) for item in parameters
    ):
        raise PolicyError("parameters is not a list of strings")
    start = _check_date(event["start"], "start")
    end = _check_date(event["end"], "end")
    if not start < end:
        raise PolicyError("start is not strictly before end")


def _assemble(transport):
    if not isinstance(transport, dict):
        raise PolicyError("transport is not an object")
    if set(transport) != {"commits", "pages"}:
        raise PolicyError("transport carries keys other than commits, pages")
    commits = transport["commits"]
    pages = transport["pages"]
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise PolicyError("commits and pages must be lists")

    committed = {}
    for commit in commits:
        if not isinstance(commit, dict):
            raise PolicyError("a commit is not an object")
        if set(commit) != {"batch", "parts", "sha256"}:
            raise PolicyError("a commit is malformed")
        batch = commit["batch"]
        parts = commit["parts"]
        digest = commit["sha256"]
        if not _nonempty_str(batch):
            raise PolicyError("commit batch is not a nonempty string")
        if isinstance(parts, bool) or not isinstance(parts, int) or parts < 1:
            raise PolicyError("commit parts is not a positive integer")
        if not _nonempty_str(digest):
            raise PolicyError("commit sha256 is not a nonempty string")
        previous = committed.get(batch)
        if previous is None:
            committed[batch] = (parts, digest)
        elif previous != (parts, digest):
            raise PolicyError("conflicting commit replay for a batch")

    delivered = {}
    for page in pages:
        if not isinstance(page, dict):
            raise PolicyError("a page is not an object")
        if set(page) != {"batch", "part", "events"}:
            raise PolicyError("a page is malformed")
        batch = page["batch"]
        part = page["part"]
        events = page["events"]
        if not _nonempty_str(batch):
            raise PolicyError("page batch is not a nonempty string")
        if isinstance(part, bool) or not isinstance(part, int) or part < 0:
            raise PolicyError("page part is not a zero-based integer")
        if not isinstance(events, list):
            raise PolicyError("page events is not a list")
        if batch not in committed:
            raise PolicyError("a page belongs to no committed batch")
        key = (batch, part)
        previous = delivered.get(key)
        if previous is None:
            delivered[key] = events
        elif _canonical(previous) != _canonical(events):
            raise PolicyError("conflicting page replay for a part")

    history = []
    for batch in sorted(committed):
        parts, digest = committed[batch]
        for delivered_batch, delivered_part in delivered:
            if delivered_batch == batch and delivered_part >= parts:
                raise PolicyError("a batch carries a part it did not commit")
        segments = []
        for part in range(parts):
            if (batch, part) not in delivered:
                raise PolicyError("a committed batch is missing a part")
            segments.extend(delivered[(batch, part)])
        if hashlib.sha256(_canonical(segments)).hexdigest() != digest:
            raise PolicyError("a batch does not match its committed digest")
        history.extend(segments)
    return history


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError("request is not an object")
    for field in ("now", "subjects"):
        if field not in request:
            raise PolicyError("request is missing " + field)
    now = _check_date(request["now"], "now")
    subjects = request["subjects"]
    if not isinstance(subjects, list) or not all(
        _nonempty_str(subject) for subject in subjects
    ):
        raise PolicyError("subjects is not a list of nonempty strings")

    has_events = "events" in request
    has_transport = "transport" in request
    if has_events and has_transport:
        raise PolicyError("request carries both events and transport")
    if has_transport:
        history = _assemble(request["transport"])
    elif has_events:
        history = request["events"]
        if not isinstance(history, list):
            raise PolicyError("events is not a list")
    else:
        raise PolicyError("request carries neither events nor transport")

    for event in history:
        _validate_event(event)

    revisions = {}
    for event in history:
        key = (event["authorization"], int(event["revision"]))
        current = revisions.get(key)
        if current is None:
            revisions[key] = event
        elif _canonical(event) != _canonical(current):
            raise PolicyError("conflicting events share a revision")

    winners = {}
    for (authorization, revision), event in revisions.items():
        current = winners.get(authorization)
        if current is None or revision > int(current["revision"]):
            winners[authorization] = event

    wanted = set(subjects)
    active = {}
    for authorization, event in winners.items():
        if event["operation"] != "grant":
            continue
        if not event["start"] <= now < event["end"]:
            continue
        subject = event["subject"]
        if subject not in wanted:
            continue
        if subject in active:
            raise PolicyError("multiple active authorizations for a subject")
        active[subject] = event

    return {
        subject: (active[subject]["parameters"] if subject in active else None)
        for subject in subjects
    }


def main():
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write("resolve: request is not valid JSON\n")
        return 1
    try:
        decision = resolve(request)
    except PolicyError as exc:
        sys.stderr.write("resolve: " + str(exc) + "\n")
        return 1
    sys.stdout.write(json.dumps(decision, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
