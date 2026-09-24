"""Contract tests for policy/resolve.py (docs/policy-reconciliation.md).

Run:  python3 -m pytest test_resolve.py -q
"""
import copy
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "policy")))
import resolve as resolver  # noqa: E402

FEED = json.load(open(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "policy", "authorizations.json"))))
CANDIDATES = {c["id"]: c["request"] for c in FEED["candidates"]}


def run_cli(request):
    proc = subprocess.run([sys.executable, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "policy", "resolve.py"))],
                          input=json.dumps(request), text=True, capture_output=True)
    return proc.returncode, proc.stdout, proc.stderr


def grant(authorization, revision, subject, start="2026-01-01", end="2100-01-01", parameters=("2099-B1", "999")):
    return {"authorization": authorization, "revision": revision, "subject": subject, "operation": "grant",
            "start": start, "end": end, "parameters": list(parameters)}


def withdraw(authorization, revision, subject, start="2026-01-01", end="2100-01-01", parameters=("2099-B1", "999")):
    return {"authorization": authorization, "revision": revision, "subject": subject, "operation": "withdraw",
            "start": start, "end": end, "parameters": list(parameters)}


def events_request(events, now="2026-09-05", subjects=("mf-3480", "mf-6350")):
    return {"now": now, "subjects": list(subjects), "events": list(events)}


# --- the release feed -------------------------------------------------------

def test_feed_candidates_in_order():
    assert run_cli(CANDIDATES["close-19"])[0] != 0, "close-19 must be rejected"
    assert run_cli(CANDIDATES["close-18"])[0] != 0, "close-18 must be rejected"
    code, out, _ = run_cli(CANDIDATES["close-17"])
    assert code == 0
    assert json.loads(out) == {"mf-3480": None, "mf-6350": ["2042-B2", "430"]}


def test_close_19_rejected_because_manifest_lists_batch_10():
    request = copy.deepcopy(CANDIDATES["close-19"])
    # committing batch 10 as well, at its manifest digest, makes g19 whole again
    g17 = CANDIDATES["close-17"]
    batch10_commit = [c for c in g17["transport"]["commits"] if c["batch"] == "10"][0]
    batch10_pages = [p for p in g17["transport"]["pages"] if p["batch"] == "10"]
    request["transport"]["commits"].append(batch10_commit)
    request["transport"]["pages"].extend(copy.deepcopy(batch10_pages))
    code, out, _ = run_cli(request)
    assert code == 0
    assert json.loads(out) == {"mf-3480": None, "mf-6350": ["2042-B2", "430"]}


# --- revision rules ---------------------------------------------------------

def test_higher_revision_replaces_subject_and_window():
    events = [grant("A", "1", "mf-3480"), grant("A", "2", "mf-9999", start="2026-09-01", end="2026-09-10", parameters=("x",))]
    assert resolver.resolve(events_request(events)) == {"mf-3480": None, "mf-6350": None}


def test_equal_revision_identical_replay_is_idempotent():
    events = [grant("A", "1", "mf-3480"), grant("A", "1", "mf-3480")]
    assert resolver.resolve(events_request(events)) == {"mf-3480": ["2099-B1", "999"], "mf-6350": None}


def test_equal_revision_conflict_is_invalid():
    events = [grant("A", "1", "mf-3480"), grant("A", "1", "mf-3480", parameters=("777",))]
    assert run_cli(events_request(events))[0] != 0
    try:
        resolver.resolve(events_request(events))
        assert False, "conflicting equal revision accepted"
    except resolver.Invalid:
        pass


def test_withdrawn_authorization_contributes_nothing():
    events = [grant("A", "1", "mf-3480"), withdraw("A", "2", "mf-3480")]
    assert resolver.resolve(events_request(events)) == {"mf-3480": None, "mf-6350": None}


def test_multiple_active_authorizations_for_one_subject_is_invalid():
    events = [grant("A", "1", "mf-3480"), grant("B", "1", "mf-3480")]
    assert run_cli(events_request(events))[0] != 0


def test_two_active_on_different_subjects_is_fine():
    events = [grant("A", "1", "mf-3480", parameters=("2041-B5", "300")),
              grant("B", "1", "mf-6350", parameters=("2042-B2", "430"))]
    assert resolver.resolve(events_request(events)) == {"mf-3480": ["2041-B5", "300"], "mf-6350": ["2042-B2", "430"]}


