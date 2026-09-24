"""Resolve the current signed authorization snapshot.

Contract: docs/policy-reconciliation.md.  resolve(request) returns the corrected
authorization snapshot for the request's subjects; the CLI reads the same JSON
request on stdin and exits nonzero, without a partial result, when the request's
authorization history is invalid.
"""
import datetime
import hashlib
import json
import os
import re
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation", "start", "end", "parameters"}
OPERATIONS = {"grant", "withdraw"}
REVISION_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_MANIFESTS = "/workspace/policy/manifests.json"


class Invalid(Exception):
    pass


def _nonempty_string(value, what):
    if not isinstance(value, str) or not value:
        raise Invalid("%s must be a nonempty string" % what)
    return value


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _date(value, what):
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise Invalid("%s must be an ISO YYYY-MM-DD date" % what)
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise Invalid("%s is not a real calendar date" % what)


def _event(raw, index):
    if not isinstance(raw, dict):
        raise Invalid("event %d is not an object" % index)
    if set(raw) != EVENT_KEYS:
        raise Invalid("event %d keys are not exactly %s" % (index, sorted(EVENT_KEYS)))
    authorization = _nonempty_string(raw["authorization"], "event %d authorization" % index)
    subject = _nonempty_string(raw["subject"], "event %d subject" % index)
    revision = raw["revision"]
    if not isinstance(revision, str) or not REVISION_RE.match(revision):
        raise Invalid("event %d revision is not a canonical nonnegative decimal" % index)
    if raw["operation"] not in OPERATIONS:
        raise Invalid("event %d operation is not grant or withdraw" % index)
    parameters = raw["parameters"]
    if not isinstance(parameters, list) or not all(isinstance(p, str) for p in parameters):
        raise Invalid("event %d parameters is not a list of strings" % index)
    start = _date(raw["start"], "event %d start" % index)
    end = _date(raw["end"], "event %d end" % index)
    if not start < end:
        raise Invalid("event %d start is not strictly before end" % index)
    return (authorization, int(revision), subject, raw["operation"], raw["start"], raw["end"], tuple(parameters))


