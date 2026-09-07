#!/usr/bin/env python3
"""Contract tests for the authorization resolver."""
import hashlib
import json
import os
import sys
import unittest

from resolve import InvalidRequest, resolve


def event(authorization, revision, subject, operation="grant",
          start="2026-01-01", end="2100-01-01", parameters=("2042-B2", "430")):
    return {
        "authorization": authorization,
        "revision": revision,
        "subject": subject,
        "operation": operation,
        "start": start,
        "end": end,
        "parameters": list(parameters),
    }


def transport(commits, pages):
    return {"commits": commits, "pages": pages}


def commit(batch, parts, events):
    raw = json.dumps(events, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return {"batch": batch, "parts": parts,
            "sha256": hashlib.sha256(raw).hexdigest()}


def page(batch, part, events):
    return {"batch": batch, "part": part, "events": events}


class ResolverTests(unittest.TestCase):
    def assert_invalid(self, request, substring):
        with self.assertRaises(InvalidRequest) as raised:
            resolve(request)
        self.assertIn(substring, str(raised.exception))

    def test_numeric_revision_and_withdrawal(self):
        request = {
            "now": "2041-06-01",
            "subjects": ["one", "two"],
            "events": [
                event("A", "2", "one", parameters=("old",)),
                event("A", "10", "one", "withdraw"),
                event("B", "2", "two"),
                event("B", "10", "two", parameters=("new",)),
            ],
        }
        self.assertEqual(resolve(request), {"one": None, "two": ["new"]})

    def test_correction_moves_authorization(self):
        request = {
            "now": "2041-06-01",
            "subjects": ["old", "new"],
            "events": [
                event("A", "1", "old"),
                event("A", "2", "new", parameters=("moved",)),
            ],
        }
        self.assertEqual(resolve(request), {"old": None, "new": ["moved"]})

    def test_equal_revision_conflict_anywhere_is_invalid(self):
        request = {
            "now": "2041-06-01", "subjects": ["one"],
            "events": [
                event("A", "1", "one", parameters=("right",)),
                event("A", "2", "one", "withdraw"),
                event("A", "1", "one", parameters=("wrong",)),
            ],
        }
        self.assert_invalid(request, "conflicting revision")

    def test_end_is_exclusive(self):
        request = {
            "now": "2041-01-01", "subjects": ["one"],
            "events": [event("A", "1", "one", end="2041-01-01")],
        }
        self.assertEqual(resolve(request), {"one": None})

    def test_multiple_active_authorizations_for_subject(self):
        request = {
            "now": "2041-06-01", "subjects": ["one"],
            "events": [event("A", "1", "one"), event("B", "1", "one")],
        }
        self.assert_invalid(request, "multiple active authorizations")

    def test_unknown_event_subject_is_excluded(self):
        request = {
            "now": "2041-06-01", "subjects": ["one"],
            "events": [event("A", "1", "other")],
        }
        self.assertEqual(resolve(request), {"one": None})

    def test_segmented_replay_and_page_order(self):
        first = event("A", "1", "one", parameters=("first",))
        second = event("B", "1", "one", parameters=("second",))
        c1 = commit("opaque-10", 2, [first])
        c2 = commit("02", 1, [second])
        # Transport order, duplicate identical page replay and page order do
        # not affect either batch assembly or history inclusion.
        request = {
            "now": "2041-06-01", "subjects": ["one"],
            "transport": transport(
                [c2, c1],
                [page("opaque-10", 1, []), page("02", 0, [second]),
                 page("opaque-10", 0, [first]), page("opaque-10", 0, [first])],
            ),
        }
        self.assert_invalid(request, "multiple active authorizations")

    def test_valid_segmented_history(self):
        e1 = event("A", "1", "one", parameters=("ok",))
        e2 = event("B", "1", "two", operation="withdraw")
        c1 = commit("b", 2, [e1])
        c2 = commit("a", 1, [e2])
        request = {
            "now": "2041-06-01", "subjects": ["one", "two", "unknown"],
            "transport": transport(
                [c2, c1],
                [page("b", 1, []), page("a", 0, [e2]), page("b", 0, [e1])],
            ),
        }
        self.assertEqual(resolve(request),
                         {"one": ["ok"], "two": None, "unknown": None})

    def test_segment_validation(self):
        e = event("A", "1", "one")
        c = commit("b", 1, [e])
        cases = (
            (transport([c], []), "missing"),
            (transport([c], [page("b", 0, [e]), page("b", 1, [])]), "extra"),
            (transport([], [page("b", 0, [e])]), "uncommitted"),
            (transport([c], [page("b", 0, [])]), "digest mismatch"),
            (transport([c, dict(c, sha256="0"*64)],
                       [page("b", 0, [e])]), "conflicting replay of commit"),
            (transport([c], [page("b", 0, [e]),
                             page("b", 0, [], )]),
             "conflicting replay of page"),
        )
        for bad, expected in cases:
            with self.subTest(bad=bad):
                self.assert_invalid(
                    {"now": "2041-06-01", "subjects": ["one"],
                     "transport": bad}, expected)

    def test_event_validation(self):
        base = event("A", "1", "one")
        cases = []
        bad = dict(base); bad["revision"] = "01"; cases.append((bad, "leading zero"))
        bad = dict(base); bad["start"] = "2041-02-30"; cases.append((bad, "valid date"))
        bad = dict(base); bad["start"] = "2041-01-01"; bad["end"] = "2041-01-01"; cases.append((bad, "strictly before"))
        bad = dict(base); bad["parameters"] = [1]; cases.append((bad, "parameters"))
        bad = dict(base); bad["extra"] = 1; cases.append((bad, "exactly"))
        for bad_event, expected in cases:
            with self.subTest(bad_event=bad_event):
                self.assert_invalid(
                    {"now": "2041-06-01", "subjects": ["one"],
                     "events": [bad_event]}, expected)


if __name__ == "__main__":
    unittest.main()