# --- window rules -----------------------------------------------------------

def test_grant_applies_within_start_le_now_lt_end():
    base = grant("A", "1", "mf-3480", start="2026-01-01", end="2026-09-06")
    assert resolver.resolve(events_request([base], now="2026-01-01"))["mf-3480"] == ["2099-B1", "999"]
    assert resolver.resolve(events_request([base], now="2026-09-05"))["mf-3480"] == ["2099-B1", "999"]
    assert resolver.resolve(events_request([base], now="2026-09-06"))["mf-3480"] is None


def test_host_clock_is_irrelevant():
    event = grant("A", "1", "mf-3480", start="2026-09-05", end="2026-09-06")
    assert resolver.resolve(events_request([event], now="2026-09-05"))["mf-3480"] == ["2099-B1", "999"]


# --- event validation -------------------------------------------------------

def test_invalid_events_reject_even_when_superseded():
    bad = [
        ("operation", "suspend"),
        ("start", "2026-02-30"),
        ("start", "2026-1-1"),
        ("end", "2100/01/01"),
        ("revision", "01"),
        ("revision", "1x"),
        ("revision", 1),
        ("subject", ""),
        ("authorization", ""),
        ("parameters", "430"),
        ("parameters", [430]),
    ]
    for key, value in bad:
        event = grant("A", "9", "mf-3480")
        event[key] = value
        good = grant("A", "10", "mf-3480")  # supersedes the bad event
        assert run_cli(events_request([event, good]))[0] != 0, (key, value)


def test_start_must_precede_end():
    event = grant("A", "1", "mf-3480", start="2026-09-06", end="2026-09-06")
    assert run_cli(events_request([event]))[0] != 0


def test_extra_event_key_is_invalid():
    event = grant("A", "1", "mf-3480")
    event["note"] = "x"
    assert run_cli(events_request([event]))[0] != 0


def test_unrequested_and_out_of_window_history_must_still_be_valid():
    bad = grant("Z", "1", "mf-0000")
    bad["operation"] = "nope"
    assert run_cli(events_request([bad, grant("A", "1", "mf-3480")]))[0] != 0


def test_unknown_subjects_are_excluded_from_projection():
    events = [grant("A", "1", "mf-0000"), grant("B", "1", "mf-3480", parameters=("p",))]
    assert resolver.resolve(events_request(events)) == {"mf-3480": ["p"], "mf-6350": None}


# --- transport --------------------------------------------------------------

