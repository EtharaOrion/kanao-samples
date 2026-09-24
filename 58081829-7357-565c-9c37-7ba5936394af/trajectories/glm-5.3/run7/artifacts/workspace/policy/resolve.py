"""Resolve the current signed authorization snapshot.

Implements the signed authorization reconciliation contract in
docs/policy-reconciliation.md: complete validation of the delivery
envelope and of every event in the history before any projection, then
revision supersede/withdraw resolution keyed on the authorization, not
the subject. Invalid input raises InvalidHistory; the CLI exits nonzero
and prints no partial result.
"""
import datetime
import hashlib
import json
import os
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation",
              "start", "end", "parameters"}
OPERATIONS = {"grant", "withdraw"}
DEFAULT_MANIFESTS = "/workspace/policy/manifests.json"


class InvalidHistory(Exception):
    pass


def _bad(message):
    raise InvalidHistory(message)


def _is_nonempty_str(value):
    return isinstance(value, str) and value != ""


def _is_canonical_revision(value):
    if not isinstance(value, str) or not value.isdigit():
        return False
    return value == "0" or not value.startswith("0")


def _is_date(value):
    if not isinstance(value, str):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _check_event(event, where):
    if not isinstance(event, dict):
        _bad("event is not an object at %s" % where)
    if set(event) != EVENT_KEYS:
        _bad("event keys are not exactly the contract keys at %s" % where)
    for field in ("authorization", "revision", "subject", "operation",
                  "start", "end"):
        if not _is_nonempty_str(event[field]):
            _bad("%s is not a nonempty string at %s" % (field, where))
    if not _is_canonical_revision(event["revision"]):
        _bad("revision is not a canonical nonnegative decimal at %s" % where)
    if event["operation"] not in OPERATIONS:
        _bad("operation is not grant or withdraw at %s" % where)
    if not _is_date(event["start"]) or not _is_date(event["end"]):
        _bad("start and end must be ISO dates at %s" % where)
    if not (event["start"] < event["end"]):
        _bad("start is not strictly before end at %s" % where)
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(
            _is_nonempty_str(p) for p in parameters):
        _bad("parameters is not a list of nonempty strings at %s" % where)


def _check_commit(commit, where):
    if not isinstance(commit, dict):
        _bad("commit is not an object at %s" % where)
    if set(commit) != {"batch", "parts", "sha256"}:
        _bad("commit keys are not exactly the contract keys at %s" % where)
    if not _is_nonempty_str(commit["batch"]):
        _bad("commit batch is not a nonempty string at %s" % where)
    parts = commit["parts"]
    if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
        _bad("commit parts is not a positive integer at %s" % where)
    digest = commit["sha256"]
    if not _is_nonempty_str(digest) or len(digest) != 64 \
            or any(c not in "0123456789abcdef" for c in digest):
        _bad("commit sha256 is not a hex digest at %s" % where)


def _check_page(page, where):
    if not isinstance(page, dict):
        _bad("page is not an object at %s" % where)
    if set(page) != {"batch", "part", "events"}:
        _bad("page keys are not exactly the contract keys at %s" % where)
    if not _is_nonempty_str(page["batch"]):
        _bad("page batch is not a nonempty string at %s" % where)
    part = page["part"]
    if not isinstance(part, int) or isinstance(part, bool) or part < 0:
        _bad("page part is not a zero-based integer at %s" % where)
    if not isinstance(page["events"], list):
        _bad("page events is not a list at %s" % where)


