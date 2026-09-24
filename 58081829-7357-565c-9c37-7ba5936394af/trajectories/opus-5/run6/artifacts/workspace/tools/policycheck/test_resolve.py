#!/usr/bin/env python3
"""test_resolve - the reconciliation contract the release resolver must meet.

Run it after any change to policy/resolve.py, and before a release that carries
a new feed or new manifests:

    python3 tools/policycheck/test_resolve.py

Exit 0 when every case holds, 1 otherwise.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RESOLVER = os.path.join(REPO, "policy", "resolve.py")
FEED = os.path.join(REPO, "policy", "authorizations.json")
sys.path.insert(0, os.path.join(REPO, "policy"))

import resolve as resolver  # noqa: E402

NOW = "2026-09-05"
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def event(authorization="AUTH-1", revision="1", subject="mf-1", operation="grant",
          start="2026-01-01", end="2100-01-01", parameters=("2042-B2", "430")):
    if isinstance(parameters, tuple):
        parameters = list(parameters)
    return {"authorization": authorization, "revision": revision,
            "subject": subject, "operation": operation, "start": start,
            "end": end, "parameters": parameters}


def digest(events):
    return hashlib.sha256(json.dumps(
        events, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


def request(events=None, subjects=("mf-1",), now=NOW, transport=None):
    body = {"now": now, "subjects": list(subjects)}
    if transport is None:
        body["events"] = list(events or [])
    else:
        body["transport"] = transport
    return body


def delivery(batches, generation=None):
    """A transport delivering {batch: [page events, ...]}."""
    commits = []
    pages = []
    for batch, parts in batches.items():
        commits.append({"batch": batch, "parts": len(parts),
                        "sha256": digest([e for part in parts for e in part])})
        for index, part in enumerate(parts):
            pages.append({"batch": batch, "part": index, "events": part})
    transport = {"commits": commits, "pages": pages}
    if generation is not None:
        transport["generation"] = generation
    return transport


def rejects(body, because):
    try:
        resolver.resolve(body)
    except resolver.PolicyError:
        return
    raise AssertionError("accepted a request it must reject: %s" % because)


@case
def a_grant_in_window_projects_its_parameters():
    assert resolver.resolve(request([event()])) == {"mf-1": ["2042-B2", "430"]}


@case
def a_subject_without_an_authorization_projects_null():
    assert resolver.resolve(request([], subjects=("mf-1", "mf-2"))) == {
        "mf-1": None, "mf-2": None}


@case
def unrequested_subjects_are_excluded_from_the_projection():
    body = request([event(subject="mf-2")], subjects=("mf-1",))
    assert resolver.resolve(body) == {"mf-1": None}


@case
def the_window_is_half_open():
    assert resolver.resolve(request([event(start=NOW)]))["mf-1"] is not None
    assert resolver.resolve(request([event(end=NOW)]))["mf-1"] is None


@case
def the_highest_revision_governs_whatever_its_order():
    events = [event(revision="10", parameters=("2042-B2", "430")),
              event(revision="9", parameters=("2041-B5", "300"))]
    assert resolver.resolve(request(events)) == {"mf-1": ["2042-B2", "430"]}
    assert resolver.resolve(request(events[::-1])) == {"mf-1": ["2042-B2", "430"]}


@case
def a_winning_withdrawal_contributes_no_decision():
    events = [event(revision="2"), event(revision="10", operation="withdraw")]
    assert resolver.resolve(request(events)) == {"mf-1": None}


@case
def a_correction_does_not_survive_on_the_former_subject():
    events = [event(revision="2", subject="mf-1"),
              event(revision="3", subject="mf-2")]
    assert resolver.resolve(request(events, subjects=("mf-1", "mf-2"))) == {
        "mf-1": None, "mf-2": ["2042-B2", "430"]}


@case
def a_correction_may_move_outside_the_window():
    events = [event(revision="2"),
              event(revision="3", start="2030-01-01", end="2031-01-01")]
    assert resolver.resolve(request(events)) == {"mf-1": None}


@case
def repeated_identical_revisions_are_idempotent():
    assert resolver.resolve(request([event(), event()])) == {
        "mf-1": ["2042-B2", "430"]}


@case
def conflicting_equal_revisions_are_invalid_even_when_superseded():
    rejects(request([event(revision="2", parameters=("a", "1")),
                     event(revision="2", parameters=("b", "2")),
                     event(revision="11", parameters=("c", "3"))]),
            "revision 2 arrived twice, differently")


@case
def two_current_authorizations_for_one_subject_are_an_error():
    rejects(request([event(authorization="AUTH-1"),
                     event(authorization="AUTH-2")]),
            "one subject cannot hold two current grants")


@case
def every_event_is_validated_however_little_it_governs():
    for broken, because in (
            (dict(event(), extra="x"), "an extra key"),
            ({k: v for k, v in event().items() if k != "end"}, "a missing key"),
            (event(revision="01"), "a non-canonical revision"),
            (event(revision=2), "a revision that is not a string"),
            (event(operation="revoke"), "an unknown operation"),
            (event(authorization=""), "an empty identifier"),
            (event(subject=""), "an empty subject"),
            (event(start="2026-1-1"), "a date that is not ISO"),
            (event(start="2026-02-30"), "a date that is not real"),
            (event(start="2026-01-01", end="2026-01-01"), "start not before end"),
            (event(parameters="430"), "parameters that are not a list"),
            (event(parameters=[430]), "a parameter that is not a string")):
        rejects(request([event(revision="9", subject="mf-9"), broken]), because)
        rejects(request([broken], subjects=("mf-9",)), because)


@case
def a_request_carries_either_events_or_a_transport():
    body = request([event()])
    body["transport"] = delivery({"1": [[event()]]})
    rejects(body, "both events and a transport")
    rejects({"now": NOW, "subjects": ["mf-1"]}, "neither events nor transport")


@case
def a_transport_delivers_every_committed_batch():
    transport = delivery({"2": [[event(revision="2")], []],
                          "40": [[event(revision="12", parameters=("x", "1"))]]})
    assert resolver.resolve(request(transport=transport)) == {"mf-1": ["x", "1"]}


@case
def parts_concatenate_in_numeric_order_not_arrival_order():
    parts = [[event(revision="2", parameters=("first", "1"))],
             [event(revision="3", parameters=("second", "2"))]]
    transport = delivery({"10": parts})
    transport["pages"].reverse()
    assert resolver.resolve(request(transport=transport)) == {
        "mf-1": ["second", "2"]}


@case
def identical_replays_of_a_page_or_commit_are_idempotent():
    transport = delivery({"2": [[event()]]})
    transport["pages"].append(dict(transport["pages"][0]))
    transport["commits"].append(dict(transport["commits"][0]))
    assert resolver.resolve(request(transport=transport)) == {
        "mf-1": ["2042-B2", "430"]}


@case
def a_transport_that_does_not_reassemble_is_invalid():
    transport = delivery({"2": [[event()]]})
    conflicting = dict(transport["pages"][0], events=[event(revision="4")])
    rejects(request(transport=dict(transport, pages=transport["pages"] + [conflicting])),
            "a conflicting page replay")

    transport = delivery({"2": [[event()], [event(revision="3")]]})
    rejects(request(transport=dict(transport, pages=transport["pages"][:1])),
            "a missing part")

    transport = delivery({"2": [[event()]]})
    extra = {"batch": "2", "part": 4, "events": []}
    rejects(request(transport=dict(transport, pages=transport["pages"] + [extra])),
            "a part beyond the committed count")

    transport = delivery({"2": [[event()]]})
    stray = {"batch": "7", "part": 0, "events": [event(revision="3")]}
    rejects(request(transport=dict(transport, pages=transport["pages"] + [stray])),
            "an uncommitted page")

    transport = delivery({"2": [[event()]]})
    transport["commits"][0]["sha256"] = "0" * 64
    rejects(request(transport=transport), "a digest that does not match")


@case
def a_released_generation_must_match_its_manifest():
    events = [event()]
    transport = delivery({"2": [events]}, generation="g1")
    listed = transport["commits"][0]["sha256"]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "manifests.json")
        os.environ["MARROWFLEET_POLICY_MANIFESTS"] = path
        try:
            write = lambda body: open(path, "w", encoding="utf-8").write(
                json.dumps(body))

            write({"generations": {"g1": {"batches": {"2": listed}}}})
            assert resolver.resolve(request(transport=transport)) == {
                "mf-1": ["2042-B2", "430"]}

            write({"generations": {"g1": {"batches": {
                "2": listed, "10": "a" * 64}}}})
            rejects(request(transport=transport),
                    "a manifest batch the delivery does not commit")

            write({"generations": {"g1": {"batches": {}}}})
            rejects(request(transport=transport),
                    "a committed batch the manifest does not list")

            write({"generations": {"g1": {"batches": {"2": "b" * 64}}}})
            rejects(request(transport=transport),
                    "a digest the manifest does not list")

            write({"generations": {"g2": {"batches": {"2": listed}}}})
            rejects(request(transport=transport),
                    "a generation the manifest does not name")

            trial = delivery({"2": [events]})
            assert resolver.resolve(request(transport=trial)) == {
                "mf-1": ["2042-B2", "430"]}
        finally:
            del os.environ["MARROWFLEET_POLICY_MANIFESTS"]


@case
def the_bureau_feed_resolves_to_the_candidate_the_manifests_release():
    with open(FEED, encoding="utf-8") as fh:
        feed = json.load(fh)
    outcome = []
    for candidate in feed["candidates"]:
        run = subprocess.run([sys.executable, RESOLVER],
                             input=json.dumps(candidate["request"]),
                             capture_output=True, text=True)
        outcome.append((candidate["id"], run.returncode, run.stdout.strip()))
    assert [(name, code) for name, code, _ in outcome] == [
        ("close-19", 1), ("close-18", 1), ("close-17", 0)], outcome
    assert json.loads(outcome[2][2]) == {"mf-3480": None,
                                         "mf-6350": ["2042-B2", "430"]}, outcome
    assert outcome[0][2] == "" and outcome[1][2] == "", "a partial result"


@case
def the_command_exits_nonzero_and_prints_nothing_on_an_invalid_history():
    run = subprocess.run([sys.executable, RESOLVER],
                         input=json.dumps(request([event(revision="01")])),
                         capture_output=True, text=True)
    assert run.returncode != 0 and run.stdout == "" and run.stderr, run


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s" % (fn.__name__, exc))
        else:
            print("ok   %s" % fn.__name__)
    print("%d case(s), %d failed" % (len(CASES), failed))
    return 1 if failed else 0


sys.exit(main())