def make_transport(events_by_batch, generation=None):
    import hashlib
    commits, pages = [], []
    for batch, parts in events_by_batch.items():
        for part, events in enumerate(parts):
            pages.append({"batch": batch, "part": part, "events": events})
        whole = [e for part in parts for e in part]
        digest = hashlib.sha256(json.dumps(whole, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        commits.append({"batch": batch, "parts": len(parts), "sha256": digest})
    transport = {"commits": commits, "pages": pages}
    if generation:
        transport["generation"] = generation
    return {"now": "2026-09-05", "subjects": ["mf-3480", "mf-6350"], "transport": transport}


def test_transport_pages_concatenate_in_part_order():
    request = make_transport({"2": [[grant("A", "2", "mf-3480", parameters=("second",))], [grant("A", "1", "mf-3480")]]})
    assert resolver.resolve(request) == {"mf-3480": ["second"], "mf-6350": None}


def test_identical_page_replay_is_idempotent():
    request = make_transport({"2": [[grant("A", "1", "mf-3480")]]})
    request["transport"]["pages"].append(copy.deepcopy(request["transport"]["pages"][0]))
    assert resolver.resolve(request) == {"mf-3480": ["2099-B1", "999"], "mf-6350": None}


def test_conflicting_page_replay_invalidates_request():
    request = make_transport({"2": [[grant("A", "1", "mf-3480")]]})
    request["transport"]["pages"].append({"batch": "2", "part": 0, "events": [grant("A", "1", "mf-3480", parameters=("x",))]})
    assert run_cli(request)[0] != 0


def test_missing_part_invalidates_request():
    request = make_transport({"2": [[grant("A", "1", "mf-3480")], [grant("A", "2", "mf-3480")]]})
    request["transport"]["pages"] = [p for p in request["transport"]["pages"] if p["part"] != 1]
    assert run_cli(request)[0] != 0


def test_extra_part_beyond_commit_invalidates_request():
    request = make_transport({"2": [[grant("A", "1", "mf-3480")]]})
    request["transport"]["pages"].append({"batch": "2", "part": 1, "events": []})
    request["transport"]["commits"][0]["parts"] = 1
    # digest still matches part 0 alone, but the page is an uncommitted extra part
    import hashlib
    digest = hashlib.sha256(json.dumps([grant("A", "1", "mf-3480")], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    request["transport"]["commits"][0]["sha256"] = digest
    assert run_cli(request)[0] != 0


def test_uncommitted_page_invalidates_request():
    request = make_transport({"2": [[grant("A", "1", "mf-3480")]]})
    request["transport"]["pages"].append({"batch": "40", "part": 0, "events": []})
    assert run_cli(request)[0] != 0


def test_digest_mismatch_invalidates_request():
    request = make_transport({"2": [[grant("A", "1", "mf-3480")]]})
    request["transport"]["commits"][0]["sha256"] = "0" * 64
    assert run_cli(request)[0] != 0


def test_all_committed_batches_belong_to_history():
    request = make_transport({"2": [[grant("A", "2", "mf-3480", parameters=("from-2",))]],
                              "40": [[grant("A", "1", "mf-3480")]]})
    assert resolver.resolve(request) == {"mf-3480": ["from-2"], "mf-6350": None}


# --- manifests --------------------------------------------------------------

def test_generation_must_match_manifest_batch_set():
    request = make_transport({"2": [[grant("A", "1", "mf-3480")]]}, generation="g19")
    assert run_cli(request)[0] != 0  # manifest lists 10, 2, 40


def test_generation_must_match_manifest_digests():
    request = make_transport({"2": [[grant("A", "1", "mf-3480")]]}, generation="g17")
    assert run_cli(request)[0] != 0  # g17's batch 2 digest is a different history


def test_unknown_generation_invalidates_request():
    request = make_transport({"2": [[grant("A", "1", "mf-3480")]]}, generation="g99")
    assert run_cli(request)[0] != 0


def test_trial_delivery_without_generation_needs_no_manifest():
    request = make_transport({"2": [[grant("A", "1", "mf-3480")]]})
    assert resolver.resolve(request) == {"mf-3480": ["2099-B1", "999"], "mf-6350": None}


def test_manifests_env_var_selects_trial_manifests(tmp_path):
    good = make_transport({"9": [[grant("A", "1", "mf-3480")]]})
    manifest = {"generations": {"gX": {"batches": {good["transport"]["commits"][0]["sha256"] and "9": good["transport"]["commits"][0]["sha256"]}}}}
    path = tmp_path / "manifests.json"
    path.write_text(json.dumps(manifest))
    good["transport"]["generation"] = "gX"
    env = dict(os.environ, MARROWFLEET_POLICY_MANIFESTS=str(path))
    proc = subprocess.run([sys.executable, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "policy", "resolve.py"))],
                          input=json.dumps(good), text=True, capture_output=True, env=env)
    assert proc.returncode == 0
    bad = make_transport({"9": [[grant("A", "1", "mf-3480", parameters=("other",))]]}, generation="gX")
    bad["transport"]["commits"][0]["sha256"] = good["transport"]["commits"][0]["sha256"]
    proc = subprocess.run([sys.executable, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "policy", "resolve.py"))],
                          input=json.dumps(bad), text=True, capture_output=True, env=env)
    assert proc.returncode != 0


# --- no partial results -----------------------------------------------------

def test_no_partial_result_on_error(capsys):
    try:
        resolver.resolve(events_request([grant("A", "1", "mf-3480"), grant("A", "1", "mf-3480", parameters=("x",))]))
        assert False
    except resolver.Invalid:
        pass


def test_request_without_transport_or_events_is_invalid():
    assert run_cli({"now": "2026-09-05", "subjects": ["mf-3480"]})[0] != 0
