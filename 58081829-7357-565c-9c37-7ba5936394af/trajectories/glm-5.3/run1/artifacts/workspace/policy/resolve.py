"""Validate and resolve the signed authorization snapshot."""

import datetime
import json
import re
import sys


class PolicyError(ValueError):
    """The authorization request is invalid."""


_EVENT_KEYS = {
    "authorization", "revision", "subject", "operation", "start", "end",
    "parameters",
}
_REVISION = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _mapping(value, what):
    if not isinstance(value, dict):
        raise PolicyError("%s must be an object" % what)
    return value


def _string(value, what, nonempty=True):
    if type(value) is not str or (nonempty and not value):
        raise PolicyError("%s must be a%s string" %
                          (what, " nonempty" if nonempty else ""))
    return value


def _date(value, what):
    if type(value) is not str or len(value) != 10:
        raise PolicyError("%s must be an ISO YYYY-MM-DD date" % what)
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise PolicyError("%s must be an ISO YYYY-MM-DD date" % what) from exc
    if parsed.isoformat() != value:
        raise PolicyError("%s must be an ISO YYYY-MM-DD date" % what)
    return parsed


def _revision(value):
    if type(value) is not str or not _REVISION.fullmatch(value):
        raise PolicyError("revision must be a canonical nonnegative decimal")
    return int(value, 10)


def _validated_event(event):
    event = _mapping(event, "event")
    if set(event) != _EVENT_KEYS:
        raise PolicyError("event keys are not the exact supported set")

    authorization = _string(event["authorization"], "authorization")
    subject = _string(event["subject"], "subject")
    operation = event["operation"]
    if operation not in ("grant", "withdraw"):
        raise PolicyError("operation must be grant or withdraw")
    revision = _revision(event["revision"])

    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(
            type(parameter) is str for parameter in parameters):
        raise PolicyError("parameters must be a list of strings")

    start = _date(event["start"], "start")
    end = _date(event["end"], "end")
    if start >= end:
        raise PolicyError("start must be before end")

    # A private tuple preserves equality while avoiding JSON-specific compare
    # behavior for duplicate revisions.
    normalized = (
        authorization,
        int(revision),
        subject,
        operation,
        event["start"],
        event["end"],
        tuple(parameters),
    )
    return {
        "authorization": authorization,
        "revision": revision,
        "subject": subject,
        "operation": operation,
        "start": start,
        "end": end,
        "parameters": tuple(parameters),
        "normalized": normalized,
    }


def _events_from_transport(request):
    transport = _mapping(request.get("transport"), "transport")
    commits = transport.get("commits")
    pages = transport.get("pages")
    if not isinstance(commits, list) or not isinstance(pages, list):
        raise PolicyError("transport commits and pages must be arrays")

    commit_by_batch = {}
    for commit in commits:
        commit = _mapping(commit, "commit")
        for key in ("batch", "parts", "sha256"):
            if key not in commit:
                raise PolicyError("commit is missing %s" % key)
        batch = _string(commit["batch"], "commit batch")
        parts = commit["parts"]
        if type(parts) is not int or isinstance(parts, bool) or parts < 1:
            raise PolicyError("commit parts must be a positive integer")
        digest = _string(commit["sha256"], "commit sha256")
        if not _SHA256.fullmatch(digest):
            raise PolicyError("commit sha256 is not a SHA-256 digest")
        repeated = (batch, parts, digest.lower())
        old = commit_by_batch.get(batch)
        if old is not None and old != repeated:
            raise PolicyError("conflicting repeated commit for %s" % batch)
        commit_by_batch[batch] = repeated

    page_by_part = {}
    for page in pages:
        page = _mapping(page, "page")
        for key in ("batch", "part", "events"):
            if key not in page:
                raise PolicyError("page is missing %s" % key)
        batch = _string(page["batch"], "page batch")
        part = page["part"]
        if type(part) is not int or isinstance(part, bool) or part < 0:
            raise PolicyError("page part must be a zero-based integer")
        if not isinstance(page["events"], list):
            raise PolicyError("page events must be an array")
        key = (batch, part)
        old = page_by_part.get(key)
        if old is not None and old != page["events"]:
            raise PolicyError("conflicting repeated page %s part %s" % key)
        page_by_part[key] = page["events"]

    history = []
    imported_batches = set()
    import hashlib
    for commit in commits:
        batch = commit["batch"]
        if batch in imported_batches:
            continue
        imported_batches.add(batch)
        parts = commit["parts"]
        events = []
        for part in range(parts):
            key = (batch, part)
            if key not in page_by_part:
                raise PolicyError("missing page %s part %s" % key)
            events.extend(page_by_part[key])
        payload = json.dumps(
            events, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        if digest != commit["sha256"].lower():
            raise PolicyError("batch %s digest mismatch" % batch)
        history.extend(events)

    for key in page_by_part:
        if key[0] not in commit_by_batch:
            raise PolicyError("uncommitted page %s part %s" % key)

    return history


def resolve(request):
    request = _mapping(request, "request")
    if "transport" in request:
        if "events" in request:
            raise PolicyError("request cannot carry both events and transport")
        events = _events_from_transport(request)
    else:
        events = request.get("events")
        if not isinstance(events, list):
            raise PolicyError("events must be an array")

    now = _date(request.get("now"), "now")
    subjects = request.get("subjects")
    if not isinstance(subjects, list):
        raise PolicyError("subjects must be an array")
    requested = []
    for subject in subjects:
        _string(subject, "subject")
        if subject not in requested:
            requested.append(subject)

    validated = [_validated_event(event) for event in events]

    corrected = {}
    seen_revisions = {}
    for event in validated:
        authorization = event["authorization"]
        revision = event["revision"]
        seen_key = (authorization, revision)
        old_seen = seen_revisions.get(seen_key)
        if old_seen is not None and event["normalized"] != old_seen["normalized"]:
            raise PolicyError(
                "conflicting revision %s for %s" % (revision, authorization)
            )
        if old_seen is None:
            seen_revisions[seen_key] = event

        old = corrected.get(authorization)
        if old is None or revision > old["revision"]:
            corrected[authorization] = event

    active = {}
    for event in corrected.values():
        if (event["operation"] == "grant" and
                event["start"] <= now < event["end"]):
            subject = event["subject"]
            if subject in active:
                raise PolicyError(
                    "multiple active authorizations for %s" % subject
                )
            active[subject] = event

    return {
        subject: list(active[subject]["parameters"])
        if subject in active else None
        for subject in requested
    }


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        result = resolve(request)
    except (PolicyError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        sys.stderr.write("policy resolver: %s\n" % exc)
        sys.exit(1)
    print(json.dumps(result, sort_keys=True))
