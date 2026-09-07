"""Resolve the current signed authorization snapshot.

Implements the contract in docs/policy-reconciliation.md:

  * authorizations are identified independently of subjects; the highest
    numeric revision of an authorization fully replaces every earlier
    revision of it, including that revision's subject and interval;
  * repeated identical revisions are idempotent, conflicting equal
    revisions are invalid;
  * a winning withdrawal contributes no active decision;
  * a grant applies only within start <= now < end;
  * two active authorizations for one subject are an error;
  * the complete history is validated before anything is projected, and an
    invalid history raises (nonzero CLI exit) with no partial result.
"""
import json
import hashlib
import re
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation",
              "start", "end", "parameters"}
REVISION = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
DATE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class PolicyError(ValueError):
    """The authorization history is not one this adapter may project."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _nonempty(value):
    return isinstance(value, str) and value != ""


def _date(value):
    if not (_nonempty(value) and DATE.match(value)):
        return False
    month, day = int(value[5:7]), int(value[8:10])
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return False
    return True


def _check_event(event):
    if not isinstance(event, dict):
        raise PolicyError("event is not an object")
    if set(event) != EVENT_KEYS:
        raise PolicyError("event keys are not exactly the required set")
    if not _nonempty(event["authorization"]):
        raise PolicyError("authorization is not a nonempty string")
    if not _nonempty(event["subject"]):
        raise PolicyError("subject is not a nonempty string")
    revision = event["revision"]
    if not _nonempty(revision) or not REVISION.match(revision):
        raise PolicyError("revision is not a canonical decimal string")
    if event["operation"] not in ("grant", "withdraw"):
        raise PolicyError("operation is not grant or withdraw")
    parameters = event["parameters"]
    if not isinstance(parameters, list):
        raise PolicyError("parameters is not a list")
    for item in parameters:
        if not isinstance(item, str):
            raise PolicyError("parameters is not a list of strings")
    if not _date(event["start"]) or not _date(event["end"]):
        raise PolicyError("start and end must be ISO YYYY-MM-DD dates")
    if not event["start"] < event["end"]:
        raise PolicyError("start must be strictly before end")


def _assemble(transport):
    """Rebuild the complete event history from a segmented delivery.

    Batches are deltas, so every committed batch belongs to the history
    whatever order the transport presents it in.
    """
    if not isinstance(transport, dict):
        raise PolicyError("transport is not an object")
    commits, pages = transport.get("commits"), transport.get("pages")
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise PolicyError("transport needs a commits list and a pages list")

    committed = {}
    for commit in commits:
        if not isinstance(commit, dict):
            raise PolicyError("commit is not an object")
        batch = commit.get("batch")
        parts = commit.get("parts")
        digest = commit.get("sha256")
        if not _nonempty(batch):
            raise PolicyError("commit batch is not a nonempty string")
        if isinstance(parts, bool) or not isinstance(parts, int) or parts < 1:
            raise PolicyError("commit parts is not a positive integer")
        if not _nonempty(digest):
            raise PolicyError("commit sha256 is not a nonempty string")
        if batch in committed and committed[batch] != (parts, digest):
            raise PolicyError("conflicting commits for one batch")
        committed[batch] = (parts, digest)

    seen = {}
    for page in pages:
        if not isinstance(page, dict):
            raise PolicyError("page is not an object")
        batch = page.get("batch")
        part = page.get("part")
        events = page.get("events")
        if not _nonempty(batch):
            raise PolicyError("page batch is not a nonempty string")
        if isinstance(part, bool) or not isinstance(part, int) or part < 0:
            raise PolicyError("page part is not a zero-based integer")
        if not isinstance(events, list):
            raise PolicyError("page events is not a list")
        if batch not in committed:
            raise PolicyError("page for an uncommitted batch")
        if part >= committed[batch][0]:
            raise PolicyError("page part is outside the committed parts")
        key = (batch, part)
        if key in seen and _canonical(seen[key]) != _canonical(events):
            raise PolicyError("conflicting replay of one page")
        seen[key] = events

    history = []
    for batch in sorted(committed):
        parts, digest = committed[batch]
        assembled = []
        for part in range(parts):
            if (batch, part) not in seen:
                raise PolicyError("missing part for a committed batch")
            assembled.extend(seen[(batch, part)])
        if hashlib.sha256(_canonical(assembled)).hexdigest() != digest:
            raise PolicyError("batch digest does not match its pages")
        history.extend(assembled)
    return history


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError("request is not an object")
    now = request.get("now")
    if not _date(now):
        raise PolicyError("now is not an ISO date")
    subjects = request.get("subjects")
    if not isinstance(subjects, list):
        raise PolicyError("subjects is not a list")
    for subject in subjects:
        if not _nonempty(subject):
            raise PolicyError("subjects is not a list of nonempty strings")

    if "transport" in request:
        if "events" in request:
            raise PolicyError("request carries both events and transport")
        history = _assemble(request["transport"])
    else:
        history = request.get("events")
        if not isinstance(history, list):
            raise PolicyError("events is not a list")

    # Complete validation precedes projection: every event in the history
    # counts, including superseded, withdrawn, out-of-window and
    # unrequested-subject records.
    for event in history:
        _check_event(event)

    # Form the corrected authorization snapshot first. Authorizations are
    # identified independently of subjects, so a correction carries its
    # subject and interval with it and no older revision survives on the
    # subject it moved away from.
    # Repeated identical revisions are idempotent; conflicting equal
    # revisions are invalid. This holds for every revision in the history,
    # not just the one that ends up winning, so bucket the whole history
    # first and only then choose the winner.
    revisions = {}
    for event in history:
        key = (event["authorization"], int(event["revision"]))
        if key in revisions:
            if _canonical(event) != _canonical(revisions[key]):
                raise PolicyError(
                    "conflicting equal revisions of one authorization")
            continue
        revisions[key] = event

    winning = {}
    for (name, revision), event in revisions.items():
        current = winning.get(name)
        if current is None or revision > current[0]:
            winning[name] = (revision, event)

    active = {}
    for _, event in winning.values():
        # A winning withdrawal contributes no active decision.
        if event["operation"] != "grant":
            continue
        # A grant applies only within start <= now < end.
        if not (event["start"] <= now < event["end"]):
            continue
        subject = event["subject"]
        if subject in active:
            raise PolicyError("multiple active authorizations for a subject")
        active[subject] = event["parameters"]

    # Unknown subjects are excluded from the projection; every requested
    # subject is returned, mapped to its parameters or to null.
    return {subject: active.get(subject) for subject in subjects}


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write("resolve: request is not valid JSON: %s\n" % exc)
        raise SystemExit(1)
    try:
        decision = resolve(request)
    except PolicyError as exc:
        sys.stderr.write("resolve: %s\n" % exc)
        raise SystemExit(1)
    except (ValueError, KeyError, TypeError) as exc:
        sys.stderr.write("resolve: malformed request: %s\n" % exc)
        raise SystemExit(1)
    print(json.dumps(decision, sort_keys=True))
