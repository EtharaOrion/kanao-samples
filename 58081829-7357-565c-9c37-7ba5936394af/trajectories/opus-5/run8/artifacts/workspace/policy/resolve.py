"""Resolve the current signed authorization snapshot.

The contract this implements is `docs/policy-reconciliation.md`. Validation of
the complete authorization history always precedes projection, so an invalid
candidate raises and contributes nothing rather than resolving to a partial
snapshot.
"""
import datetime
import hashlib
import json
import os
import re
import sys

MANIFESTS_ENV = "MARROWFLEET_POLICY_MANIFESTS"
MANIFESTS_DEFAULT = "/workspace/policy/manifests.json"

EVENT_KEYS = frozenset(
    ("authorization", "revision", "subject", "operation", "start", "end",
     "parameters")
)
COMMIT_KEYS = frozenset(("batch", "parts", "sha256"))
PAGE_KEYS = frozenset(("batch", "part", "events"))
TRANSPORT_KEYS = frozenset(("generation", "commits", "pages"))
OPERATIONS = frozenset(("grant", "withdraw"))

DATE_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
REVISION_RE = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")


class PolicyError(ValueError):
    """An authorization history or request that the contract rejects."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _digest(events):
    return hashlib.sha256(_canonical(events).encode("utf-8")).hexdigest()


def _text(value, what):
    if not isinstance(value, str) or not value:
        raise PolicyError("%s must be a nonempty string" % what)
    return value


def _sha256(value, what):
    if not isinstance(value, str) or not SHA256_RE.match(value.lower()):
        raise PolicyError("%s must be a sha256 digest" % what)
    return value.lower()


def _date(value, what):
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise PolicyError("%s must be an ISO YYYY-MM-DD date" % what)
    year, month, day = (int(part) for part in value.split("-"))
    try:
        datetime.date(year, month, day)
    except ValueError:
        raise PolicyError("%s is not a real date: %s" % (what, value))
    return value


def _validate_event(event):
    if not isinstance(event, dict):
        raise PolicyError("an event must be an object")
    if frozenset(event) != EVENT_KEYS:
        raise PolicyError("event keys must be exactly %s, got %s"
                          % (sorted(EVENT_KEYS), sorted(event)))
    _text(event["authorization"], "authorization")
    _text(event["subject"], "subject")
    revision = event["revision"]
    if not isinstance(revision, str) or not REVISION_RE.match(revision):
        raise PolicyError(
            "revision must be a canonical nonnegative decimal string, got %r"
            % (revision,))
    if event["operation"] not in OPERATIONS:
        raise PolicyError("operation must be grant or withdraw, got %r"
                          % (event["operation"],))
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(
            isinstance(item, str) for item in parameters):
        raise PolicyError("parameters must be a list of strings")
    start = _date(event["start"], "start")
    end = _date(event["end"], "end")
    if start >= end:
        raise PolicyError("start must be strictly before end: %s..%s"
                          % (start, end))


def _manifest_batches(generation):
    path = os.environ.get(MANIFESTS_ENV) or MANIFESTS_DEFAULT
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifests = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PolicyError("the release manifests are unreadable: %s" % exc)
    if not isinstance(manifests, dict) or not isinstance(
            manifests.get("generations"), dict):
        raise PolicyError("the release manifests carry no generations record")
    generations = manifests["generations"]
    if generation not in generations:
        raise PolicyError("the manifests name no generation %r" % generation)
    record = generations[generation]
    if not isinstance(record, dict) or not isinstance(
            record.get("batches"), dict):
        raise PolicyError("generation %r lists no batches" % generation)
    return {
        _text(batch, "a manifest batch name"):
            _sha256(sha, "the digest manifested for batch %r" % batch)
        for batch, sha in record["batches"].items()
    }


def _commits(commits):
    if not isinstance(commits, list):
        raise PolicyError("a transport must carry a commits list")
    committed = {}
    for commit in commits:
        if not isinstance(commit, dict) or frozenset(commit) != COMMIT_KEYS:
            raise PolicyError(
                "a commit must carry exactly batch, parts and sha256")
        batch = _text(commit["batch"], "a commit batch")
        parts = commit["parts"]
        if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
            raise PolicyError("commit %r must carry a positive parts count"
                              % batch)
        record = (parts, _sha256(commit["sha256"],
                                 "the digest committed for batch %r" % batch))
        if batch in committed and committed[batch] != record:
            raise PolicyError("conflicting commits for batch %r" % batch)
        committed[batch] = record
    return committed


def _pages(pages, committed):
    if not isinstance(pages, list):
        raise PolicyError("a transport must carry a pages list")
    carried = {}
    for page in pages:
        if not isinstance(page, dict) or frozenset(page) != PAGE_KEYS:
            raise PolicyError(
                "a page must carry exactly batch, part and events")
        batch = _text(page["batch"], "a page batch")
        part = page["part"]
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            raise PolicyError("page part must be a zero-based integer, got %r"
                              % (part,))
        events = page["events"]
        if not isinstance(events, list):
            raise PolicyError("page %r/%d must carry an events list"
                              % (batch, part))
        if batch not in committed:
            raise PolicyError("page %r/%d belongs to no commit" % (batch, part))
        if part >= committed[batch][0]:
            raise PolicyError(
                "page %r/%d lies beyond the %d parts its commit declares"
                % (batch, part, committed[batch][0]))
        if (batch, part) in carried and carried[(batch, part)] != events:
            raise PolicyError("conflicting replays of page %r/%d"
                              % (batch, part))
        carried[(batch, part)] = events
    return carried


def _manifested(generation, committed):
    listed = _manifest_batches(generation)
    missing = sorted(frozenset(listed) - frozenset(committed))
    if missing:
        raise PolicyError(
            "generation %r lists batches the transport does not commit: %s"
            % (generation, ", ".join(missing)))
    extra = sorted(frozenset(committed) - frozenset(listed))
    if extra:
        raise PolicyError(
            "the transport commits batches generation %r does not list: %s"
            % (generation, ", ".join(extra)))
    for batch in sorted(listed):
        if committed[batch][1] != listed[batch]:
            raise PolicyError(
                "batch %r is committed at a digest generation %r does not list"
                % (batch, generation))


def _history(transport):
    """Validate a segmented delivery and return its complete event history."""
    if not isinstance(transport, dict):
        raise PolicyError("a transport must be an object")
    unknown = sorted(frozenset(transport) - TRANSPORT_KEYS)
    if unknown:
        raise PolicyError("a transport carries no %s" % ", ".join(unknown))

    committed = _commits(transport.get("commits"))
    carried = _pages(transport.get("pages"), committed)

    if "generation" in transport:
        _manifested(_text(transport["generation"], "generation"), committed)

    history = []
    for batch in sorted(committed):
        parts, sha = committed[batch]
        assembled = []
        for part in range(parts):
            if (batch, part) not in carried:
                raise PolicyError("batch %r is missing part %d" % (batch, part))
            assembled.extend(carried[(batch, part)])
        if _digest(assembled) != sha:
            raise PolicyError("batch %r does not match its committed digest"
                              % batch)
        history.extend(assembled)
    return history


def _corrected(history):
    """Return the winning event of each authorization after corrections."""
    revisions = {}
    for event in history:
        held = (event["authorization"], int(event["revision"]))
        if held in revisions and revisions[held] != event:
            raise PolicyError(
                "authorization %s carries conflicting revision %s"
                % (held[0], event["revision"]))
        revisions[held] = event
    winners = {}
    for (authorization, revision), event in revisions.items():
        held = winners.get(authorization)
        if held is None or revision > held[0]:
            winners[authorization] = (revision, event)
    return {name: event for name, (_, event) in winners.items()}


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError("a request must be an object")
    if ("events" in request) == ("transport" in request):
        raise PolicyError("a request carries either events or a transport")

    now = _date(request.get("now"), "now")
    subjects = request.get("subjects")
    if not isinstance(subjects, list):
        raise PolicyError("a request must carry a subjects list")
    for subject in subjects:
        _text(subject, "a requested subject")

    if "transport" in request:
        history = _history(request["transport"])
    else:
        history = request["events"]
        if not isinstance(history, list):
            raise PolicyError("events must be a list")
    for event in history:
        _validate_event(event)

    winners = _corrected(history)
    active = {}
    for authorization, event in winners.items():
        if event["operation"] != "grant":
            continue
        if not event["start"] <= now < event["end"]:
            continue
        active.setdefault(event["subject"], []).append(authorization)

    snapshot = {}
    for subject in subjects:
        holders = sorted(active.get(subject, ()))
        if len(holders) > 1:
            raise PolicyError(
                "subject %s carries %d active authorizations: %s"
                % (subject, len(holders), ", ".join(holders)))
        snapshot[subject] = (winners[holders[0]]["parameters"] if holders
                             else None)
    return snapshot


def main():
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write("resolve: unreadable request: %s\n" % exc)
        return 2
    try:
        snapshot = resolve(request)
    except PolicyError as exc:
        sys.stderr.write("resolve: %s\n" % exc)
        return 1
    sys.stdout.write(json.dumps(snapshot, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
