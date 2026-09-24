"""Resolve the current signed authorization snapshot.

Contract: /workspace/docs/policy-reconciliation.md.

The resolver is given a request carrying either `events` (an events-only
request) or a `transport` of {generation,commits,pages}.  The transport is a
segmented delivery of authorization history: batches are deltas, parts
concatenate in numeric order, and every committed batch belongs to the history
regardless of transport order.  A named generation must commit exactly the
batches the release manifest lists, each at the manifest's digest.

Validation of the complete history precedes projection: an invalid request
raises, printing the reason on stderr, and leaves no partial result.
"""

import hashlib
import json
import os
import sys

DEFAULT_MANIFESTS = "/workspace/policy/manifests.json"

EVENT_KEYS = frozenset(
    ("authorization", "revision", "subject", "operation", "start", "end", "parameters")
)
OPERATIONS = frozenset(("grant", "withdraw"))


class Invalid(Exception):
    """An invalid request: the entire candidate is rejected."""


def _fail(message):
    raise Invalid(message)


# ---------------------------------------------------------------- validation

def _check_identifier(value, what):
    if not isinstance(value, str) or not value:
        _fail("%s must be a nonempty string" % what)


def _check_revision(value):
    # canonical nonnegative decimal: no sign, no padding, no leading zeros
    if not isinstance(value, str) or not value.isdigit():
        _fail("revision must be a canonical nonnegative decimal string")
    if len(value) > 1 and value[0] == "0":
        _fail("revision must not carry leading zeros")
    return int(value)


def _check_date(value, what):
    if not isinstance(value, str) or len(value) != 10:
        _fail("%s must be an ISO YYYY-MM-DD date" % what)
    year, month, day = value[:4], value[5:7], value[8:10]
    if value[4] != "-" or value[7] != "-":
        _fail("%s must be an ISO YYYY-MM-DD date" % what)
    for part in (year, month, day):
        if not part.isdigit():
            _fail("%s must be an ISO YYYY-MM-DD date" % what)
    days = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if not 1 <= int(month) <= 12:
        _fail("%s month out of range" % what)
    if not 1 <= int(day) <= days[int(month) - 1]:
        _fail("%s day out of range" % what)
    return value


def _check_event(event, index):
    if not isinstance(event, dict):
        _fail("event %d is not an object" % index)
    if set(event) != EVENT_KEYS:
        _fail("event %d keys are exactly the event keys, no more, no less" % index)
    _check_identifier(event["authorization"], "authorization")
    _check_revision(event["revision"])
    _check_identifier(event["subject"], "subject")
    if event["operation"] not in OPERATIONS:
        _fail("operation is grant or withdraw")
    _check_date(event["start"], "start")
    _check_date(event["end"], "end")
    if not (event["start"] < event["end"]):
        _fail("start must be strictly before end")
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(
        isinstance(item, str) for item in parameters
    ):
        _fail("parameters is a list of strings")


# ------------------------------------------------------------- transport in

