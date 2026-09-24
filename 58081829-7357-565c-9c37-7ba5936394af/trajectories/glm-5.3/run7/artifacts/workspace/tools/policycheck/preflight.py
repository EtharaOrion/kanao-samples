"""Preflight for the signed authorization resolver.

Runs the resolver over trial requests and asserts which are admissible.
Not part of the release; the desk uses it to show a candidate's decisions
or why it is rejected before the wrapper ever applies anything.
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile

RESOLVER = os.path.join(os.path.dirname(__file__), "..", "..", "policy",
                        "resolve.py")


def run(request, manifests=None):
    env = dict(os.environ)
    if manifests is None:
        env.pop("MARROWFLEET_POLICY_MANIFESTS", None)
    else:
        handle, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w") as fh:
            json.dump(manifests, fh)
        env["MARROWFLEET_POLICY_MANIFESTS"] = path
    proc = subprocess.run([sys.executable, RESOLVER],
                          input=json.dumps(request), text=True,
                          capture_output=True, env=env)
    if manifests is not None:
        os.unlink(path)
    return proc


def check(name, request, expect_rc, expect=None, manifests="__default__"):
    proc = run(request, None if manifests == "__default__" else manifests)
    ok = proc.returncode == expect_rc
    if expect is not None and proc.returncode == 0:
        ok = ok and json.loads(proc.stdout) == expect
    print("%-38s %s rc=%d %s"
          % (name, "PASS" if ok else "FAIL", proc.returncode,
             proc.stderr.strip()[:90]))
    return ok


def base_event(**over):
    event = {"authorization": "AUTH-1", "revision": "1", "subject": "mf-1",
             "operation": "grant", "start": "2026-01-01",
             "end": "2100-01-01", "parameters": ["2042-B2", "430"]}
    event.update(over)
    return event


def events_request(events, now="2026-09-05", subjects=("mf-1",)):
    return {"now": now, "subjects": list(subjects), "events": events}


def transport_request(commits, pages, generation=None, now="2026-09-05",
                      subjects=("mf-1",)):
    transport = {"commits": commits, "pages": pages}
    if generation is not None:
        transport["generation"] = generation
    return {"now": now, "subjects": list(subjects), "transport": transport}


def digest(events):
    payload = json.dumps(events, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def single_batch(events, batch="1"):
    return ([{"batch": batch, "parts": 1, "sha256": digest(events)}],
            [{"batch": batch, "part": 0, "events": events}])


def main():
    results = []

    # events-only requests remain supported
    results.append(check("events grant in window", events_request(
        [base_event()]), 0, {"mf-1": ["2042-B2", "430"]}))
    results.append(check("events grant out of window", events_request(
        [base_event(start="2099-01-01", end="2100-01-01")]), 0,
        {"mf-1": None}))
    results.append(check("events withdraw wins", events_request(
        [base_event(), base_event(revision="2", operation="withdraw")]),
        0, {"mf-1": None}))
    results.append(check("events grant at end boundary excluded",
        events_request([base_event(end="2026-09-05")]), 0, {"mf-1": None}))
    results.append(check("events grant at start boundary included",
        events_request([base_event(start="2026-09-05")]), 0,
        {"mf-1": ["2042-B2", "430"]}))
    results.append(check("unknown subject excluded from projection",
        events_request([base_event()], subjects=("mf-1", "mf-9")), 0,
        {"mf-1": ["2042-B2", "430"]}))
    results.append(check("numeric revision order, not string order",
        events_request([base_event(revision="10",
                                   parameters=["2099-B1", "1"]),
                        base_event(revision="9",
                                   parameters=["p", "2"])]),
        0, {"mf-1": ["2099-B1", "1"]}))
    results.append(check("correction moves subject", events_request(
        [base_event(subject="mf-1"),
         base_event(revision="2", subject="mf-2")],
        subjects=("mf-1", "mf-2")),
        0, {"mf-1": None, "mf-2": ["2042-B2", "430"]}))

    # events-only invalidity
    results.append(check("event carries an extra key", events_request(
        [dict(base_event(), note="x")]), 1))
    results.append(check("event misses a key", events_request(
        [{k: v for k, v in base_event().items() if k != "end"}]), 1))
    results.append(check("leading-zero revision", events_request(
        [base_event(revision="01")]), 1))
    results.append(check("start not before end", events_request(
        [base_event(start="2026-05-05", end="2026-05-05")]), 1))
    results.append(check("operation outside grant/withdraw", events_request(
        [base_event(operation="suspend")]), 1))
    results.append(check("parameters not strings", events_request(
        [base_event(parameters=["2042-B2", 430])]), 1))
    results.append(check("conflicting equal revisions", events_request(
        [base_event(parameters=["2099-B1", "999"]),
         base_event(parameters=["2099-B1", "777"])]), 1))
    results.append(check("identical repeated revisions idempotent",
        events_request([base_event(), base_event()]), 0,
        {"mf-1": ["2042-B2", "430"]}))
    results.append(check("multiple active authorizations for a subject",
        events_request([base_event(),
                        base_event(authorization="AUTH-2",
                                   parameters=["2099-B1", "1"])]), 1))
    results.append(check("correction to another subject leaves it bare",
        events_request([base_event(),
                        base_event(revision="2", subject="mf-2",
                                   parameters=["2042-B2", "9"])]),
        0, {"mf-1": None}))

    # transport without a generation is an unreleased trial delivery
    commits, pages = single_batch([base_event()])
    results.append(check("unreleased trial delivery accepted",
        transport_request(commits, pages), 0,
        {"mf-1": ["2042-B2", "430"]}))
    bad = copy.deepcopy(commits)
    bad[0]["sha256"] = "0" * 64
    results.append(check("commit digest mismatch", transport_request(
        bad, pages), 1))
    missing = copy.deepcopy(pages)
    del missing[0]["events"][0]
    results.append(check("page content digest mismatch", transport_request(
        copy.deepcopy(commits), missing), 1))
    results.append(check("uncommitted page", transport_request(
        copy.deepcopy(commits), pages + [{"batch": "9", "part": 0,
                                          "events": []}]), 1))
    results.append(check("missing part", transport_request(
        [{"batch": "1", "parts": 2, "sha256": digest([base_event()])}],
        pages), 1))
    results.append(check("part outside commit", transport_request(
        [{"batch": "1", "parts": 1, "sha256": digest([base_event()])}],
        [{"batch": "1", "part": 0, "events": [base_event()]},
         {"batch": "1", "part": 1, "events": []}]), 1))
    dup = copy.deepcopy(pages)
    results.append(check("identical page replay idempotent",
        transport_request(commits, pages + dup), 0,
        {"mf-1": ["2042-B2", "430"]}))
    results.append(check("conflicting page replay", transport_request(
        copy.deepcopy(commits), pages + [{"batch": "1", "part": 0,
                                          "events": []}]), 1))

    # generation transports are checked against the manifests
    g1 = {"generations": {"g1": {"batches": {"1": digest([base_event()])}}}}
    results.append(check("generation matches its manifest",
        transport_request(commits, pages, generation="g1"), 0,
        {"mf-1": ["2042-B2", "430"]}, manifests=g1))
    g2 = {"generations": {"g2": {"batches": {"1": digest([base_event()]),
                                             "2": "f" * 64}}}}
    results.append(check("transport short of the manifest", transport_request(
        commits, pages, generation="g2"), 1, manifests=g2))
    g3 = {"generations": {"g3": {"batches": {"1": "e" * 64}}}}
    results.append(check("transport digest differs from manifest",
        transport_request(commits, pages, generation="g3"), 1, manifests=g3))
    g4 = {"generations": {"g4": {"batches": {}}}}
    results.append(check("transport commits an unlisted batch",
        transport_request(commits, pages, generation="g4"), 1, manifests=g4))
    g5 = {"generations": {}}
    results.append(check("manifest names no such generation",
        transport_request(commits, pages, generation="gX"), 1, manifests=g5))

    # the release feed's own candidates
    feed = json.load(open(os.path.join(os.path.dirname(__file__), "..",
                                       "..", "policy",
                                       "authorizations.json")))
    admissible = set()
    for candidate in feed["candidates"]:
        proc = run(candidate["request"], None)
        print("%-38s rc=%d %s" % ("feed candidate " + candidate["id"],
                                  proc.returncode,
                                  (proc.stdout.strip() or
                                   proc.stderr.strip())[:90]))
        if proc.returncode == 0:
            admissible.add(candidate["id"])
    print("admissible candidates: %s" % sorted(admissible))
    results.append(admissible == {"close-17"})

    print("RESULT: %d/%d checks passed" % (sum(1 for r in results if r),
                                           len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
