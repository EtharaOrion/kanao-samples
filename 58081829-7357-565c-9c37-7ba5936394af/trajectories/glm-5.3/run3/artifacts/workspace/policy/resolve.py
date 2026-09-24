"""Resolve the current signed authorization snapshot.

Implements docs/policy-reconciliation.md. The resolver validates the complete
candidate before projecting: transport envelope (commits, pages, parts,
digests, idempotent replays), the release manifest for a named generation,
every event in the history (including superseded, withdrawn, out-of-window and
unrequested-subject records), and the revision rules. Any fault raises, leaving
no partial result.
"""
import datetime
import hashlib
import json
import os
import sys

EVENT_KEYS = frozenset(("authorization", "revision", "subject", "operation",
                        "start", "end", "parameters"))
OPERATIONS = frozenset(("grant", "withdraw"))
HEX = frozenset("0123456789abcdef")


class Invalid(Exception):
    """The candidate is not admissible; the release must not use it."""


def _valid_date(value):
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" \
            or value[7] != "-" or not value[:4].isdigit() \
            or not value[5:7].isdigit() or not value[8:10].isdigit():
        return False
    try:
        datetime.date(int(value[:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        return False
    return True


def _canonical(events):
    return json.dumps(events, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _validate_event(event, where):
    if not isinstance(event, dict) or set(event) != EVENT_KEYS:
        raise Invalid("event keys are not exactly the required set (%s)" % where)
    for field in ("authorization", "subject"):
        if not isinstance(event[field], str) or not event[field]:
            raise Invalid("%s is not a nonempty string (%s)" % (field, where))
    revision = event["revision"]
    if not isinstance(revision, str) or not revision.isdigit() \
            or (len(revision) > 1 and revision[0] == "0"):
        raise Invalid("revision is not a canonical decimal string (%s)" % where)
    if event["operation"] not in OPERATIONS:
        raise Invalid("operation is neither grant nor withdraw (%s)" % where)
    params = event["parameters"]
    if not isinstance(params, list) \
            or not all(isinstance(p, str) for p in params):
        raise Invalid("parameters is not a list of strings (%s)" % where)
    if not _valid_date(event["start"]) or not _valid_date(event["end"]):
        raise Invalid("dates are not ISO YYYY-MM-DD (%s)" % where)
    if not event["start"] < event["end"]:
        raise Invalid("start is not strictly before end (%s)" % where)


def _check_manifest(transport, commits):
    """A generation's manifest says which batches its history holds."""
    generation = transport.get("generation")
    if generation is None:
        return
    if not isinstance(generation, str) or not generation:
        raise Invalid("generation is not a nonempty string")
    path = os.environ.get("MARROWFLEET_POLICY_MANIFESTS",
                          "/workspace/policy/manifests.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Invalid("cannot read the release manifests: %s" % exc)
    generations = manifest.get("generations") if isinstance(manifest, dict) else None
    record = generations.get(generation) if isinstance(generations, dict) else None
    if not isinstance(record, dict):
        raise Invalid("generation %r is not named by the release manifests"
                      % generation)
    batches = record.get("batches")
    if not isinstance(batches, dict):
        raise Invalid("generation %r carries no batch table" % generation)
    listed, committed = set(batches), set(commits)
    if listed != committed:
        raise Invalid("generation %r commits %s but its manifest lists %s"
                      % (generation, sorted(committed), sorted(listed)))
    for batch, digest in batches.items():
        if not isinstance(digest, str) or commits[batch][1] != digest:
            raise Invalid("batch %r is committed at a digest the manifest for "
                          "generation %r does not list" % (batch, generation))


def _assemble(transport):
    """Validate a segmented delivery and return the complete event history."""
    if not isinstance(transport, dict):
        raise Invalid("transport is not an object")
    commits, pages = transport.get("commits"), transport.get("pages")
    if not isinstance(commits, list) or not commits:
        raise Invalid("transport carries no commits")
    if not isinstance(pages, list):
        raise Invalid("transport pages are not a list")

    declared = {}
    for commit in commits:
        if not isinstance(commit, dict):
            raise Invalid("commit is not an object")
        batch = commit.get("batch")
        if not isinstance(batch, str) or not batch:
            raise Invalid("commit batch is not a nonempty string")
        if batch in declared:
            raise Invalid("commit %r is declared twice" % batch)
        parts = commit.get("parts")
        if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
            raise Invalid("commit %r parts is not a positive integer" % batch)
        digest = commit.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 \
                or not set(digest) <= HEX:
            raise Invalid("commit %r sha256 is not a digest" % batch)
        declared[batch] = (parts, digest)

    arrived = {}
    for page in pages:
        if not isinstance(page, dict):
            raise Invalid("page is not an object")
        batch, part, events = page.get("batch"), page.get("part"), page.get("events")
        if not isinstance(batch, str) or not batch:
            raise Invalid("page batch is not a nonempty string")
        if batch not in declared:
            raise Invalid("page %r arrives for an uncommitted batch" % batch)
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            raise Invalid("page %r part is not a zero-based integer" % batch)
        if not isinstance(events, list):
            raise Invalid("page %r part %r carries no events list" % (batch, part))
        for event in events:
            _validate_event(event, "batch %r part %r" % (batch, part))
        replayed = arrived.setdefault((batch, part), events)
        if replayed is not events and replayed != events:
            raise Invalid("page %r part %r replays with different events"
                          % (batch, part))

    history = []
    for batch, (parts, digest) in declared.items():
        for part in range(parts):
            if (batch, part) not in arrived:
                raise Invalid("batch %r is missing part %d" % (batch, part))
        events = [e for part in range(parts) for e in arrived[(batch, part)]]
        if hashlib.sha256(_canonical(events)).hexdigest() != digest:
            raise Invalid("batch %r does not match its commit digest" % batch)
        history.extend(events)

    _check_manifest(transport, declared)
    return history


def _project(request):
    for field in ("now", "subjects"):
        if field not in request:
            raise Invalid("request carries no %s" % field)
    now = request["now"]
    if not _valid_date(now):
        raise Invalid("evaluation instant is not ISO YYYY-MM-DD")
    subjects = request["subjects"]
    if not isinstance(subjects, list) \
            or not all(isinstance(s, str) and s for s in subjects):
        raise Invalid("subjects is not a list of nonempty strings")

    winners = {}
    for event in request["events"]:
        _validate_event(event, "history")
        authorization, revision = event["authorization"], event["revision"]
        current = winners.get(authorization)
        if current is None or int(revision) > int(current["revision"]):
            winners[authorization] = event
        elif revision == current["revision"] and event != current:
            raise Invalid("authorization %r carries conflicting revisions %s"
                          % (authorization, revision))

    active = {}
    for authorization, event in winners.items():
        if event["operation"] != "grant" or not event["start"] <= now < event["end"]:
            continue
        subject = event["subject"]
        if subject in active:
            raise Invalid("subject %r carries multiple active authorizations "
                          "(%s, %s)" % (subject, active[subject], authorization))
        active[subject] = authorization

    return {subject: winners[active[subject]]["parameters"]
            if subject in active else None for subject in subjects}


def resolve(request):
    if not isinstance(request, dict):
        raise Invalid("request is not an object")
    request = dict(request)
    transport = request.pop("transport", None)
    if transport is not None and "events" in request:
        raise Invalid("request carries both a transport and an events list")
    if transport is not None:
        request["events"] = _assemble(transport)
    if "events" not in request:
        raise Invalid("request carries neither a transport nor an events list")
    if not isinstance(request["events"], list):
        raise Invalid("events is not a list")
    return _project(request)


if __name__ == "__main__":
    try:
        print(json.dumps(resolve(json.load(sys.stdin)), sort_keys=True))
    except Invalid as exc:
        sys.stderr.write("policy resolver: %s\n" % exc)
        sys.exit(1)
    except ValueError as exc:
        sys.stderr.write("policy resolver: request is not valid JSON: %s\n" % exc)
        sys.exit(1)
