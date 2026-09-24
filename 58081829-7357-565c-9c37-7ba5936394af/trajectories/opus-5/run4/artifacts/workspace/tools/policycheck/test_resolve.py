#!/usr/bin/env python3
"""The reconciliation contract, checked against policy/resolve.py.

Every case here is a sentence of docs/policy-reconciliation.md. Run it before
and after any change to the resolver:

    python3 tools/policycheck/test_resolve.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESOLVER = os.path.join(ROOT, "policy", "resolve.py")
FEED = os.path.join(ROOT, "policy", "authorizations.json")
sys.path.insert(0, os.path.join(ROOT, "policy"))

import resolve as resolver  # noqa: E402


def event(authorization="AUTH-1", revision="1", subject="mf-3480",
          operation="grant", start="2026-01-01", end="2100-01-01",
          parameters=("2042-B2", "430")):
    return {"authorization": authorization, "revision": revision,
            "subject": subject, "operation": operation, "start": start,
            "end": end, "parameters": list(parameters)}


def digest(events):
    body = json.dumps(events, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def commit(batch, pages):
    """A commit for a batch whose parts are the given page event lists."""
    flat = [item for page in pages for item in page]
    return {"batch": batch, "parts": len(pages), "sha256": digest(flat)}


def pages(batch, page_events):
    return [{"batch": batch, "part": index, "events": events}
            for index, events in enumerate(page_events)]


def request(subjects=("mf-3480",), now="2026-09-05", **kwargs):
    out = {"now": now, "subjects": list(subjects)}
    out.update(kwargs)
    return out


class Projection(unittest.TestCase):
    def test_events_only_request_is_still_supported(self):
        decision = resolver.resolve(request(events=[event()]))
        self.assertEqual(decision, {"mf-3480": ["2042-B2", "430"]})

    def test_a_higher_revision_replaces_an_earlier_one(self):
        decision = resolver.resolve(request(events=[
            event(revision="2", parameters=["2099-B1", "999"]),
            event(revision="10", parameters=["2042-B2", "430"])]))
        self.assertEqual(decision, {"mf-3480": ["2042-B2", "430"]})

    def test_a_correction_moves_the_subject_and_the_older_one_does_not_survive(self):
        decision = resolver.resolve(request(
            subjects=["mf-3480", "mf-6350"], events=[
                event(revision="1", subject="mf-3480"),
                event(revision="2", subject="mf-6350")]))
        self.assertEqual(decision, {"mf-3480": None,
                                    "mf-6350": ["2042-B2", "430"]})

    def test_a_correction_out_of_the_window_leaves_no_decision(self):
        decision = resolver.resolve(request(events=[
            event(revision="1"),
            event(revision="2", start="2030-01-01", end="2031-01-01")]))
        self.assertEqual(decision, {"mf-3480": None})

    def test_a_winning_withdrawal_contributes_no_decision(self):
        decision = resolver.resolve(request(events=[
            event(revision="1"),
            event(revision="2", operation="withdraw")]))
        self.assertEqual(decision, {"mf-3480": None})

    def test_a_withdrawal_a_later_grant_supersedes_does_not_bind(self):
        decision = resolver.resolve(request(events=[
            event(revision="2", operation="withdraw"),
            event(revision="3")]))
        self.assertEqual(decision, {"mf-3480": ["2042-B2", "430"]})

    def test_the_grant_window_is_half_open(self):
        self.assertEqual(
            resolver.resolve(request(now="2026-01-01", events=[
                event(start="2026-01-01", end="2027-01-01")])),
            {"mf-3480": ["2042-B2", "430"]})
        self.assertEqual(
            resolver.resolve(request(now="2027-01-01", events=[
                event(start="2026-01-01", end="2027-01-01")])),
            {"mf-3480": None})

    def test_revisions_are_ordered_numerically_not_as_text(self):
        decision = resolver.resolve(request(events=[
            event(revision="9", parameters=["2099-B1", "999"]),
            event(revision="10", parameters=["2042-B2", "430"])]))
        self.assertEqual(decision, {"mf-3480": ["2042-B2", "430"]})

    def test_an_unknown_subject_is_excluded_from_the_projection(self):
        decision = resolver.resolve(request(events=[
            event(), event(authorization="AUTH-9", subject="mf-0000")]))
        self.assertEqual(decision, {"mf-3480": ["2042-B2", "430"]})

    def test_the_host_clock_is_irrelevant(self):
        first = resolver.resolve(request(events=[event()]))
        os.environ["TZ"] = "Pacific/Kiritimati"
        self.assertEqual(first, resolver.resolve(request(events=[event()])))

    def test_every_requested_subject_is_a_key(self):
        decision = resolver.resolve(request(subjects=["a", "b"], events=[]))
        self.assertEqual(decision, {"a": None, "b": None})


class Invalid(unittest.TestCase):
    def reject(self, **kwargs):
        with self.assertRaises(resolver.PolicyError):
            resolver.resolve(request(**kwargs))

    def test_conflicting_equal_revisions_are_invalid(self):
        self.reject(events=[event(revision="2", parameters=["a"]),
                            event(revision="2", parameters=["b"])])

    def test_a_conflict_below_the_winning_revision_is_still_invalid(self):
        self.reject(events=[event(revision="11"),
                            event(revision="2", parameters=["a"]),
                            event(revision="2", parameters=["b"])])

    def test_repeated_identical_revisions_are_idempotent(self):
        decision = resolver.resolve(request(events=[event(), event()]))
        self.assertEqual(decision, {"mf-3480": ["2042-B2", "430"]})

    def test_two_active_authorizations_on_one_subject_are_an_error(self):
        self.reject(events=[event(authorization="AUTH-1"),
                            event(authorization="AUTH-2")])

    def test_a_superseded_record_must_still_be_valid(self):
        self.reject(events=[event(revision="1", operation="cancel"),
                            event(revision="2")])

    def test_a_withdrawn_record_must_still_be_valid(self):
        self.reject(events=[event(revision="2", operation="withdraw",
                                  start="2027-01-01", end="2026-01-01")])

    def test_an_unrequested_subject_record_must_still_be_valid(self):
        self.reject(events=[event(), event(authorization="AUTH-9",
                                           subject="", revision="1")])

    def test_a_revision_with_a_leading_zero_is_invalid(self):
        self.reject(events=[event(revision="01")])

    def test_a_numeric_revision_is_invalid(self):
        self.reject(events=[dict(event(), revision=1)])

    def test_an_extra_event_key_is_invalid(self):
        self.reject(events=[dict(event(), note="x")])

    def test_a_missing_event_key_is_invalid(self):
        broken = event()
        del broken["parameters"]
        self.reject(events=[broken])

    def test_non_string_parameters_are_invalid(self):
        self.reject(events=[event(parameters=[430])])

    def test_a_start_not_before_end_is_invalid(self):
        self.reject(events=[event(start="2026-01-01", end="2026-01-01")])

    def test_a_malformed_date_is_invalid(self):
        self.reject(events=[event(start="2026-1-1")])
        self.reject(events=[event(start="2026-02-30")])

    def test_validation_precedes_projection(self):
        """A valid current grant does not excuse a broken record elsewhere."""
        self.reject(subjects=["mf-3480"],
                    events=[event(), event(authorization="AUTH-9",
                                           subject="mf-0000", revision="x")])


class Transport(unittest.TestCase):
    def reject(self, transport, **kwargs):
        with self.assertRaises(resolver.PolicyError):
            resolver.resolve(request(transport=transport, **kwargs))

    def test_parts_concatenate_in_numeric_order(self):
        first, second = [event(revision="1")], [event(revision="2")]
        transport = {"commits": [commit("b", [first, second])],
                     "pages": list(reversed(pages("b", [first, second])))}
        self.assertEqual(resolver.resolve(request(transport=transport)),
                         {"mf-3480": ["2042-B2", "430"]})

    def test_all_committed_batches_belong_to_the_history(self):
        old = [event(revision="1", parameters=["2099-B1", "999"])]
        new = [event(revision="2", parameters=["2042-B2", "430"])]
        transport = {"commits": [commit("40", [new]), commit("2", [old])],
                     "pages": pages("2", [old]) + pages("40", [new])}
        self.assertEqual(resolver.resolve(request(transport=transport)),
                         {"mf-3480": ["2042-B2", "430"]})

    def test_a_batch_is_not_chosen_by_the_largest_name(self):
        """'40' beats '2' as text; the history is both batches, not the newest."""
        withdrawal = [event(revision="10", operation="withdraw")]
        grant = [event(revision="2")]
        transport = {"commits": [commit("2", [grant]),
                                 commit("10", [withdrawal])],
                     "pages": pages("2", [grant]) + pages("10", [withdrawal])}
        self.assertEqual(resolver.resolve(request(transport=transport)),
                         {"mf-3480": None})

    def test_an_identical_replay_is_idempotent(self):
        body = [event()]
        transport = {"commits": [commit("b", [body]), commit("b", [body])],
                     "pages": pages("b", [body]) + pages("b", [body])}
        self.assertEqual(resolver.resolve(request(transport=transport)),
                         {"mf-3480": ["2042-B2", "430"]})

    def test_a_conflicting_page_replay_is_invalid(self):
        body = [event()]
        transport = {"commits": [commit("b", [body])],
                     "pages": pages("b", [body]) + [
                         {"batch": "b", "part": 0, "events": []}]}
        self.reject(transport)

    def test_a_conflicting_commit_replay_is_invalid(self):
        body = [event()]
        bad = dict(commit("b", [body]), sha256="0" * 64)
        self.reject({"commits": [commit("b", [body]), bad],
                     "pages": pages("b", [body])})

    def test_a_missing_part_is_invalid(self):
        first, second = [event(revision="1")], [event(revision="2")]
        self.reject({"commits": [commit("b", [first, second])],
                     "pages": pages("b", [first, second])[:1]})

    def test_an_extra_part_is_invalid(self):
        body = [event()]
        self.reject({"commits": [commit("b", [body])],
                     "pages": pages("b", [body]) + [
                         {"batch": "b", "part": 1, "events": []}]})

    def test_an_uncommitted_page_is_invalid(self):
        body = [event()]
        self.reject({"commits": [commit("b", [body])],
                     "pages": pages("b", [body]) + pages("other", [body])})

    def test_a_mismatching_digest_is_invalid(self):
        body = [event()]
        self.reject({"commits": [dict(commit("b", [body]), sha256="0" * 64)],
                     "pages": pages("b", [body])})

    def test_an_event_in_any_committed_batch_must_be_valid(self):
        good, bad = [event()], [dict(event(authorization="AUTH-9"), revision="01")]
        self.reject({"commits": [commit("a", [good]), commit("b", [bad])],
                     "pages": pages("a", [good]) + pages("b", [bad])})


class Manifests(unittest.TestCase):
    def setUp(self):
        self.body = [event()]
        self.handle = tempfile.NamedTemporaryFile("w", suffix=".json",
                                                  delete=False)
        json.dump({"generations": {"g1": {"batches": {
            "b": digest(self.body)}}}}, self.handle)
        self.handle.close()
        os.environ["MARROWFLEET_POLICY_MANIFESTS"] = self.handle.name

    def tearDown(self):
        del os.environ["MARROWFLEET_POLICY_MANIFESTS"]
        os.unlink(self.handle.name)

    def transport(self, **kwargs):
        out = {"generation": "g1", "commits": [commit("b", [self.body])],
               "pages": pages("b", [self.body])}
        out.update(kwargs)
        return out

    def reject(self, transport):
        with self.assertRaises(resolver.PolicyError):
            resolver.resolve(request(transport=transport))

    def test_a_released_generation_committing_its_manifest_batches_resolves(self):
        self.assertEqual(resolver.resolve(request(transport=self.transport())),
                         {"mf-3480": ["2042-B2", "430"]})

    def test_a_generation_the_manifest_does_not_name_is_invalid(self):
        self.reject(self.transport(generation="g9"))

    def test_a_batch_the_manifest_lists_but_the_transport_omits_is_invalid(self):
        self.reject({"generation": "g1", "commits": [], "pages": []})

    def test_a_batch_the_manifest_does_not_list_is_invalid(self):
        extra = [event(authorization="AUTH-2", subject="mf-6350")]
        self.reject(self.transport(
            commits=[commit("b", [self.body]), commit("c", [extra])],
            pages=pages("b", [self.body]) + pages("c", [extra])))

    def test_a_committed_digest_the_manifest_does_not_carry_is_invalid(self):
        other = [event(revision="7")]
        self.reject(self.transport(commits=[commit("b", [other])],
                                   pages=pages("b", [other])))

    def test_a_trial_delivery_naming_no_generation_needs_no_manifest(self):
        transport = self.transport()
        del transport["generation"]
        os.environ["MARROWFLEET_POLICY_MANIFESTS"] = "/nonexistent"
        self.assertEqual(resolver.resolve(request(transport=transport)),
                         {"mf-3480": ["2042-B2", "430"]})


class Feed(unittest.TestCase):
    """The installed feed, through the resolver's command line."""

    def run_candidate(self, candidate):
        return subprocess.run([sys.executable, RESOLVER],
                              input=json.dumps(candidate["request"]),
                              text=True, capture_output=True)

    def setUp(self):
        with open(FEED, encoding="utf-8") as handle:
            self.feed = json.load(handle)
        self.candidates = {c["id"]: c for c in self.feed["candidates"]}

    def test_close_19_is_rejected_for_a_batch_its_generation_lists(self):
        run = self.run_candidate(self.candidates["close-19"])
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stdout, "")

    def test_close_18_is_rejected_for_a_conflicting_revision(self):
        run = self.run_candidate(self.candidates["close-18"])
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stdout, "")

    def test_close_17_is_the_first_admissible_candidate(self):
        run = self.run_candidate(self.candidates["close-17"])
        self.assertEqual(run.returncode, 0)
        self.assertEqual(json.loads(run.stdout),
                         {"mf-3480": None, "mf-6350": ["2042-B2", "430"]})

    def test_the_decision_covers_exactly_the_feed_subjects(self):
        run = self.run_candidate(self.candidates["close-17"])
        self.assertEqual(set(json.loads(run.stdout)), set(self.feed["subjects"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
