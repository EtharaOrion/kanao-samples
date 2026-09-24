"""Resolve the current signed authorization snapshot.

Implements docs/policy-reconciliation.md: a request carries either an events
list or a segmented transport (with or without a named generation). The
complete history is validated before anything is projected; any fault raises
ResolveError, which the CLI turns into a nonzero exit with no partial result.
"""
import hashlib
import json
import os
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation", "start", "end", "parameters"}
OPERATIONS = {"grant", "withdraw"}
MANIFEST_PATH = os.environ.get("MARROWFLEET_POLICY_MANIFESTS", "/workspace/policy/manifests.json")


class ResolveError(Exception):
    pass


def _nonempty_str(value):
    return isinstance(value, str) and value != ""


def _revision(value):
    """A canonical nonnegative decimal string: no leading zeros, no sign."""
    if not isinstance(value, str) or not value.isdigit():
        return None
    if len(value) > 1 and value[0] == "0":
        return None
    return int(value)


def _date(value):
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
        return None
    year, month, day = value[:4], value[5:7], value[8:10]
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return None
    month, day = int(month), int(day)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return value


def _history(request):
    """Yield the complete authorization history, fully validated."""
    if "transport" in request:
        source = request["transport"]
        if "events" in request:
            raise ResolveError("request carries both events and a transport")
    else:
        source = request.get("events")
    if isinstance(source, list):
        for event in source:
            if not isinstance(event, dict):
                raise ResolveError("event is not an object")
            yield event
        return
    if not isinstance(source, dict):
        raise ResolveError("request events is neither a list nor a transport")
    transport = source
    generation = transport.get("generation")
    commits = transport.get("commits")
    pages = transport.get("pages")
    if generation is not None and not _nonempty_str(generation):
        raise ResolveError("transport generation is not a nonempty string")
    if not isinstance(commits, list) or not commits:
        raise ResolveError("transport commits is not a nonempty list")
    if not isinstance(pages, list):
        raise ResolveError("transport pages is not a list")

    committed = {}
    for commit in commits:
        if not isinstance(commit, dict):
            raise ResolveError("commit is not an object")
        if set(commit) != {"batch", "parts", "sha256"}:
            raise ResolveError("commit keys are not exactly batch, parts, sha256")
        batch, parts, digest = commit["batch"], commit["parts"], commit["sha256"]
        if not _nonempty_str(batch):
            raise ResolveError("commit batch is not a nonempty string")
        if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
            raise ResolveError("commit parts is not a positive integer")
        if not _nonempty_str(digest) or len(digest) != 64 or digest.strip("0123456789abcdef") != "":
            raise ResolveError("commit sha256 is not a digest")
        if batch in committed:
            if committed[batch] != (parts, digest):
                raise ResolveError("conflicting replay of commit for batch %r" % batch)
        else:
            committed[batch] = (parts, digest)

    assembled = {}
    seen_pages = set()
    for page in pages:
        if not isinstance(page, dict):
            raise ResolveError("page is not an object")
        if set(page) != {"batch", "part", "events"}:
            raise ResolveError("page keys are not exactly batch, part, events")
        batch, part = page["batch"], page["part"]
        if not _nonempty_str(batch):
            raise ResolveError("page batch is not a nonempty string")
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            raise ResolveError("page part is not a zero-based integer")
        page_events = page["events"]
        if not isinstance(page_events, list):
            raise ResolveError("page events is not a list")
        key = (batch, part)
        if key in seen_pages:
            if assembled[batch][part] != page_events:
                raise ResolveError("conflicting replay of page %r part %d" % (batch, part))
            continue
        seen_pages.add(key)
        assembled.setdefault(batch, {})[part] = page_events

    if generation is not None:
        with open(MANIFEST_PATH, encoding="utf-8") as handle:
            manifests = json.load(handle)
        generations = manifests.get("generations") if isinstance(manifests, dict) else None
        if not isinstance(generations, dict):
            raise ResolveError("manifests file is not a generation record")
        listed = generations.get(generation)
        if not isinstance(listed, dict) or not isinstance(listed.get("batches"), dict):
            raise ResolveError("generation %r is not named by the manifest" % generation)
        for batch, digest in listed["batches"].items():
            if batch not in committed:
                raise ResolveError("generation %r lists batch %r the transport does not commit"
                                   % (generation, batch))
            if committed[batch][1] != digest:
                raise ResolveError("batch %r commits a digest the manifest does not list for it"
                                   % batch)
        for batch in committed:
            if batch not in listed["batches"]:
                raise ResolveError("transport commits batch %r the manifest of generation %r "
                                   "does not list" % (batch, generation))

    for batch in committed:
        parts, digest = committed[batch]
        pages_of = assembled.get(batch, {})
        if set(pages_of) != set(range(parts)):
            raise ResolveError("batch %r is committed at %d parts but pages %r arrived"
                               % (batch, parts, sorted(pages_of)))
        events_of = [event for part in range(parts) for event in pages_of[part]]
        payload = json.dumps(events_of, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ResolveError("batch %r does not assemble at the digest it commits" % batch)
    for batch in set(assembled) - set(committed):
        raise ResolveError("batch %r arrived uncommitted" % batch)

    for batch in sorted(committed):
        parts = committed[batch][0]
        pages_of = assembled[batch]
        for part in range(parts):
            for event in pages_of[part]:
                if not isinstance(event, dict):
                    raise ResolveError("event is not an object")
                yield event


def _validated(events):
    for event in events:
        if set(event) != EVENT_KEYS:
            raise ResolveError("event keys are not exactly the event keys")
        if not _nonempty_str(event["authorization"]):
            raise ResolveError("event authorization is not a nonempty string")
        if not _nonempty_str(event["subject"]):
            raise ResolveError("event subject is not a nonempty string")
        if _revision(event["revision"]) is None:
            raise ResolveError("event revision %r is not a canonical nonnegative decimal"
                               % (event["revision"],))
        if event["operation"] not in OPERATIONS:
            raise ResolveError("event operation %r is neither grant nor withdraw"
                               % (event["operation"],))
        parameters = event["parameters"]
        if not isinstance(parameters, list) or not all(_nonempty_str(p) for p in parameters):
            raise ResolveError("event parameters is not a list of nonempty strings")
        start, end = _date(event["start"]), _date(event["end"])
        if start is None or end is None or not start < end:
            raise ResolveError("event interval %r..%r is not ISO with start before end"
                               % (event["start"], event["end"]))
        yield event


def resolve(request):
    if not isinstance(request, dict):
        raise ResolveError("request is not an object")
    now = request.get("now")
    if _date(now) is None:
        raise ResolveError("request now is not an ISO date")
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not all(_nonempty_str(s) for s in subjects):
        raise ResolveError("request subjects is not a list of nonempty strings")

    revisions = {}
    winners = {}
    known_subjects = set()
    for event in _validated(_history(request)):
        authorization = event["authorization"]
        revision = _revision(event["revision"])
        known_subjects.add(event["subject"])
        seen = revisions.get((authorization, revision))
        if seen is None:
            revisions[(authorization, revision)] = event
        elif seen != event:
            raise ResolveError("conflicting equal revisions for %r at %d"
                               % (authorization, revision))
        current = winners.get(authorization)
        if current is None or revision > current[0]:
            winners[authorization] = (revision, event)

    active = {}
    for _revision_value, event in winners.values():
        if event["operation"] == "withdraw":
            continue
        if not (event["start"] <= now < event["end"]):
            continue
        subject = event["subject"]
        if subject in active:
            raise ResolveError("multiple active authorizations for subject %r" % subject)
        active[subject] = event["parameters"]

    return {subject: active.get(subject) for subject in subjects if subject in known_subjects}


if __name__ == "__main__":
    try:
        resolved = resolve(json.load(sys.stdin))
    except (ResolveError, ValueError, OSError, KeyError, TypeError) as fault:
        sys.stderr.write("resolve: %s\n" % fault)
        sys.exit(1)
    print(json.dumps(resolved, sort_keys=True))
