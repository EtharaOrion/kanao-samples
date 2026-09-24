"""Resolve the current signed authorization snapshot.

Reads a reconciliation request (JSON on stdin, or via resolve(request)) and
returns {subject: parameters | None} for every requested subject.

A request carries either `events` or a segmented `transport`. A transport may
name a `generation`, in which case the batches it commits must be exactly the
batches that generation's release manifest lists, each at the manifest's
digest. The complete history is validated before anything is projected; any
invalid input raises Invalid (the CLI exits nonzero) with no partial result.
"""
import datetime
import hashlib
import json
import os
import re
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation", "start", "end", "parameters"}
OPERATIONS = ("grant", "withdraw")
REVISION_RE = re.compile(r"0|[1-9][0-9]*\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
DEFAULT_MANIFESTS = "/workspace/policy/manifests.json"


class Invalid(Exception):
    """Raised when a request, its history or its projection is invalid."""


def _nonempty(value):
    return isinstance(value, str) and value != ""


def _date(value, where):
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise Invalid("%s: date %r is not ISO YYYY-MM-DD" % (where, value))
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise Invalid("%s: date %r is not ISO YYYY-MM-DD" % (where, value))
    return value


def _event_digest(events):
    payload = json.dumps(events, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_event(event, where):
    if not isinstance(event, dict):
        raise Invalid("%s: event is not an object" % where)
    keys = set(event)
    if keys != EVENT_KEYS:
        missing = sorted(EVENT_KEYS - keys)
        extra = sorted(keys - EVENT_KEYS)
        raise Invalid("%s: event keys wrong (missing %s, unexpected %s)" % (where, missing, extra))
    for field in ("authorization", "subject"):
        if not _nonempty(event[field]):
            raise Invalid("%s: %s is not a nonempty string" % (where, field))
    revision = event["revision"]
    if not isinstance(revision, str) or not REVISION_RE.match(revision):
        raise Invalid("%s: revision %r is not a canonical nonnegative decimal" % (where, revision))
    if event["operation"] not in OPERATIONS:
        raise Invalid("%s: operation %r is not grant or withdraw" % (where, event["operation"]))
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(isinstance(p, str) for p in parameters):
        raise Invalid("%s: parameters is not a list of strings" % where)
    start = _date(event["start"], where)
    end = _date(event["end"], where)
    if not start < end:
        raise Invalid("%s: start %s is not strictly before end %s" % (where, start, end))


def _load_manifests():
    path = os.environ.get("MARROWFLEET_POLICY_MANIFESTS") or DEFAULT_MANIFESTS
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Invalid("release manifests unreadable at %s: %s" % (path, exc))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("generations"), dict):
        raise Invalid("release manifests at %s are not shaped as expected" % path)
    return manifest["generations"]


def _assemble(request):
    """Validate the delivery envelope and return the complete event history."""
    transport = request.get("transport")
    if transport is None:
        if "events" not in request:
            raise Invalid("request carries neither events nor a transport")
        events = request["events"]
        if not isinstance(events, list):
            raise Invalid("events is not a list")
        for index, event in enumerate(events):
            _validate_event(event, "event %d" % index)
        return list(events)

    if not isinstance(transport, dict):
        raise Invalid("transport is not an object")
    if "events" in request:
        raise Invalid("request carries both events and a transport")

    commits = transport.get("commits")
    pages = transport.get("pages")
    if not isinstance(commits, list) or not commits:
        raise Invalid("transport carries no commits")
    if not isinstance(pages, list):
        raise Invalid("transport pages is not a list")

    committed = {}
    for index, commit in enumerate(commits):
        where = "commit %d" % index
        if not isinstance(commit, dict):
            raise Invalid("%s: not an object" % where)
        if set(commit) != {"batch", "parts", "sha256"}:
            raise Invalid("%s: commit keys are wrong" % where)
        batch = commit["batch"]
        if not _nonempty(batch):
            raise Invalid("%s: batch is not a nonempty string" % where)
        parts = commit["parts"]
        if isinstance(parts, bool) or not isinstance(parts, int) or parts < 1:
            raise Invalid("%s: parts %r is not a positive integer" % (where, parts))
        digest = commit["sha256"]
        if not isinstance(digest, str) or not SHA256_RE.match(digest):
            raise Invalid("%s: sha256 is not a digest" % where)
        previous = committed.get(batch)
        if previous is None:
            committed[batch] = (parts, digest)
        elif previous != (parts, digest):
            raise Invalid("batch %s is committed twice with conflicting commits" % batch)

    seen_pages = {}
    for index, page in enumerate(pages):
        where = "page %d" % index
        if not isinstance(page, dict):
            raise Invalid("%s: not an object" % where)
        if set(page) != {"batch", "part", "events"}:
            raise Invalid("%s: page keys are wrong" % where)
        batch = page["batch"]
        if not _nonempty(batch):
            raise Invalid("%s: batch is not a nonempty string" % where)
        part = page["part"]
        if isinstance(part, bool) or not isinstance(part, int) or part < 0:
            raise Invalid("%s: part %r is not a zero-based integer" % (where, part))
        if batch not in committed:
            raise Invalid("%s: its batch %s is not committed" % (where, batch))
        if not isinstance(page["events"], list):
            raise Invalid("%s: events is not a list" % where)
        for position, event in enumerate(page["events"]):
            _validate_event(event, "%s event %d" % (where, position))
        identity = json.dumps(page["events"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        previous = seen_pages.get((batch, part))
        if previous is None:
            seen_pages[(batch, part)] = identity
        elif previous != identity:
            raise Invalid("batch %s part %d is replayed with conflicting events" % (batch, part))

    history = []
    digests = {}
    for commit in commits:
        batch = commit["batch"]
        if batch in digests:
            continue
        parts = committed[batch][0]
        by_part = {}
        for (b, part), identity in seen_pages.items():
            if b == batch:
                by_part[part] = identity
        for part in range(parts):
            if part not in by_part:
                raise Invalid("batch %s is missing part %d of %d" % (batch, part, parts))
        events = [event for part in range(parts) for event in json.loads(by_part[part])]
        digest = _event_digest(events)
        if digest != committed[batch][1]:
            raise Invalid("batch %s does not match its commit digest" % batch)
        digests[batch] = digest
        history.extend(events)

    for (batch, part) in seen_pages:
        if part >= committed[batch][0]:
            raise Invalid("batch %s carries part %d beyond its declared parts" % (batch, part))

    generation = transport.get("generation")
    if generation is not None:
        if not _nonempty(generation):
            raise Invalid("generation is not a nonempty string")
        generations = _load_manifests()
        record = generations.get(generation)
        if not isinstance(record, dict) or not isinstance(record.get("batches"), dict):
            raise Invalid("generation %s is not named by the release manifests" % generation)
        listed = record["batches"]
        for batch in listed:
            if batch not in digests:
                raise Invalid("generation %s lists batch %s that the transport does not commit" % (generation, batch))
            if listed[batch] != digests[batch]:
                raise Invalid("batch %s does not match generation %s manifest digest" % (batch, generation))
        for batch in digests:
            if batch not in listed:
                raise Invalid("transport commits batch %s that generation %s does not list" % (batch, generation))

    return history


def resolve(request):
    if not isinstance(request, dict):
        raise Invalid("request is not an object")
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not all(_nonempty(s) for s in subjects):
        raise Invalid("request subjects are not a list of nonempty strings")
    now = _date(request.get("now"), "request now")

    history = _assemble(request)

    by_authorization = {}
    for event in history:
        revisions = by_authorization.setdefault(event["authorization"], {})
        revision = event["revision"]
        previous = revisions.get(revision)
        if previous is None:
            revisions[revision] = event
        elif previous != event:
            raise Invalid("authorization %s revision %s is recorded twice with conflicting events"
                          % (event["authorization"], revision))

    active = {}
    for authorization, revisions in by_authorization.items():
        winner = revisions[max(revisions, key=int)]
        if winner["operation"] != "grant":
            continue
        if not (winner["start"] <= now < winner["end"]):
            continue
        subject = winner["subject"]
        if subject in active:
            raise Invalid("subject %s carries more than one active authorization (%s, %s)"
                          % (subject, active[subject][1], authorization))
        active[subject] = (winner["parameters"], authorization)

    return {subject: active[subject][0] if subject in active else None for subject in subjects}


def main(argv):
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write("resolve: request is not JSON: %s\n" % exc)
        return 1
    try:
        result = resolve(request)
    except Invalid as exc:
        sys.stderr.write("resolve: %s\n" % exc)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
