#!/usr/bin/env python3
"""Trial deliveries for the authorization resolver (docs/policy-reconciliation.md).

Each case is a private request; ACCEPT means exit 0 with the projection shown,
REJECT means a nonzero exit. Run:  python3 policy/resolve-trials.py
"""
import copy
import json
import os
import subprocess
import sys
import tempfile

RESOLVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolve.py")
MANIFESTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifests.json")


def run(request, manifests=MANIFESTS):
    env = dict(os.environ, MARROWFLEET_POLICY_MANIFESTS=manifests)
    proc = subprocess.run([sys.executable, RESOLVER], input=json.dumps(request),
                          text=True, capture_output=True, env=env)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def digests_of(pages, commits):
    batches = {}
    for page in pages:
        batches.setdefault(page["batch"], {})[page["part"]] = page["events"]
    out = {}
    for commit in commits:
        events = [e for part in sorted(batches[commit["batch"]])
                  for e in batches[commit["batch"]][part]]
        import hashlib
        out[commit["batch"]] = hashlib.sha256(json.dumps(
            events, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    return out


def event(auth, rev, subject, op="grant", start="2026-01-01", end="2100-01-01", params=("2042-B2", "430")):
    return {"authorization": auth, "revision": str(rev), "subject": subject,
            "operation": op, "start": start, "end": end, "parameters": list(params)}


CASES = []


def case(name, expect, request, manifests=MANIFESTS):
    CASES.append((name, expect, request, manifests))


def transport(pages, commits, generation=None):
    t = {"commits": commits, "pages": pages}
    if generation is not None:
        t["generation"] = generation
    return t


def commit(batch, parts, sha):
    return {"batch": batch, "parts": parts, "sha256": sha}


# --- events-only requests -------------------------------------------------
case("events-only projection", "accept",
     {"now": "2026-09-05", "subjects": ["mf-1", "mf-2"],
      "events": [event("A", 1, "mf-1"), event("A", 2, "mf-2", params=("2099-B1", "77"))]})
case("events-only later revision governs (higher numeric, not lexicographic)", "accept",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", 10, "mf-1", params=("2099-B1", "10")),
                 event("A", 9, "mf-1", params=("2099-B1", "9"))]})
case("events-only conflicting equal revisions", "reject",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", 2, "mf-1", params=("2099-B1", "1")),
                 event("A", 2, "mf-1", params=("2099-B1", "2"))]})
case("events-only identical repeat is idempotent", "accept",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", 2, "mf-1"), event("A", 2, "mf-1")]})
case("events-only unknown subject excluded", "accept",
     {"now": "2026-09-05", "subjects": ["mf-1", "mf-ghost"],
      "events": [event("A", 1, "mf-1")]})
case("events-only leading-zero revision", "reject",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", "02", "mf-1")]})
case("events-only start not before end", "reject",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", 1, "mf-1", start="2026-05-05", end="2026-05-05")]})
case("events-only bad operation", "reject",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [dict(event("A", 1, "mf-1"), operation="revoke")]})
case("events-only extra event key", "reject",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [dict(event("A", 1, "mf-1"), note="x")]})
case("events-only non-string parameter", "reject",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", 1, "mf-1", params=(430,))]})

# --- window and withdrawal semantics --------------------------------------
case("grant applies at start boundary", "accept",
     {"now": "2026-01-01", "subjects": ["mf-1"], "events": [event("A", 1, "mf-1")]})
case("grant does not apply at end boundary", "accept",
     {"now": "2100-01-01", "subjects": ["mf-1"], "events": [event("A", 1, "mf-1")]})
case("winning withdrawal leaves nothing", "accept",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", 1, "mf-1"), event("A", 2, "mf-1", op="withdraw")]})
case("withdrawn then re-granted at a higher revision", "accept",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", 1, "mf-1"), event("A", 2, "mf-1", op="withdraw"),
                 event("A", 3, "mf-1", params=("2041-B5", "300"))]})
case("correction moves subject; former subject null, other subject excluded", "accept",
     {"now": "2026-09-05", "subjects": ["mf-1", "mf-2"],
      "events": [event("A", 1, "mf-1", params=("2041-B5", "300")),
                 event("A", 2, "mf-2", params=("2042-B2", "430"))]})
case("correction moves grant out of window; subject null", "accept",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", 1, "mf-1"),
                 event("A", 2, "mf-1", start="2050-01-01", end="2060-01-01")]})
