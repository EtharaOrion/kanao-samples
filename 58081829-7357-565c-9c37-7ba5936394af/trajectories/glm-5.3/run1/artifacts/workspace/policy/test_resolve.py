#!/usr/bin/env python3
"""Contract tests for policy/resolve.py (docs/policy-reconciliation.md)."""
import copy
import json
import os
import subprocess
import sys
import tempfile

RESOLVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolve.py")
RUNS = []

def canon(events):
    return json.dumps(events, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

import hashlib
def digest(events):
    return hashlib.sha256(canon(events)).hexdigest()

def ev(auth, rev, subject, op="grant", start="2026-01-01", end="2100-01-01", params=("2042-B2", "430")):
    return {"authorization": auth, "revision": str(rev), "subject": subject,
            "operation": op, "start": start, "end": end, "parameters": list(params)}

def run(request, manifests=None, env=None):
    environ = dict(os.environ)
    if manifests is not None:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(manifests, fh)
        environ["MARROWFLEET_POLICY_MANIFESTS"] = path
    elif env:
        environ.update(env)
    r = subprocess.run([sys.executable, RESOLVE], input=json.dumps(request),
                       capture_output=True, text=True, env=environ)
    out = r.stdout.strip()
    RUNS.append((r.returncode, out or r.stderr.strip()))
    return r.returncode, (json.loads(out) if r.returncode == 0 else None)

def check(name, got, want_rc, want_result=None):
    rc, result = got
    ok = rc == want_rc and (want_result is None or result == want_result)
    print(("PASS" if ok else "FAIL"), name, "->", got)
    return ok

ok = True

# 1 events-only request
ok &= check("events-only basic", run({"now": "2026-09-05", "subjects": ["a"],
    "events": [ev("A", 1, "a"), ev("A", 2, "a", params=("2099-B1", "1"))]}),
    0, {"a": ["2099-B1", "1"]})

# 2 unreleased trial transport (no generation), pages out of order, duplicate page
tr = {"commits": [{"batch": "2", "parts": 2, "sha256": digest([ev("A", 1, "a")])}],
      "pages": [{"batch": "2", "part": 1, "events": []},
                {"batch": "2", "part": 0, "events": [ev("A", 1, "a")]},
                {"batch": "2", "part": 0, "events": [ev("A", 1, "a")]}]}
ok &= check("trial transport idempotent replay", run({"now": "2026-09-05", "subjects": ["a"], "transport": tr}), 0, {"a": ["2042-B2", "430"]})

# 3 mismatched digest
tr3 = copy.deepcopy(tr); tr3["commits"][0]["sha256"] = "0" * 64
ok &= check("digest mismatch", run({"now": "2026-09-05", "subjects": ["a"], "transport": tr3}), 1)

# 4 missing part
tr4 = {"commits": [{"batch": "2", "parts": 2, "sha256": digest([ev("A", 1, "a")])}],
       "pages": [{"batch": "2", "part": 0, "events": [ev("A", 1, "a")]}]}
ok &= check("missing part", run({"now": "2026-09-05", "subjects": ["a"], "transport": tr4}), 1)

# 5 extra part
tr5 = {"commits": [{"batch": "2", "parts": 1, "sha256": digest([ev("A", 1, "a")])}],
       "pages": [{"batch": "2", "part": 0, "events": [ev("A", 1, "a")]},
                 {"batch": "2", "part": 1, "events": []}]}
ok &= check("extra part", run({"now": "2026-09-05", "subjects": ["a"], "transport": tr5}), 1)

# 6 uncommitted page
tr6 = copy.deepcopy(tr); tr6["pages"].append({"batch": "9", "part": 0, "events": []})
ok &= check("uncommitted page", run({"now": "2026-09-05", "subjects": ["a"], "transport": tr6}), 1)

# 7 conflicting page replay
tr7 = copy.deepcopy(tr)
tr7["pages"].append({"batch": "2", "part": 0, "events": [ev("A", 1, "a", params=("x",))]})
ok &= check("conflicting page replay", run({"now": "2026-09-05", "subjects": ["a"], "transport": tr7}), 1)

# 8 conflicting commit replay
tr8 = copy.deepcopy(tr); tr8["commits"].append({"batch": "2", "parts": 1, "sha256": digest([ev("A", 1, "a")])})
ok &= check("conflicting commit replay", run({"now": "2026-09-05", "subjects": ["a"], "transport": tr8}), 1)

def manifest_for(transport, generation="g1"):
    return {"generations": {generation: {"batches": {c["batch"]: c["sha256"] for c in transport["commits"]}}}}

# 9 generation not in manifest
trg = copy.deepcopy(tr); trg["generation"] = "g1"
ok &= check("unknown generation", run({"now": "2026-09-05", "subjects": ["a"], "transport": trg},
       manifests={"generations": {}}), 1)
ok &= check("named generation ok", run({"now": "2026-09-05", "subjects": ["a"], "transport": trg},
       manifests=manifest_for(trg)), 0, {"a": ["2042-B2", "430"]})

# 10 manifest digest mismatch
m10 = manifest_for(trg); m10["generations"]["g1"]["batches"]["2"] = "1" * 64
ok &= check("manifest digest mismatch", run({"now": "2026-09-05", "subjects": ["a"], "transport": trg}, manifests=m10), 1)

# 11 manifest lists a batch the transport does not commit
m11 = {"generations": {"g1": {"batches": {"2": trg["commits"][0]["sha256"], "10": "2" * 64}}}}
ok &= check("manifest batch not committed", run({"now": "2026-09-05", "subjects": ["a"], "transport": trg}, manifests=m11), 1)

# 11b transport commits a batch the manifest does not list
m11b = {"generations": {"g1": {"batches": {}}}}
ok &= check("committed batch not in manifest", run({"now": "2026-09-05", "subjects": ["a"], "transport": trg}, manifests=m11b), 1)

# 12 correction moves to another subject: older revision must not survive on the former one
tr12 = {"commits": [{"batch": "1", "parts": 1, "sha256": digest([ev("A", 1, "a"), ev("A", 2, "b", params=("x", "y"))])}],
        "pages": [{"batch": "1", "part": 0, "events": [ev("A", 1, "a"), ev("A", 2, "b", params=("x", "y"))]}]}
ok &= check("correction moves subject", run({"now": "2026-09-05", "subjects": ["a", "b"], "transport": tr12}),
       0, {"a": None, "b": ["x", "y"]})

# 13 correction moves outside the evaluation window
tr13 = {"commits": [{"batch": "1", "parts": 1, "sha256": digest([ev("A", 1, "a"), ev("A", 2, "a", start="2099-01-01")])}],
        "pages": [{"batch": "1", "part": 0, "events": [ev("A", 1, "a"), ev("A", 2, "a", start="2099-01-01")]}]}
ok &= check("correction out of window", run({"now": "2026-09-05", "subjects": ["a"], "transport": tr13}), 0, {"a": None})

# 14 multiple active authorizations for a subject
tr14 = {"commits": [{"batch": "1", "parts": 1, "sha256": digest([ev("A", 1, "s"), ev("B", 1, "s", params=("p",))])}],
        "pages": [{"batch": "1", "part": 0, "events": [ev("A", 1, "s"), ev("B", 1, "s", params=("p",))]}]}
ok &= check("multiple active authorizations", run({"now": "2026-09-05", "subjects": ["s"], "transport": tr14}), 1)

# 15 winning withdraw contributes no decision
tr15 = {"commits": [{"batch": "1", "parts": 1, "sha256": digest([ev("A", 1, "a"), ev("A", 2, "a", op="withdraw")])}],
        "pages": [{"batch": "1", "part": 0, "events": [ev("A", 1, "a"), ev("A", 2, "a", op="withdraw")]}]}
ok &= check("winning withdraw", run({"now": "2026-09-05", "subjects": ["a"], "transport": tr15}), 0, {"a": None})

# 16 invalid event keys / shapes
ok &= check("bad event keys", run({"now": "2026-09-05", "subjects": ["a"],
    "events": [{"authorization": "A", "revision": "1", "subject": "a"}]}), 1)
ok &= check("revision leading zero", run({"now": "2026-09-05", "subjects": ["a"],
    "events": [ev("A", "007", "a")]}), 1)
ok &= check("start equals end", run({"now": "2026-09-05", "subjects": ["a"],
    "events": [ev("A", 1, "a", start="2026-01-01", end="2026-01-01")]}), 1)
ok &= check("bad operation", run({"now": "2026-09-05", "subjects": ["a"],
    "events": [dict(ev("A", 1, "a"), operation="revoke")]}), 1)

# 17 window boundaries: start inclusive, end exclusive
ok &= check("now at start", run({"now": "2026-01-01", "subjects": ["a"],
    "events": [ev("A", 1, "a", start="2026-01-01", end="2027-01-01")]}), 0, {"a": ["2042-B2", "430"]})
ok &= check("now at end", run({"now": "2027-01-01", "subjects": ["a"],
    "events": [ev("A", 1, "a", start="2026-01-01", end="2027-01-01")]}), 0, {"a": None})

# 18 numeric revision ordering beats string ordering
ok &= check("numeric revisions", run({"now": "2026-09-05", "subjects": ["a"],
    "events": [ev("A", 2, "a", params=("keep",)), ev("A", 10, "a", params=("win",))]}), 0, {"a": ["win"]})

# 19 unrequested subjects excluded, every requested subject returned
ok &= check("projection keys", run({"now": "2026-09-05", "subjects": ["a", "c"],
    "events": [ev("A", 1, "b")]}), 0, {"a": None, "c": None})

# 20 invalid superseded event still invalidates
ok &= check("superseded event validated", run({"now": "2026-09-05", "subjects": ["a"],
    "events": [dict(ev("A", 1, "a"), end="2020-01-01"), ev("A", 2, "a")]}), 1)

# 21 no partial output on failure
r = subprocess.run([sys.executable, RESOLVE], input=json.dumps({"now": "x", "subjects": []}),
                   capture_output=True, text=True)
ok &= check("no partial result", (r.returncode, r.stdout.strip()), 1, "")
r = subprocess.run([sys.executable, RESOLVE], input="not json", capture_output=True, text=True)
ok &= check("unreadable request", (r.returncode, r.stdout.strip()), 2, "")

print("\n%d/%d passed" % (sum(1 for rc, _ in RUNS if True), 0) if False else "")
print("summary:", "ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)
