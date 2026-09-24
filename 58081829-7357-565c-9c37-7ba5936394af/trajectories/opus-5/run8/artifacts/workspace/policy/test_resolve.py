"""Contract tests for the authorization resolver.

Each test names the clause of `docs/policy-reconciliation.md` it holds, so a
later release that regresses the resolver fails here rather than at a base.
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import resolve as R  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(HERE, "authorizations.json")
RESOLVER = os.path.join(HERE, "resolve.py")
MANIFESTS = os.path.join(HERE, "manifests.json")

WINDOW = {"start": "2026-01-01", "end": "2100-01-01"}


def event(authorization, revision, subject, operation="grant",
          parameters=("2042-B2", "430"), **over):
    body = {
        "authorization": authorization,
        "revision": revision,
        "subject": subject,
        "operation": operation,
        "parameters": list(parameters),
    }
    body.update(WINDOW)
    body.update(over)
    return body


def digest(events):
    blob = json.dumps(events, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def delivery(batches, subjects=("mf-3480", "mf-6350"), now="2026-09-05",
             generation=None):
    """Build a transport request from {batch: [part events, ...]}."""
    commits, pages = [], []
    for batch, parts in batches.items():
        flat = [e for part in parts for e in part]
        commits.append({"batch": batch, "parts": len(parts),
                        "sha256": digest(flat)})
        for index, part in enumerate(parts):
            pages.append({"batch": batch, "part": index, "events": part})
    transport = {"commits": commits, "pages": pages}
    if generation is not None:
        transport["generation"] = generation
    return {"now": now, "subjects": list(subjects), "transport": transport}


def run_cli(request, env=None):
    environ = dict(os.environ)
    environ.pop("MARROWFLEET_POLICY_MANIFESTS", None)
    environ.update(env or {})
    return subprocess.run([sys.executable, RESOLVER], input=json.dumps(request),
                          text=True, capture_output=True, env=environ)


# --- the installed feed -----------------------------------------------------

def candidates():
    with open(FEED, "r", encoding="utf-8") as handle:
        return {c["id"]: c["request"] for c in json.load(handle)["candidates"]}


def test_g19_is_rejected_for_a_batch_the_manifest_lists_uncommitted():
    with pytest.raises(R.PolicyError):
        R.resolve(candidates()["close-19"])


def test_g18_is_rejected_for_conflicting_equal_revisions():
    with pytest.raises(R.PolicyError):
        R.resolve(candidates()["close-18"])


def test_g17_is_the_admissible_candidate():
    assert R.resolve(candidates()["close-17"]) == {
        "mf-3480": None,
        "mf-6350": ["2042-B2", "430"],
    }


def test_selection_is_stable_across_repeated_resolution():
    request = candidates()["close-17"]
    assert [R.resolve(request) for _ in range(3)] == [
        {"mf-3480": None, "mf-6350": ["2042-B2", "430"]}] * 3


def test_cli_exits_zero_only_for_the_admissible_candidate():
    got = candidates()
    assert run_cli(got["close-19"]).returncode != 0
    assert run_cli(got["close-18"]).returncode != 0
    accepted = run_cli(got["close-17"])
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout) == {
        "mf-3480": None, "mf-6350": ["2042-B2", "430"]}


def test_a_rejected_candidate_yields_no_partial_result():
    for name in ("close-19", "close-18"):
        assert run_cli(candidates()[name]).stdout == ""


# --- revision semantics -----------------------------------------------------

def test_a_higher_revision_replaces_an_earlier_one():
    request = delivery({"b": [[event("A", "2", "mf-3480", parameters=["x"]),
                               event("A", "10", "mf-3480",
                                     parameters=["2042-B2", "430"])]]})
    assert R.resolve(request)["mf-3480"] == ["2042-B2", "430"]


def test_revisions_compare_numerically_not_as_strings():
    request = delivery({"b": [[event("A", "9", "mf-3480", parameters=["old"]),
                               event("A", "10", "mf-3480",
                                     parameters=["new"])]]})
    assert R.resolve(request)["mf-3480"] == ["new"]


def test_identical_repeated_revisions_are_idempotent():
    twice = event("A", "3", "mf-3480", parameters=["p"])
    request = delivery({"b": [[twice], [dict(twice)]]})
    assert R.resolve(request)["mf-3480"] == ["p"]


def test_conflicting_equal_revisions_are_invalid():
    request = delivery({"b": [[event("A", "3", "mf-3480", parameters=["p"]),
                               event("A", "3", "mf-3480",
                                     parameters=["q"])]]})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_a_conflict_below_the_winning_revision_is_still_invalid():
    request = delivery({"b": [[event("A", "11", "mf-3480", parameters=["top"]),
                               event("A", "3", "mf-3480", parameters=["p"]),
                               event("A", "3", "mf-3480",
                                     parameters=["q"])]]})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_a_winning_withdrawal_contributes_no_decision():
    request = delivery({"b": [[event("A", "2", "mf-3480", parameters=["p"]),
                               event("A", "10", "mf-3480",
                                     operation="withdraw")]]})
    assert R.resolve(request)["mf-3480"] is None


def test_a_superseded_withdrawal_does_not_suppress_the_winner():
    request = delivery({"b": [[event("A", "2", "mf-3480",
                                     operation="withdraw"),
                               event("A", "10", "mf-3480",
                                     parameters=["p"])]]})
    assert R.resolve(request)["mf-3480"] == ["p"]


def test_a_correction_moving_subject_does_not_survive_on_the_former_one():
    request = delivery({"b": [[event("A", "2", "mf-3480", parameters=["old"]),
                               event("A", "10", "mf-6350",
                                     parameters=["new"])]]})
    assert R.resolve(request) == {"mf-3480": None, "mf-6350": ["new"]}


def test_a_correction_out_of_window_does_not_survive_on_the_former_subject():
    request = delivery({"b": [[
        event("A", "2", "mf-3480", parameters=["old"]),
        event("A", "10", "mf-3480", parameters=["new"],
              start="2099-01-01", end="2100-01-01"),
    ]]})
    assert R.resolve(request)["mf-3480"] is None


def test_multiple_active_authorizations_for_a_subject_are_an_error():
    request = delivery({"b": [[event("A", "1", "mf-3480", parameters=["p"]),
                               event("B", "1", "mf-3480",
                                     parameters=["q"])]]})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_an_unrequested_subject_is_excluded_from_the_projection():
    request = delivery({"b": [[event("A", "1", "mf-9999", parameters=["p"]),
                               event("B", "1", "mf-6350",
                                     parameters=["q"])]]})
    assert R.resolve(request) == {"mf-3480": None, "mf-6350": ["q"]}


def test_every_requested_subject_is_returned_as_a_key():
    request = delivery({"b": [[event("A", "1", "mf-6350", parameters=["q"])]]},
                       subjects=("mf-1130", "mf-3480", "mf-6350"))
    assert sorted(R.resolve(request)) == ["mf-1130", "mf-3480", "mf-6350"]


# --- the evaluation window --------------------------------------------------

def test_a_grant_applies_from_its_start_inclusive():
    request = delivery({"b": [[event("A", "1", "mf-3480", parameters=["p"],
                                     start="2026-09-05", end="2027-01-01")]]})
    assert R.resolve(request)["mf-3480"] == ["p"]


def test_a_grant_does_not_apply_at_its_end():
    request = delivery({"b": [[event("A", "1", "mf-3480", parameters=["p"],
                                     start="2026-01-01",
                                     end="2026-09-05")]]})
    assert R.resolve(request)["mf-3480"] is None


def test_the_evaluation_instant_is_the_requests_not_the_hosts():
    batches = {"b": [[event("A", "1", "mf-3480", parameters=["p"],
                            start="2030-01-01", end="2031-01-01")]]}
    assert R.resolve(delivery(batches, now="2030-06-01"))["mf-3480"] == ["p"]
    assert R.resolve(delivery(batches, now="2026-09-05"))["mf-3480"] is None


# --- delivery envelope ------------------------------------------------------

def test_parts_concatenate_in_numeric_order_not_transport_order():
    first = [event("A", "1", "mf-3480", parameters=["p"])]
    second = [event("B", "1", "mf-6350", parameters=["q"])]
    request = delivery({"b": [first, second]})
    request["transport"]["pages"].reverse()
    assert R.resolve(request) == {"mf-3480": ["p"], "mf-6350": ["q"]}


def test_parts_beyond_ten_order_numerically_not_lexically():
    parts = [[] for _ in range(11)]
    parts[2] = [event("A", "1", "mf-3480", parameters=["early"])]
    parts[10] = [event("A", "2", "mf-3480", parameters=["late"])]
    assert R.resolve(delivery({"b": parts}))["mf-3480"] == ["late"]


def test_every_committed_batch_belongs_to_the_history():
    request = delivery({
        "2": [[event("A", "2", "mf-3480", parameters=["old"])]],
        "40": [[event("A", "9", "mf-3480", parameters=["new"])]],
    })
    assert R.resolve(request)["mf-3480"] == ["new"]


def test_a_lexically_later_batch_does_not_shadow_an_earlier_one():
    request = delivery({
        "2": [[event("A", "10", "mf-3480", parameters=["winner"])]],
        "40": [[event("A", "9", "mf-3480", parameters=["loser"])]],
    })
    assert R.resolve(request)["mf-3480"] == ["winner"]


def test_a_missing_part_invalidates_the_request():
    request = delivery({"b": [[event("A", "1", "mf-3480")],
                              [event("B", "1", "mf-6350")]]})
    request["transport"]["pages"] = [request["transport"]["pages"][0]]
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_an_extra_part_invalidates_the_request():
    request = delivery({"b": [[event("A", "1", "mf-3480")]]})
    request["transport"]["pages"].append(
        {"batch": "b", "part": 1, "events": []})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_an_uncommitted_page_invalidates_the_request():
    request = delivery({"b": [[event("A", "1", "mf-3480")]]})
    request["transport"]["pages"].append(
        {"batch": "zz", "part": 0, "events": []})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_a_mismatching_digest_invalidates_the_request():
    request = delivery({"b": [[event("A", "1", "mf-3480")]]})
    request["transport"]["commits"][0]["sha256"] = "0" * 64
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_an_identical_page_replay_is_idempotent():
    request = delivery({"b": [[event("A", "1", "mf-3480",
                                     parameters=["p"])]]})
    page = request["transport"]["pages"][0]
    request["transport"]["pages"].append(json.loads(json.dumps(page)))
    assert R.resolve(request)["mf-3480"] == ["p"]


def test_an_identical_commit_replay_is_idempotent():
    request = delivery({"b": [[event("A", "1", "mf-3480",
                                     parameters=["p"])]]})
    commit = request["transport"]["commits"][0]
    request["transport"]["commits"].append(json.loads(json.dumps(commit)))
    assert R.resolve(request)["mf-3480"] == ["p"]


def test_a_conflicting_page_replay_invalidates_the_request():
    request = delivery({"b": [[event("A", "1", "mf-3480")]]})
    request["transport"]["pages"].append(
        {"batch": "b", "part": 0,
         "events": [event("A", "1", "mf-6350")]})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_a_conflicting_commit_replay_invalidates_the_request():
    request = delivery({"b": [[event("A", "1", "mf-3480")]]})
    request["transport"]["commits"].append(
        {"batch": "b", "parts": 2,
         "sha256": request["transport"]["commits"][0]["sha256"]})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_a_non_positive_parts_count_invalidates_the_request():
    request = delivery({"b": [[event("A", "1", "mf-3480")]]})
    request["transport"]["commits"][0]["parts"] = 0
    with pytest.raises(R.PolicyError):
        R.resolve(request)


# --- event validation covers the whole history ------------------------------

@pytest.mark.parametrize("bad", [
    {"revision": "01"},
    {"revision": "007"},
    {"revision": "-1"},
    {"revision": 3},
    {"revision": ""},
    {"revision": "1.0"},
    {"operation": "amend"},
    {"subject": ""},
    {"authorization": ""},
    {"parameters": "430"},
    {"parameters": [430]},
    {"start": "2026-1-1"},
    {"start": "2026-02-30"},
    {"start": "2100-01-01", "end": "2100-01-01"},
    {"start": "2100-01-01", "end": "2026-01-01"},
])
def test_a_malformed_event_invalidates_the_request(bad):
    body = event("A", "1", "mf-3480")
    body.update(bad)
    with pytest.raises(R.PolicyError):
        R.resolve(delivery({"b": [[body]]}))


def test_an_event_with_an_extra_key_is_invalid():
    body = event("A", "1", "mf-3480")
    body["note"] = "why"
    request = delivery({"b": [[body]]})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_an_event_with_a_missing_key_is_invalid():
    body = event("A", "1", "mf-3480")
    del body["end"]
    request = delivery({"b": [[body]]})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_a_superseded_event_is_validated_too():
    request = delivery({"b": [[event("A", "01", "mf-3480"),
                               event("A", "10", "mf-3480")]]})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_a_withdrawn_event_is_validated_too():
    withdrawn = event("A", "1", "mf-3480", operation="withdraw")
    withdrawn["parameters"] = [430]
    request = delivery({"b": [[withdrawn, event("A", "2", "mf-6350")]]})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_an_out_of_window_event_is_validated_too():
    stale = event("A", "1", "mf-3480", start="2000-13-01", end="2001-01-01")
    with pytest.raises(R.PolicyError):
        R.resolve(delivery({"b": [[stale]]}))


def test_an_unrequested_subjects_event_is_validated_too():
    other = event("A", "1", "mf-9999")
    other["operation"] = "amend"
    request = delivery({"b": [[other, event("B", "1", "mf-6350")]]})
    with pytest.raises(R.PolicyError):
        R.resolve(request)


# --- release manifests ------------------------------------------------------

def test_a_named_generation_must_commit_exactly_its_manifested_batches(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"generations": {"gX": {"batches": {
        "b": digest([event("A", "1", "mf-3480", parameters=["p"])])}}}}))
    os.environ["MARROWFLEET_POLICY_MANIFESTS"] = str(manifest)
    try:
        good = delivery({"b": [[event("A", "1", "mf-3480",
                                      parameters=["p"])]]},
                        generation="gX")
        assert R.resolve(good)["mf-3480"] == ["p"]

        extra = delivery({"b": [[event("A", "1", "mf-3480",
                                       parameters=["p"])]],
                          "c": [[event("B", "1", "mf-6350")]]},
                         generation="gX")
        with pytest.raises(R.PolicyError):
            R.resolve(extra)

        unnamed = delivery({"b": [[event("A", "1", "mf-3480",
                                         parameters=["p"])]]},
                           generation="gY")
        with pytest.raises(R.PolicyError):
            R.resolve(unnamed)
    finally:
        del os.environ["MARROWFLEET_POLICY_MANIFESTS"]


def test_a_manifested_digest_mismatch_invalidates_the_request(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(
        {"generations": {"gX": {"batches": {"b": "0" * 64}}}}))
    os.environ["MARROWFLEET_POLICY_MANIFESTS"] = str(manifest)
    try:
        request = delivery({"b": [[event("A", "1", "mf-3480")]]},
                           generation="gX")
        with pytest.raises(R.PolicyError):
            R.resolve(request)
    finally:
        del os.environ["MARROWFLEET_POLICY_MANIFESTS"]


def test_an_unnamed_generation_is_a_trial_checked_without_a_manifest():
    request = delivery({"b": [[event("A", "1", "mf-3480",
                                     parameters=["p"])]]})
    assert "generation" not in request["transport"]
    assert R.resolve(request)["mf-3480"] == ["p"]


def test_a_trial_may_be_checked_against_its_own_manifests(tmp_path):
    manifest = tmp_path / "m.json"
    body = event("A", "1", "mf-3480", parameters=["p"])
    manifest.write_text(json.dumps(
        {"generations": {"trial": {"batches": {"b": digest([body])}}}}))
    request = delivery({"b": [[body]]}, generation="trial")
    accepted = run_cli(request,
                       env={"MARROWFLEET_POLICY_MANIFESTS": str(manifest)})
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["mf-3480"] == ["p"]
    assert run_cli(request).returncode != 0


def test_the_installed_manifests_are_the_default_source():
    assert R.MANIFESTS_DEFAULT == MANIFESTS
    request = candidates()["close-17"]
    assert run_cli(request).returncode == 0


# --- events-only requests remain supported ----------------------------------

def test_an_events_only_request_still_resolves():
    request = {"now": "2026-09-05", "subjects": ["mf-3480", "mf-6350"],
               "events": [event("A", "1", "mf-6350",
                                parameters=["2042-B2", "430"])]}
    assert R.resolve(request) == {"mf-3480": None,
                                  "mf-6350": ["2042-B2", "430"]}


def test_an_events_only_request_is_validated():
    request = {"now": "2026-09-05", "subjects": ["mf-3480"],
               "events": [event("A", "01", "mf-3480")]}
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_a_request_carrying_both_events_and_a_transport_is_invalid():
    request = delivery({"b": [[event("A", "1", "mf-3480")]]})
    request["events"] = []
    with pytest.raises(R.PolicyError):
        R.resolve(request)


def test_a_request_carrying_neither_is_invalid():
    with pytest.raises(R.PolicyError):
        R.resolve({"now": "2026-09-05", "subjects": ["mf-3480"]})


@pytest.mark.parametrize("malformed", [
    {"subjects": ["mf-3480"], "events": []},
    {"now": "not-a-date", "subjects": ["mf-3480"], "events": []},
    {"now": "2026-09-05", "events": []},
    {"now": "2026-09-05", "subjects": "mf-3480", "events": []},
    {"now": "2026-09-05", "subjects": [""], "events": []},
])
def test_a_malformed_request_is_invalid(malformed):
    with pytest.raises(R.PolicyError):
        R.resolve(malformed)


# --- protected inputs -------------------------------------------------------

def test_resolving_leaves_the_feed_and_manifests_byte_for_byte():
    before = [open(path, "rb").read() for path in (FEED, MANIFESTS)]
    for request in candidates().values():
        try:
            R.resolve(request)
        except R.PolicyError:
            pass
    assert [open(path, "rb").read() for path in (FEED, MANIFESTS)] == before
