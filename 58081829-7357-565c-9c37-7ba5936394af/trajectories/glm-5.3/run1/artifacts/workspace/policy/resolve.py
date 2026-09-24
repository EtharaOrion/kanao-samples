"""Resolve the current signed authorization snapshot.

Implements the reconciliation contract in docs/policy-reconciliation.md:

  * transport delivery envelope: commits/pages, per-batch SHA-256 over the
    concatenated event array (sorted keys, (',',':') separators,
    ensure_ascii=False, UTF-8), idempotent replay, complete parts,
    no uncommitted pages,
  * release manifests: a transport that names a generation must commit
    exactly the batches that generation's manifest lists, at the manifest's
    digests,
  * complete validation of every event in the history (including superseded,
    withdrawn, out-of-window and unrequested-subject records) before any
    projection,
  * revision resolution per authorization (numeric revision, higher fully
    replaces earlier, including subject and interval), winning withdrawals
    contributing no decision, grants active only within start <= now < end,
    multiple active authorizations for a subject being an error,
  * projection of the requested subjects only.

Any failure raises PolicyError; the CLI then exits nonzero with no partial
result printed.
"""
import hashlib
import json
import os
import re
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation", "start", "end", "parameters"}
OPERATIONS = {"grant", "withdraw"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REVISION_RE = re.compile(r"^(0|[1-9][0-9]*)$")
DEFAULT_MANIFESTS = "/workspace/policy/manifests.json"


class PolicyError(Exception):
    pass


def _nonempty_string(value, what):
    if not isinstance(value, str) or not value:
        raise PolicyError("%s must be a nonempty string" % what)
    return value


def _validate_date(value, what):
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise PolicyError("%s must be an ISO YYYY-MM-DD date" % what)
    return value


def validate_event(event):
    if not isinstance(event, dict) or set(event) != EVENT_KEYS:
        raise PolicyError("event keys must be exactly %s" % sorted(EVENT_KEYS))
    _nonempty_string(event["authorization"], "authorization")
    _nonempty_string(event["subject"], "subject")
    revision = event["revision"]
    if not isinstance(revision, str) or not REVISION_RE.match(revision):
        raise PolicyError("revision %r must be a canonical nonnegative decimal string" % revision)
    if event["operation"] not in OPERATIONS:
        raise PolicyError("operation %r must be grant or withdraw" % event["operation"])
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(isinstance(p, str) for p in parameters):
        raise PolicyError("parameters must be a list of strings")
    start = _validate_date(event["start"], "start")
    end = _validate_date(event["end"], "end")
    if not start < end:
        raise PolicyError("start %s must be strictly before end %s" % (start, end))
    return event


def _canonical(events):
    return json.dumps(events, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(events):
    return hashlib.sha256(_canonical(events)).hexdigest()


def collect_transport_events(transport, manifests):
    """Return the complete event history carried by a transport."""
    if not isinstance(transport, dict):
        raise PolicyError("transport must be an object")
    for key in ("commits", "pages"):
        if not isinstance(transport.get(key), list):
            raise PolicyError("transport must carry %s as a list" % key)

    pages = {}
    for page in transport["pages"]:
        if not isinstance(page, dict) or set(page) != {"batch", "part", "events"}:
            raise PolicyError("each page has batch, part and events")
        batch = _nonempty_string(page["batch"], "page batch")
        part = page["part"]
        if isinstance(part, bool) or not isinstance(part, int) or part < 0:
            raise PolicyError("page part must be a zero-based integer")
        if not isinstance(page["events"], list):
            raise PolicyError("page events must be a list")
        for event in page["events"]:
            validate_event(event)
        replay = _canonical(page["events"])
        previous = pages.setdefault((batch, part), replay)
        if previous != replay:
            raise PolicyError("conflicting replay of page %s part %d" % (batch, part))

    commits = {}
    for commit in transport["commits"]:
        if not isinstance(commit, dict) or set(commit) != {"batch", "parts", "sha256"}:
            raise PolicyError("each commit has batch, parts and sha256")
        batch = _nonempty_string(commit["batch"], "commit batch")
        parts = commit["parts"]
        if isinstance(parts, bool) or not isinstance(parts, int) or parts < 1:
            raise PolicyError("commit parts must be a positive integer")
        sha256 = commit["sha256"]
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise PolicyError("commit sha256 must be a lowercase hex digest")
        replay = (parts, sha256)
        previous = commits.setdefault(batch, replay)
        if previous != replay:
            raise PolicyError("conflicting replay of commit %s" % batch)

    for batch in sorted(commits):
        parts, sha256 = commits[batch]
        covered = {part for (b, part) in pages if b == batch}
        for part in range(parts):
            if (batch, part) not in pages:
                raise PolicyError("batch %s is missing part %d" % (batch, part))
        if covered - set(range(parts)):
            raise PolicyError("batch %s carries parts outside its commit" % batch)
        events = []
        for part in range(parts):
            events.extend(json.loads(pages[(batch, part)]))
        if _digest(events) != sha256:
            raise PolicyError("batch %s does not match its commit digest" % batch)

    uncommitted = sorted({batch for (batch, _) in pages} - set(commits))
    if uncommitted:
        raise PolicyError("uncommitted pages for batch %s" % uncommitted[0])

    generation = transport.get("generation")
    if generation is not None:
        _nonempty_string(generation, "generation")
        if manifests is None:
            raise PolicyError("no release manifests available")
        recorded = manifests.get("generations", {}).get(generation)
        if not isinstance(recorded, dict):
            raise PolicyError("generation %s is not in the release manifests" % generation)
        expected = recorded.get("batches")
        if not isinstance(expected, dict):
            raise PolicyError("generation %s has no batch table" % generation)
        missing = sorted(set(expected) - set(commits))
        if missing:
            raise PolicyError("generation %s batch %s is not committed" % (generation, missing[0]))
        extra = sorted(set(commits) - set(expected))
        if extra:
            raise PolicyError("batch %s is committed outside generation %s" % (extra[0], generation))
        for batch in sorted(expected):
            digest = expected[batch]
            if not isinstance(digest, str) or commits[batch][1] != digest:
                raise PolicyError("batch %s does not match generation %s" % (batch, generation))

    history = []
    for batch in sorted(commits, key=lambda b: (len(b), b)):
        parts = commits[batch][0]
        for part in range(parts):
            history.extend(json.loads(pages[(batch, part)]))
    return history


def load_manifests():
    path = os.environ.get("MARROWFLEET_POLICY_MANIFESTS", DEFAULT_MANIFESTS)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifests = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PolicyError("cannot read release manifests at %s: %s" % (path, exc))
    if not isinstance(manifests, dict) or not isinstance(manifests.get("generations"), dict):
        raise PolicyError("release manifests must carry a generations table")
    return manifests


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError("request must be an object")
    now = _validate_date(request.get("now"), "now")
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        raise PolicyError("request must name at least one subject")
    for subject in subjects:
        _nonempty_string(subject, "subject")

    if "transport" in request:
        events = collect_transport_events(request["transport"], load_manifests())
    elif isinstance(request.get("events"), list):
        events = request["events"]
        for event in events:
            validate_event(event)
    else:
        raise PolicyError("request must carry events or a transport")

    # Revision resolution per authorization: a higher numeric revision fully
    # replaces an earlier one, including its subject and effective interval.
    winning = {}
    replayed = {}
    for event in events:
        authorization = event["authorization"]
        revision = int(event["revision"])
        signature = _canonical([event])
        seen = replayed.setdefault(authorization, {})
        if revision in seen and seen[revision] != signature:
            raise PolicyError("conflicting equal revision %s for %s" % (event["revision"], authorization))
        seen[revision] = signature
        held = winning.get(authorization)
        if held is None or revision > held[0]:
            winning[authorization] = (revision, event)

    # Project the winning grants into per-subject decisions.
    decisions = {}
    for authorization in sorted(winning):
        event = winning[authorization][1]
        if event["operation"] != "grant":
            continue
        if not (event["start"] <= now < event["end"]):
            continue
        subject = event["subject"]
        if subject in decisions:
            raise PolicyError("multiple active authorizations for %s" % subject)
        decisions[subject] = event["parameters"]

    return {subject: decisions.get(subject) for subject in subjects}


def main(argv):
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write("resolve: unreadable request: %s\n" % exc)
        return 2
    try:
        result = resolve(request)
    except PolicyError as exc:
        sys.stderr.write("resolve: %s\n" % exc)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