def _digest(events):
    payload = json.dumps(
        events, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assemble(transport):
    """Reassemble the complete history from a segmented delivery."""
    if not isinstance(transport, dict):
        _fail("transport is an object")
    for key in ("commits", "pages"):
        if not isinstance(transport.get(key), list):
            _fail("transport carries commits and pages")

    commits = {}
    for commit in transport["commits"]:
        if not isinstance(commit, dict):
            _fail("each commit is an object")
        _check_identifier(commit.get("batch"), "commit batch")
        parts = commit.get("parts")
        if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
            _fail("commit parts is a positive integer")
        sha = commit.get("sha256")
        if not isinstance(sha, str) or not sha:
            _fail("commit sha256 is a string")
        if commit["batch"] in commits:
            identical = (
                commits[commit["batch"]]["parts"] == parts
                and commits[commit["batch"]]["sha256"] == sha
            )
            if not identical:
                _fail("conflicting commits for batch %s" % commit["batch"])
            continue
        commits[commit["batch"]] = {"parts": parts, "sha256": sha}

    seen = {}
    for page in transport["pages"]:
        if not isinstance(page, dict):
            _fail("each page is an object")
        _check_identifier(page.get("batch"), "page batch")
        part = page.get("part")
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            _fail("page part is a zero-based integer")
        events = page.get("events")
        if not isinstance(events, list):
            _fail("page events is a list")
        key = (page["batch"], part)
        if key in seen:
            if seen[key] != events:
                _fail("conflicting replay of page %s part %d" % key)
            continue
        seen[key] = events

    # no page outside the commits, every committed batch delivered whole
    for (batch, part) in seen:
        if batch not in commits:
            _fail("page for uncommitted batch %s" % batch)
        elif part >= commits[batch]["parts"]:
            _fail("page part %d outside batch %s" % (part, batch))
    for batch, commit in commits.items():
        delivered = sorted(part for (b, part) in seen if b == batch)
        if delivered != list(range(commit["parts"])):
            _fail("batch %s committed at %d parts is not delivered whole"
                  % (batch, commit["parts"]))

    history = []
    for batch in sorted(commits, key=lambda name: (len(name), name)):
        events = []
        for part in range(commits[batch]["parts"]):
            events.extend(seen[(batch, part)])
        if _digest(events) != commits[batch]["sha256"]:
            _fail("batch %s does not match its commit digest" % batch)
        history.extend(events)
    return history, commits


def _check_manifest(generation, commits):
    """A released delivery commits exactly the batches its generation lists."""
    path = os.environ.get("MARROWFLEET_POLICY_MANIFESTS", DEFAULT_MANIFESTS)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifests = json.load(handle)
    except OSError as error:
        _fail("cannot read the release manifests: %s" % error)
    except ValueError as error:
        _fail("the release manifests are not valid JSON: %s" % error)
    generations = manifests.get("generations")
    if not isinstance(generations, dict):
        _fail("the release manifests carry a generations object")
    if generation not in generations:
        _fail("generation %s is not named by the manifests" % generation)
    listed = generations[generation].get("batches")
    if not isinstance(listed, dict):
        _fail("generation %s carries a batches object" % generation)
    if set(listed) != set(commits):
        missing = sorted(set(listed) - set(commits))
        extra = sorted(set(commits) - set(listed))
        _fail("generation %s commits exactly its manifest batches "
              "(missing %s, extra %s)" % (generation, missing, extra))
    for batch, sha in listed.items():
        if not isinstance(sha, str):
            _fail("manifest digest for batch %s is a string" % batch)
        if commits[batch]["sha256"] != sha:
            _fail("batch %s does not commit at the digest the manifests list"
                  % batch)


# ----------------------------------------------------------------- resolver

def _events_from_request(request):
    """Return the complete history the request carries."""
    if "transport" in request:
        history, commits = _assemble(request["transport"])
        generation = request["transport"].get("generation")
        if generation is not None:
            _check_identifier(generation, "generation")
            _check_manifest(generation, commits)
        return history
    if "events" in request:
        events = request["events"]
        if not isinstance(events, list):
            _fail("events is a list")
        return events
    _fail("a request carries events or a transport")


def resolve(request):
    if not isinstance(request, dict):
        _fail("a request is an object")
    now = request.get("now")
    if now is not None:
        _check_date(now, "now")
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not all(
        isinstance(subject, str) and subject for subject in subjects
    ):
        _fail("subjects is a list of nonempty strings")

    history = _events_from_request(request)

    # Complete validation of every event, including superseded, withdrawn,
    # out-of-window and unrequested-subject records.
    for index, event in enumerate(history):
        _check_event(event, index)

    # Fold the history by authorization: a higher numeric revision fully
    # replaces an earlier revision of the same authorization, including its
    # subject and effective interval.  Repeated identical revisions are
    # idempotent; conflicting equal revisions are invalid -- checked across
    # the whole history, not only against the winning revision, since a
    # transport checksum says nothing about internal consistency.
    revisions = {}
    winners = {}
    for event in history:
        key = (event["authorization"], int(event["revision"]))
        canonical = (event["subject"], event["operation"],
                     event["start"], event["end"], event["parameters"])
        first = revisions.setdefault(key, canonical)
        if first != canonical:
            _fail("conflicting equal revisions of %s" % event["authorization"])
        current = winners.get(event["authorization"])
        if current is None or key[1] > current[0]:
            winners[event["authorization"]] = (key[1], event)

    # Project the corrected snapshot onto the evaluation instant.
    active = {}
    for _, event in winners.values():
        if event["operation"] == "withdraw":
            continue
        if now is not None and not (event["start"] <= now < event["end"]):
            continue
        if event["subject"] in active:
            _fail("multiple active authorizations for %s" % event["subject"])
        active[event["subject"]] = event

    # Unknown subjects are excluded from the projection after resolving
    # revisions; every requested subject is returned, null where no current
    # authorization applies.
    return {
        subject: active[subject]["parameters"] if subject in active else None
        for subject in subjects
    }


if __name__ == "__main__":
    try:
        result = resolve(json.load(sys.stdin))
    except Invalid as error:
        sys.stderr.write("resolver: %s\n" % error)
        sys.exit(1)
    print(json.dumps(result, sort_keys=True))
