"""Resolve the current signed authorization snapshot.

The reconciliation contract is in docs/policy-reconciliation.md. In outline:

  * A request carries either ``events`` or a segmented ``transport``.
  * The complete authorization history is validated before any projection is
    attempted; an invalid history raises and yields no partial result.
  * Revisions are resolved per authorization, not per subject, so that a
    correction may move an authorization to another subject or outside the
    evaluation window without its older revision surviving on the former
    subject.
  * A winning withdrawal contributes no active decision; a winning grant
    applies only while ``start <= now < end``.
  * Two active authorizations for one subject are an error, not a choice.
"""
import hashlib
import json
import re
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation",
              "start", "end", "parameters"}
CANONICAL_REVISION = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
ISO_DATE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class InvalidHistory(ValueError):
    """The authorization history is not internally consistent."""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value):
    return isinstance(value, str) and value != ""


def _date(value):
    if not isinstance(value, str) or not ISO_DATE.match(value):
        return False
    year, month, day = int(value[0:4]), int(value[5:7]), int(value[8:10])
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return False
    return (year, month, day) == (year, month, day)


def _validate_event(event):
    if not isinstance(event, dict):
        raise InvalidHistory("event is not an object")
    if set(event) != EVENT_KEYS:
        raise InvalidHistory("event keys are not exactly %s"
                             % ", ".join(sorted(EVENT_KEYS)))
    if not _text(event["authorization"]):
        raise InvalidHistory("authorization must be a nonempty string")
    if not _text(event["subject"]):
        raise InvalidHistory("subject must be a nonempty string")
    if not isinstance(event["revision"], str) \
            or not CANONICAL_REVISION.match(event["revision"]):
        raise InvalidHistory("revision must be a canonical nonnegative decimal")
    if event["operation"] not in ("grant", "withdraw"):
        raise InvalidHistory("operation must be grant or withdraw")
    if not _date(event["start"]) or not _date(event["end"]):
        raise InvalidHistory("start and end must be ISO YYYY-MM-DD dates")
    if not event["start"] < event["end"]:
        raise InvalidHistory("start must be strictly before end")
    parameters = event["parameters"]
    if not isinstance(parameters, list) \
            or not all(isinstance(item, str) for item in parameters):
        raise InvalidHistory("parameters must be a list of strings")


def _digest(events):
    payload = json.dumps(events, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assemble(transport):
    """Reassemble the delivered history from committed, paged deltas."""
    if not isinstance(transport, dict):
        raise InvalidHistory("transport is not an object")
    if set(transport) != {"commits", "pages"}:
        raise InvalidHistory("transport carries exactly commits and pages")
    if not isinstance(transport["commits"], list) \
            or not isinstance(transport["pages"], list):
        raise InvalidHistory("commits and pages must be lists")

    commits = {}
    for commit in transport["commits"]:
        if not isinstance(commit, dict) or set(commit) != {"batch", "parts",
                                                           "sha256"}:
            raise InvalidHistory("commit keys are exactly batch, parts, sha256")
        if not _text(commit["batch"]):
            raise InvalidHistory("commit batch must be a nonempty string")
        if not _is_int(commit["parts"]) or commit["parts"] <= 0:
            raise InvalidHistory("commit parts must be a positive integer")
        if not _text(commit["sha256"]):
            raise InvalidHistory("commit sha256 must be a nonempty string")
        batch = commit["batch"]
        if batch in commits and commits[batch] != commit:
            raise InvalidHistory("conflicting commit replay for batch %s"
                                 % batch)
        commits[batch] = commit

    pages = {}
    for page in transport["pages"]:
        if not isinstance(page, dict) or set(page) != {"batch", "part",
                                                      "events"}:
            raise InvalidHistory("page keys are exactly batch, part, events")
        if not _text(page["batch"]):
            raise InvalidHistory("page batch must be a nonempty string")
        if not _is_int(page["part"]) or page["part"] < 0:
            raise InvalidHistory("page part must be a zero-based integer")
        if not isinstance(page["events"], list):
            raise InvalidHistory("page events must be a list")
        key = (page["batch"], page["part"])
        if key in pages and pages[key] != page["events"]:
            raise InvalidHistory("conflicting page replay for batch %s part %d"
                                 % key)
        pages[key] = page["events"]

    for batch, part in pages:
        if batch not in commits:
            raise InvalidHistory("uncommitted page for batch %s" % batch)

    events = []
    for batch in sorted(commits):
        parts = commits[batch]["parts"]
        delivered = sorted(part for (name, part) in pages if name == batch)
        if delivered != list(range(parts)):
            raise InvalidHistory("batch %s does not carry parts 0..%d"
                                 % (batch, parts - 1))
        batch_events = []
        for part in range(parts):
            batch_events.extend(pages[(batch, part)])
        if _digest(batch_events) != commits[batch]["sha256"]:
            raise InvalidHistory("batch %s does not match its digest" % batch)
        events.extend(batch_events)
    return events


def _history(request):
    has_events = "events" in request
    has_transport = "transport" in request
    if has_events and has_transport:
        raise InvalidHistory("request carries both events and transport")
    if not has_events and not has_transport:
        raise InvalidHistory("request carries neither events nor transport")
    if has_transport:
        return _assemble(request["transport"])
    if not isinstance(request["events"], list):
        raise InvalidHistory("events must be a list")
    return request["events"]


def resolve(request):
    if not isinstance(request, dict):
        raise InvalidHistory("request is not an object")
    now = request.get("now")
    if not _date(now):
        raise InvalidHistory("now must be an ISO YYYY-MM-DD date")
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not all(_text(s) for s in subjects):
        raise InvalidHistory("subjects must be a list of nonempty strings")

    events = _history(request)

    # Complete validation precedes projection: every event in the history
    # counts, including superseded, withdrawn and out-of-window records.
    for event in events:
        _validate_event(event)

    # Identical revisions are idempotent; equal revisions that disagree are
    # invalid, whether or not either of them goes on to win.
    revisions = {}
    for event in events:
        key = (event["authorization"], int(event["revision"]))
        if key in revisions and revisions[key] != event:
            raise InvalidHistory(
                "conflicting revision %d of authorization %s"
                % (key[1], key[0]))
        revisions[key] = event

    # A higher numeric revision fully replaces an earlier revision of the same
    # authorization, including its subject and effective interval.
    winning = {}
    for (authorization, revision), event in revisions.items():
        current = winning.get(authorization)
        if current is None or revision > current[0]:
            winning[authorization] = (revision, event)

    active = {}
    for authorization, (revision, event) in winning.items():
        if event["operation"] != "grant":
            continue
        if not event["start"] <= now < event["end"]:
            continue
        subject = event["subject"]
        if subject in active:
            raise InvalidHistory(
                "multiple active authorizations for subject %s" % subject)
        active[subject] = event

    # Unknown subjects are excluded from the projection after resolution.
    return {subject: (active[subject]["parameters"] if subject in active
                      else None)
            for subject in subjects}


def main():
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write("resolve: malformed request: %s\n" % exc)
        return 2
    try:
        decision = resolve(request)
    except InvalidHistory as exc:
        sys.stderr.write("resolve: %s\n" % exc)
        return 1
    print(json.dumps(decision, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
