"""Resolve the current signed authorization snapshot.

Validation of the complete candidate precedes any projection: an invalid
history raises and yields no partial result, so the controller rejects that
candidate and carries on with the next one.
"""
import datetime
import hashlib
import json
import os
import re
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation",
              "start", "end", "parameters"}
OPERATIONS = {"grant", "withdraw"}
DATE_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
REVISION_RE = re.compile(r"\A(0|[1-9][0-9]*)\Z")
DEFAULT_MANIFESTS = "/workspace/policy/manifests.json"


class PolicyError(ValueError):
    """The candidate is not a valid authorization history."""


def _nonempty(value, what):
    if not isinstance(value, str) or not value:
        raise PolicyError("%s must be a nonempty string" % what)
    return value


def _date(value, what):
    _nonempty(value, what)
    if not DATE_RE.match(value):
        raise PolicyError("%s is not an ISO date: %r" % (what, value))
    try:
        datetime.date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        raise PolicyError("%s is not a real date: %r" % (what, value))
    return value


def _canonical_json(events):
    return json.dumps(events, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _validate_event(event):
    if not isinstance(event, dict):
        raise PolicyError("an event must be an object")
    if set(event) != EVENT_KEYS:
        raise PolicyError("event keys are %s, not %s"
                          % (sorted(EVENT_KEYS), sorted(event)))
    _nonempty(event["authorization"], "authorization")
    _nonempty(event["subject"], "subject")
    revision = event["revision"]
    if not isinstance(revision, str) or not REVISION_RE.match(revision):
        raise PolicyError("revision is not a canonical decimal string: %r"
                          % (revision,))
    if event["operation"] not in OPERATIONS:
        raise PolicyError("operation is not grant or withdraw: %r"
                          % (event["operation"],))
    start = _date(event["start"], "start")
    end = _date(event["end"], "end")
    if not start < end:
        raise PolicyError("start %s is not before end %s" % (start, end))
    parameters = event["parameters"]
    if not isinstance(parameters, list) or not all(
            isinstance(item, str) for item in parameters):
        raise PolicyError("parameters must be a list of strings")


def _generation_batches(generation):
    path = os.environ.get("MARROWFLEET_POLICY_MANIFESTS", DEFAULT_MANIFESTS)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifests = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PolicyError("the release manifests are unreadable: %s" % exc)
    generations = manifests.get("generations") if isinstance(manifests, dict) else None
    if not isinstance(generations, dict):
        raise PolicyError("the release manifests name no generations")
    if generation not in generations:
        raise PolicyError("generation %s is not named by the manifests"
                          % generation)
    record = generations[generation]
    batches = record.get("batches") if isinstance(record, dict) else None
    if not isinstance(batches, dict):
        raise PolicyError("generation %s lists no batches" % generation)
    for batch, digest in batches.items():
        _nonempty(batch, "a manifest batch")
        _nonempty(digest, "a manifest digest")
    return batches


def _committed(transport):
    """The commits of a delivery, keyed by batch, identical replays merged."""
    commits = transport.get("commits")
    if not isinstance(commits, list):
        raise PolicyError("a transport must carry a list of commits")
    seen = {}
    for commit in commits:
        if not isinstance(commit, dict) or set(commit) != {"batch", "parts", "sha256"}:
            raise PolicyError("a commit carries batch, parts and sha256")
        batch = _nonempty(commit["batch"], "a commit batch")
        parts = commit["parts"]
        if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
            raise PolicyError("commit %s: parts must be a positive integer"
                              % batch)
        _nonempty(commit["sha256"], "a commit digest")
        if batch in seen and seen[batch] != commit:
            raise PolicyError("batch %s was committed twice, differently" % batch)
        seen[batch] = commit
    return seen


def _paged(transport, committed):
    """The pages of a delivery, keyed by batch and part, replays merged."""
    pages = transport.get("pages")
    if not isinstance(pages, list):
        raise PolicyError("a transport must carry a list of pages")
    held = {}
    for page in pages:
        if not isinstance(page, dict) or set(page) != {"batch", "part", "events"}:
            raise PolicyError("a page carries batch, part and events")
        batch = _nonempty(page["batch"], "a page batch")
        part = page["part"]
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            raise PolicyError("batch %s: part must be a zero-based integer"
                              % batch)
        if not isinstance(page["events"], list):
            raise PolicyError("batch %s part %s: events must be a list"
                              % (batch, part))
        if batch not in committed:
            raise PolicyError("batch %s was paged but never committed" % batch)
        if (batch, part) in held and held[(batch, part)] != page["events"]:
            raise PolicyError("batch %s part %s arrived twice, differently"
                              % (batch, part))
        held[(batch, part)] = page["events"]
    return held


def _delivered(transport):
    """The events a segmented delivery carries, checked against its commits."""
    if not isinstance(transport, dict):
        raise PolicyError("a transport must be an object")
    if set(transport) - {"generation", "commits", "pages"}:
        raise PolicyError("a transport carries generation, commits and pages")

    committed = _committed(transport)
    held = _paged(transport, committed)

    history = []
    for batch in sorted(committed):
        commit = committed[batch]
        parts = set(part for paged_batch, part in held if paged_batch == batch)
        if parts != set(range(commit["parts"])):
            raise PolicyError("batch %s holds parts %s, not the %d committed"
                              % (batch, sorted(parts), commit["parts"]))
        events = [event for part in range(commit["parts"])
                  for event in held[(batch, part)]]
        digest = hashlib.sha256(_canonical_json(events)).hexdigest()
        if digest != commit["sha256"]:
            raise PolicyError("batch %s reassembles to %s, not the committed %s"
                              % (batch, digest, commit["sha256"]))
        history.extend(events)

    if "generation" in transport:
        generation = _nonempty(transport["generation"], "a generation")
        listed = _generation_batches(generation)
        for batch in sorted(set(listed) | set(committed)):
            if batch not in committed:
                raise PolicyError("generation %s lists batch %s, which the "
                                  "delivery does not commit"
                                  % (generation, batch))
            if batch not in listed:
                raise PolicyError("the delivery commits batch %s, which "
                                  "generation %s does not list"
                                  % (batch, generation))
            if listed[batch] != committed[batch]["sha256"]:
                raise PolicyError("batch %s is committed at %s, not the %s "
                                  "generation %s lists"
                                  % (batch, committed[batch]["sha256"],
                                     listed[batch], generation))
    return history


def _history(request):
    """The complete authorization history a request delivers, validated."""
    if ("events" in request) == ("transport" in request):
        raise PolicyError("a request carries either events or a transport")

    if "events" in request:
        history = request["events"]
        if not isinstance(history, list):
            raise PolicyError("events must be a list")
        history = list(history)
    else:
        history = _delivered(request["transport"])

    for event in history:
        _validate_event(event)
    return history


def _revisions(history):
    """The winning revision of every authorization the history carries."""
    by_authorization = {}
    for event in history:
        by_authorization.setdefault(event["authorization"], {}).setdefault(
            event["revision"], []).append(event)
    winners = {}
    for authorization, revisions in by_authorization.items():
        for revision, events in revisions.items():
            if any(event != events[0] for event in events):
                raise PolicyError("%s revision %s arrived with conflicting "
                                  "records" % (authorization, revision))
        winning = max(revisions, key=int)
        winners[authorization] = revisions[winning][0]
    return winners


def _decisions(winners, now):
    """The parameters each subject holds a current authorization for."""
    decisions = {}
    for authorization in sorted(winners):
        event = winners[authorization]
        if event["operation"] != "grant":
            continue
        if not event["start"] <= now < event["end"]:
            continue
        subject = event["subject"]
        if subject in decisions:
            raise PolicyError("%s holds more than one current authorization"
                              % subject)
        decisions[subject] = event["parameters"]
    return decisions


def resolve(request):
    if not isinstance(request, dict):
        raise PolicyError("a request must be an object")
    now = _date(request.get("now"), "now")
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        raise PolicyError("a request must name the subjects to project")
    for subject in subjects:
        _nonempty(subject, "a requested subject")

    decisions = _decisions(_revisions(_history(request)), now)
    return {subject: decisions.get(subject) for subject in subjects}


if __name__ == '__main__':
    try:
        projection = resolve(json.load(sys.stdin))
    except (PolicyError, ValueError) as exc:
        sys.stderr.write("resolve: %s\n" % exc)
        sys.exit(1)
    print(json.dumps(projection, sort_keys=True))
