"""Resolve the current signed authorization snapshot."""
import json
import hashlib
import sys

EVENT_KEYS = {"authorization", "revision", "subject", "operation",
              "start", "end", "parameters"}


class Invalid(Exception):
    pass


def _canon(events):
    return json.dumps(events, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode("utf-8")


def _date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise Invalid("bad date")
    y, m, d = value[0:4], value[5:7], value[8:10]
    if value[4] != '-' or value[7] != '-':
        raise Invalid("bad date")
    if not (y.isdigit() and m.isdigit() and d.isdigit()):
        raise Invalid("bad date")
    if not (1 <= int(m) <= 12 and 1 <= int(d) <= 31):
        raise Invalid("bad date")
    return value


def _revision(value):
    if not isinstance(value, str) or not value.isdigit():
        raise Invalid("bad revision")
    if len(value) > 1 and value[0] == '0':
        raise Invalid("bad revision")
    return int(value)


def _check_event(event):
    if not isinstance(event, dict) or set(event) != EVENT_KEYS:
        raise Invalid("bad event keys")
    for key in ("authorization", "subject"):
        if not isinstance(event[key], str) or not event[key]:
            raise Invalid("bad identifier")
    if event["operation"] not in ("grant", "withdraw"):
        raise Invalid("bad operation")
    params = event["parameters"]
    if not isinstance(params, list) or not all(isinstance(x, str) for x in params):
        raise Invalid("bad parameters")
    _revision(event["revision"])
    start = _date(event["start"])
    end = _date(event["end"])
    if not start < end:
        raise Invalid("start not before end")


def _assemble(transport):
    if not isinstance(transport, dict):
        raise Invalid("bad transport")
    commits = {}
    for commit in transport.get("commits", []):
        batch = commit["batch"]
        parts = commit["parts"]
        digest = commit["sha256"]
        if not isinstance(batch, str) or not batch:
            raise Invalid("bad batch")
        if not isinstance(parts, int) or isinstance(parts, bool) or parts < 1:
            raise Invalid("bad parts")
        if not isinstance(digest, str):
            raise Invalid("bad digest")
        if batch in commits:
            if commits[batch] != (parts, digest):
                raise Invalid("conflicting commit replay")
        commits[batch] = (parts, digest)
    pages = {}
    for page in transport.get("pages", []):
        batch = page["batch"]
        part = page["part"]
        events = page["events"]
        if not isinstance(batch, str) or not batch:
            raise Invalid("bad page batch")
        if not isinstance(part, int) or isinstance(part, bool) or part < 0:
            raise Invalid("bad page part")
        if not isinstance(events, list):
            raise Invalid("bad page events")
        key = (batch, part)
        if key in pages:
            if pages[key] != events:
                raise Invalid("conflicting page replay")
        pages[key] = events
    for batch, part in pages:
        if batch not in commits:
            raise Invalid("uncommitted page")
    history = []
    for batch in sorted(commits):
        parts, digest = commits[batch]
        collected = []
        for part in range(parts):
            if (batch, part) not in pages:
                raise Invalid("missing part")
            collected.extend(pages[(batch, part)])
        for (pbatch, ppart) in pages:
            if pbatch == batch and ppart >= parts:
                raise Invalid("extra part")
        if hashlib.sha256(_canon(collected)).hexdigest() != digest:
            raise Invalid("digest mismatch")
        history.extend(collected)
    return history


def resolve(request):
    if "transport" in request:
        events = _assemble(request["transport"])
    else:
        events = request["events"]
        if not isinstance(events, list):
            raise Invalid("bad events")
    for event in events:
        _check_event(event)

    seen = {}
    for event in events:
        key = (event["authorization"], _revision(event["revision"]))
        if key in seen:
            if seen[key] != event:
                raise Invalid("conflicting equal revisions")
        else:
            seen[key] = event

    winners = {}
    for (auth, rev), event in seen.items():
        current = winners.get(auth)
        if current is None or rev > current[0]:
            winners[auth] = (rev, event)

    now = request["now"]
    active = {}
    for auth, (_rev, event) in winners.items():
        if event["operation"] != "grant":
            continue
        if not (event["start"] <= now < event["end"]):
            continue
        subject = event["subject"]
        if subject in active:
            raise Invalid("multiple active authorizations")
        active[subject] = event["parameters"]

    return {subject: active.get(subject) for subject in request["subjects"]}


if __name__ == '__main__':
    try:
        print(json.dumps(resolve(json.load(sys.stdin)), sort_keys=True))
    except Exception as exc:
        sys.stderr.write("resolve: %s\n" % exc)
        sys.exit(1)
