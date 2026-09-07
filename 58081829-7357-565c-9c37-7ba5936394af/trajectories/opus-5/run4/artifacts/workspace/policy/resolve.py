"""Resolve the current signed authorization snapshot.

Validates the complete authorization history before projecting any decision.
Invalid history raises; callers surface a nonzero exit with no partial result.
"""
import hashlib
import json
import re
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation", "start",
              "end", "parameters"}
REVISION = re.compile(r"\A(0|[1-9][0-9]*)\Z")
DATE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class PolicyError(Exception):
    pass


def _canonical(events):
    return json.dumps(events, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _nonempty(value):
    return isinstance(value, str) and value != ""


def _check_event(event):
    if not isinstance(event, dict) or set(event) != EVENT_KEYS:
        raise PolicyError("event keys are not exactly the required set")
    if not _nonempty(event["authorization"]) or not _nonempty(event["subject"]):
        raise PolicyError("identifiers must be nonempty strings")
    if not _nonempty(event["revision"]) or not REVISION.match(event["revision"]):
        raise PolicyError("revision must be a canonical nonnegative decimal")
    if event["operation"] not in ("grant", "withdraw"):
        raise PolicyError("operation must be grant or withdraw")
    params = event["parameters"]
    if not isinstance(params, list) or not all(isinstance(p, str) for p in params):
        raise PolicyError("parameters must be a list of strings")
    for field in ("start", "end"):
        if not _nonempty(event[field]) or not DATE.match(event[field]):
            raise PolicyError("dates must be ISO YYYY-MM-DD")
    if not event["start"] < event["end"]:
        raise PolicyError("start must be strictly before end")


def _assemble(transport):
    if not isinstance(transport, dict):
        raise PolicyError("transport must be an object")
    commits = transport.get("commits")
    pages = transport.get("pages")
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise PolicyError("transport requires commits and pages lists")

    declared = {}
    for commit in commits:
        if not isinstance(commit, dict):
            raise PolicyError("commit must be an object")
        batch, parts, digest = commit.get("batch"), commit.get("parts"), commit.get("sha256")
        if not _nonempty(batch):
            raise PolicyError("commit batch must be a nonempty string")
        if not isinstance(parts, int) or isinstance(parts, bool) or parts <= 0:
            raise PolicyError("commit parts must be a positive integer")
        if not _nonempty(digest):
            raise PolicyError("commit sha256 must be a nonempty string")
        if batch in declared:
            if declared[batch] != (parts, digest):
                raise PolicyError("conflicting commit replay for a batch")
        else:
            declared[batch] = (parts, digest)

    seen = {}
    for page in pages:
        if not isinstance(page, dict):
            raise PolicyError("page must be an object")
        batch, part, events = page.get("batch"), page.get("part"), page.get("events")
        if not _nonempty(batch):
            raise PolicyError("page batch must be a nonempty string")
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            raise PolicyError("page part must be a zero-based integer")
        if not isinstance(events, list):
            raise PolicyError("page events must be a list")
        if batch not in declared:
            raise PolicyError("page for an uncommitted batch")
        if part >= declared[batch][0]:
            raise PolicyError("page part outside declared parts")
        key = (batch, part)
        if key in seen:
            if _canonical(seen[key]) != _canonical(events):
                raise PolicyError("conflicting page replay")
        else:
            seen[key] = events

    history = []
    for batch in sorted(declared):
        parts, digest = declared[batch]
        assembled = []
        for part in range(parts):
            if (batch, part) not in seen:
                raise PolicyError("missing part for a committed batch")
            assembled.extend(seen[(batch, part)])
        if hashlib.sha256(_canonical(assembled)).hexdigest() != digest:
            raise PolicyError("digest mismatch for a committed batch")
        history.extend(assembled)
    return history


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError("request must be an object")
    subjects = request.get("subjects")
    now = request.get("now")
    if not isinstance(subjects, list) or not all(_nonempty(s) for s in subjects):
        raise PolicyError("subjects must be a list of nonempty strings")
    if not _nonempty(now) or not DATE.match(now):
        raise PolicyError("now must be an ISO date")

    if "transport" in request:
        events = _assemble(request["transport"])
    else:
        events = request.get("events")
        if not isinstance(events, list):
            raise PolicyError("events must be a list")

    for event in events:
        _check_event(event)

    grouped = {}
    for event in events:
        key = (event["authorization"], int(event["revision"]))
        canonical = _canonical([event])
        if key in grouped:
            if grouped[key] != canonical:
                raise PolicyError("conflicting equal revisions for an authorization")
        else:
            grouped[key] = canonical

    winners = {}
    for event in events:
        auth = event["authorization"]
        rev = int(event["revision"])
        current = winners.get(auth)
        if current is None or rev > current[0]:
            winners[auth] = (rev, event)

    active = {}
    for rev, event in winners.values():
        if event["operation"] != "grant":
            continue
        if not (event["start"] <= now < event["end"]):
            continue
        subject = event["subject"]
        if subject in active:
            raise PolicyError("multiple active authorizations for a subject")
        active[subject] = event["parameters"]

    return {subject: active.get(subject) for subject in subjects}


if __name__ == "__main__":
    try:
        result = resolve(json.load(sys.stdin))
    except PolicyError as exc:
        sys.stderr.write("resolve: %s\n" % exc)
        sys.exit(1)
    except Exception as exc:
        sys.stderr.write("resolve: invalid request: %s\n" % exc)
        sys.exit(1)
    print(json.dumps(result, sort_keys=True))