case("multiple active authorizations for one subject", "reject",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", 1, "mf-1"), event("B", 1, "mf-1")]})
case("superseded event is still validated", "reject",
     {"now": "2026-09-05", "subjects": ["mf-1"],
      "events": [event("A", 1, "mf-1", start="2050-13-01", end="2060-01-01"),
                 event("A", 2, "mf-1")]})

# --- segmented deliveries --------------------------------------------------
PAGES_A = [{"batch": "b1", "part": 0, "events": [event("A", 1, "mf-1")]},
           {"batch": "b1", "part": 1, "events": [event("A", 2, "mf-2")]}]
COMMITS_A = [commit("b1", 2, None)]

def build(pages, commits_with_placeholder, generation=None):
    commits = copy.deepcopy(commits_with_placeholder)
    real = digests_of(pages, commits)
    for c in commits:
        if c["sha256"] is None:
            c["sha256"] = real[c["batch"]]
    request = {"now": "2026-09-05", "subjects": ["mf-1", "mf-2"]}
    request["transport"] = transport(pages, commits, generation)
    return request, real

req, _ = build(PAGES_A, COMMITS_A)
case("unreleased trial delivery (no generation)", "accept", req)

req, _ = build(PAGES_A, [commit("b1", 2, None), commit("b1", 2, None)])
case("identical commit replay is idempotent", "accept", req)

req, real = build(PAGES_A, COMMITS_A)
req["transport"]["pages"] = PAGES_A + [copy.deepcopy(PAGES_A[0])]
case("identical page replay is idempotent", "accept", req)

req, _ = build(PAGES_A, COMMITS_A)
conflict = copy.deepcopy(PAGES_A[1]); conflict["events"] = [event("A", 2, "mf-2", params=("2099-B1", "1"))]
req["transport"]["pages"] = [PAGES_A[0], PAGES_A[1], conflict]
case("conflicting page replay", "reject", req)

req, _ = build(PAGES_A, COMMITS_A)
req["transport"]["pages"] = [PAGES_A[0]]
case("missing part", "reject", req)

req, _ = build(PAGES_A, COMMITS_A)
req["transport"]["pages"] = PAGES_A + [{"batch": "b1", "part": 2, "events": []}]
case("extra part beyond committed parts", "reject", req)

req, _ = build(PAGES_A, COMMITS_A)
req["transport"]["pages"] = PAGES_A + [{"batch": "b9", "part": 0, "events": []}]
case("uncommitted page", "reject", req)

req, _ = build(PAGES_A, COMMITS_A)
req["transport"]["commits"][0]["sha256"] = "0" * 64
case("page digest does not match the commit", "reject", req)

req, _ = build(PAGES_A, [commit("b1", 3, None)])
case("parts count does not match pages", "reject", req)

req, _ = build(PAGES_A, [commit("b1", 0, None)])
case("zero parts", "reject", req)

# --- released deliveries against the manifests ------------------------------
with open(MANIFESTS, encoding="utf-8") as handle:
    MANIFEST_BODY = json.load(handle)

def with_manifest(request, generation, batches):
    body = copy.deepcopy(MANIFEST_BODY)
    body["generations"][generation] = {"batches": batches}
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(body, tmp)
    tmp.close()
    request = copy.deepcopy(request)
    request["transport"]["generation"] = generation
    return request, tmp.name

req, real = build(PAGES_A, COMMITS_A)
r, tmp = with_manifest(req, "trial-x", {"b1": real["b1"]})
case("released delivery matching its manifest", "accept", r, tmp)

req, real = build(PAGES_A, COMMITS_A)
r, tmp = with_manifest(req, "trial-y", {})
case("manifest lists no batch the transport commits", "reject", r, tmp)

req, real = build(PAGES_A, COMMITS_A)
r, tmp = with_manifest(req, "trial-z", {"b1": real["b1"], "b2": "1" * 64})
case("manifest lists a batch the transport does not commit", "reject", r, tmp)

req, real = build(PAGES_A, COMMITS_A)
r, tmp = with_manifest(req, "trial-w", {"b1": "2" * 64})
case("committed digest differs from the manifest", "reject", r, tmp)

req, real = build(PAGES_A, COMMITS_A)
request = copy.deepcopy(req)
request["transport"]["generation"] = "trial-missing"
case("generation the manifest does not name", "reject", request)

# --- the feed's own candidates ----------------------------------------------
FEED = json.load(open("/workspace/policy/authorizations.json"))
for candidate in FEED["candidates"]:
    case("feed candidate %s" % candidate["id"],
         "accept" if candidate["id"] == "close-17" else "reject", candidate["request"])


def main():
    failures = 0
    for name, expect, request, manifests in CASES:
        code, out, err = run(request, manifests)
        accepted = code == 0
        verdict = "accept" if accepted else "reject"
        flag = "ok  " if verdict == expect else "FAIL"
        if verdict != expect:
            failures += 1
        detail = out if accepted else err
        print("%s %-24s expect=%-6s got=%-6s  %s" % (flag, name, expect, verdict, detail[:110]))
    print("\n%d case(s), %d failure(s)" % (len(CASES), failures))
    return 1 if failures else 0


sys.exit(main())