def _digest(events):
    payload = json.dumps(events, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _events_from_transport(transport):
    """Validate a segmented delivery and return its complete event history."""
    if not isinstance(transport, dict):
        raise Invalid("transport is not an object")
    for key in ("commits", "pages"):
        if key not in transport:
            raise Invalid("transport carries no %s" % key)
    commits = transport["commits"]
    pages = transport["pages"]
    if not isinstance(commits, list) or not commits:
        raise Invalid("transport commits is not a nonempty list")
    if not isinstance(pages, list) or not pages:
        raise Invalid("transport pages is not a nonempty list")

    declared = {}
    for commit in commits:
        if not isinstance(commit, dict) or set(commit) != {"batch", "parts", "sha256"}:
            raise Invalid("a commit does not carry exactly batch, parts and sha256")
        batch = _nonempty_string(commit["batch"], "commit batch")
        if not _is_int(commit["parts"]) or commit["parts"] < 1:
            raise Invalid("commit %s parts is not a positive integer" % batch)
        if not isinstance(commit["sha256"], str) or not SHA256_RE.match(commit["sha256"]):
            raise Invalid("commit %s sha256 is not a hex digest" % batch)
        if batch in declared and declared[batch] != commit:
            raise Invalid("commit %s is replayed with conflicting content" % batch)
        declared[batch] = commit

    seen = {}
    for page in pages:
        if not isinstance(page, dict) or set(page) != {"batch", "part", "events"}:
            raise Invalid("a page does not carry exactly batch, part and events")
        batch = _nonempty_string(page["batch"], "page batch")
        if batch not in declared:
            raise Invalid("page batch %s is not committed" % batch)
        if not _is_int(page["part"]) or page["part"] < 0:
            raise Invalid("page %s part is not a zero-based integer" % batch)
        if not isinstance(page["events"], list):
            raise Invalid("page %s part %d carries no events list" % (batch, page["part"]))
        for i, event in enumerate(page["events"]):
            _event(event, i)
        raw = json.dumps(page["events"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        key = (batch, page["part"])
        if key in seen and seen[key] != raw:
            raise Invalid("page %s part %s is replayed with conflicting content" % key)
        seen[key] = raw

    history = []
    for batch, commit in declared.items():
        parts = []
        for part in range(commit["parts"]):
            if (batch, part) not in seen:
                raise Invalid("batch %s is missing part %d" % (batch, part))
            parts.extend(json.loads(seen[(batch, part)]))
        for page_batch, part in seen:
            if page_batch == batch and part >= commit["parts"]:
                raise Invalid("batch %s carries part %d beyond its commit" % (batch, part))
        if _digest(parts) != commit["sha256"]:
            raise Invalid("batch %s does not match its committed digest" % batch)
        history.extend(_transport_events(parts, batch))

    generation = transport.get("generation")
    if generation is not None:
        _check_generation(generation, declared)
    return history


def _transport_events(parts, batch):
    return [_event(event, i) for i, event in enumerate(parts)]


def _check_generation(generation, declared):
    _nonempty_string(generation, "generation")
    path = os.environ.get("MARROWFLEET_POLICY_MANIFESTS") or DEFAULT_MANIFESTS
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifests = json.load(handle)
    except (OSError, ValueError):
        raise Invalid("the release manifests at %s cannot be read" % path)
    if not isinstance(manifests, dict) or not isinstance(manifests.get("generations"), dict):
        raise Invalid("the release manifests are not shaped as the contract describes")
    generations = manifests["generations"]
    if generation not in generations:
        raise Invalid("generation %s is not named by the release manifests" % generation)
    entry = generations[generation]
    if not isinstance(entry, dict) or not isinstance(entry.get("batches"), dict):
        raise Invalid("generation %s carries no batch manifest" % generation)
    batches = entry["batches"]
    for batch, digest in batches.items():
        _nonempty_string(batch, "manifest batch")
        if not isinstance(digest, str) or not SHA256_RE.match(digest):
            raise Invalid("manifest batch %s carries no hex digest" % batch)
    if set(batches) != set(declared):
        raise Invalid("generation %s does not commit exactly the batches its manifest lists" % generation)
    for batch, digest in batches.items():
        if declared[batch]["sha256"] != digest:
            raise Invalid("batch %s does not match the digest its manifest lists" % batch)


def _corrected(request):
    """Validate the complete history and return {authorization: winning event}."""
    if "transport" in request:
        history = _events_from_transport(request["transport"])
    elif "events" in request:
        raw = request["events"]
        if not isinstance(raw, list):
            raise Invalid("events is not a list")
        history = [_event(event, i) for i, event in enumerate(raw)]
    else:
        raise Invalid("the request carries neither transport nor events")

    revisions = {}
    winners = {}
    for event in history:
        authorization, revision = event[0], event[1]
        previous = revisions.get((authorization, revision))
        if previous is not None and previous != event:
            raise Invalid("authorization %s revision %d is conflicting" % (authorization, revision))
        revisions[(authorization, revision)] = event
        winner = winners.get(authorization)
        if winner is None or revision > winner[1]:
            winners[authorization] = event
    return winners


def resolve(request):
    if not isinstance(request, dict):
        raise Invalid("the request is not an object")
    now = _date(request.get("now"), "now")
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        raise Invalid("subjects is not a nonempty list")
    for subject in subjects:
        _nonempty_string(subject, "subject")

    active = {}
    for event in _corrected(request).values():
        _, _, subject, operation, start, end, parameters = event
        if operation != "grant":
            continue
        if not (datetime.date.fromisoformat(start) <= now < datetime.date.fromisoformat(end)):
            continue
        if subject in active:
            raise Invalid("subject %s holds more than one active authorization" % subject)
        active[subject] = list(parameters)
    return {subject: active.get(subject) for subject in subjects}


def main(argv):
    try:
        request = json.load(sys.stdin)
        result = resolve(request)
    except Invalid as error:
        sys.stderr.write("resolve: %s\n" % error)
        return 1
    except (ValueError, TypeError, KeyError) as error:
        sys.stderr.write("resolve: malformed request: %s\n" % error)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