def _digest(events):
    payload = json.dumps(events, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _batch_sort_key(batch):
    return (0, int(batch), "") if batch.isdigit() else (1, 0, batch)


def _check_generation(transport, by_batch):
    generation = transport["generation"]
    if not _is_nonempty_str(generation):
        _bad("generation is not a nonempty string")
    path = os.environ.get("MARROWFLEET_POLICY_MANIFESTS",
                          DEFAULT_MANIFESTS)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        _bad("cannot read release manifests at %s" % path)
    generations = manifest.get("generations") if isinstance(manifest, dict) \
        else None
    record = generations.get(generation) if isinstance(generations, dict) \
        else None
    if not isinstance(record, dict):
        _bad("manifest names no generation %s" % generation)
    batches = record.get("batches")
    if not isinstance(batches, dict):
        _bad("generation %s carries no batch record" % generation)
    committed = {batch: commit["sha256"] for batch, commit in by_batch.items()}
    if set(committed) != set(batches):
        _bad("generation %s commits %s against manifest batches %s"
             % (generation, sorted(committed), sorted(batches)))
    for batch, digest in batches.items():
        if not isinstance(digest, str) or committed[batch] != digest:
            _bad("batch %s does not match the manifest digest" % batch)


def _history_from_transport(transport):
    """Validate the delivery envelope and return the complete event history."""
    if not isinstance(transport, dict):
        _bad("transport is not an object")
    expected = {"commits", "pages"}
    if "generation" in transport:
        expected.add("generation")
    if set(transport) != expected:
        _bad("transport keys are not exactly the contract keys")
    commits = transport["commits"]
    pages = transport["pages"]
    if not isinstance(commits, list) or not commits:
        _bad("transport commits is not a nonempty list")
    if not isinstance(pages, list) or not pages:
        _bad("transport pages is not a nonempty list")

    by_batch = {}
    for index, commit in enumerate(commits):
        _check_commit(commit, "commit %d" % index)
        previous = by_batch.get(commit["batch"])
        if previous is not None and previous != commit:
            _bad("conflicting replay of commit for batch %s" % commit["batch"])
        by_batch[commit["batch"]] = commit

    page_by_batch = {}
    for index, page in enumerate(pages):
        _check_page(page, "page %d" % index)
        if page["batch"] not in by_batch:
            _bad("page %d carries uncommitted batch %s"
                 % (index, page["batch"]))
        for offset, event in enumerate(page["events"]):
            _check_event(event, "page %d event %d" % (index, offset))
        slot = page_by_batch.setdefault(page["batch"], {})
        previous = slot.get(page["part"])
        if previous is not None and previous != page["events"]:
            _bad("conflicting replay of page %s part %d"
                 % (page["batch"], page["part"]))
        slot[page["part"]] = page["events"]

    if "generation" in transport:
        _check_generation(transport, by_batch)

    history = []
    for batch in sorted(by_batch, key=_batch_sort_key):
        commit = by_batch[batch]
        slot = page_by_batch.get(batch, {})
        for part in range(commit["parts"]):
            if part not in slot:
                _bad("batch %s is missing part %d" % (batch, part))
        extra = [part for part in slot if not 0 <= part < commit["parts"]]
        if extra:
            _bad("batch %s carries parts outside its commit %s"
                 % (batch, sorted(extra)))
        events = []
        for part in range(commit["parts"]):
            events.extend(slot[part])
        if _digest(events) != commit["sha256"]:
            _bad("batch %s does not match its committed digest" % batch)
        history.extend(events)
    return history


def _history_from_events(events):
    if not isinstance(events, list):
        _bad("events is not a list")
    for index, event in enumerate(events):
        _check_event(event, "event %d" % index)
    return events


def resolve(request):
    if not isinstance(request, dict):
        _bad("request is not an object")
    if set(request) - {"now", "subjects", "events", "transport"}:
        _bad("request carries keys outside the contract")
    if not _is_date(request.get("now")):
        _bad("now is not an ISO date")
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not subjects \
            or not all(_is_nonempty_str(s) for s in subjects):
        _bad("subjects is not a nonempty list of nonempty strings")
    if len(set(subjects)) != len(subjects):
        _bad("subjects carries duplicates")
    if "transport" in request:
        if "events" in request:
            _bad("request carries both events and transport")
        history = _history_from_transport(request["transport"])
    elif "events" in request:
        history = _history_from_events(request["events"])
    else:
        _bad("request carries neither events nor transport")

    now = request["now"]
    by_revision = {}
    for event in history:
        key = (event["authorization"], int(event["revision"]))
        current = by_revision.get(key)
        if current is not None and current != event:
            _bad("authorization %s carries conflicting revisions %s"
                 % (event["authorization"], event["revision"]))
        by_revision[key] = event

    winners = {}
    for (authorization, revision), event in by_revision.items():
        current = winners.get(authorization)
        if current is None or revision > int(current["revision"]):
            winners[authorization] = event

    active = {}
    for authorization in sorted(winners):
        event = winners[authorization]
        if event["operation"] != "grant":
            continue
        if not (event["start"] <= now < event["end"]):
            continue
        subject = event["subject"]
        if subject in active:
            _bad("subject %s carries multiple active authorizations"
                 % subject)
        active[subject] = event["parameters"]

    known = {event["subject"] for event in history}
    return {subject: active.get(subject)
            for subject in subjects if subject in known}


def main(argv):
    try:
        request = json.load(sys.stdin)
    except ValueError:
        sys.stderr.write("resolve: request is not JSON\n")
        return 1
    try:
        result = resolve(request)
    except InvalidHistory as error:
        sys.stderr.write("resolve: %s\n" % error)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
