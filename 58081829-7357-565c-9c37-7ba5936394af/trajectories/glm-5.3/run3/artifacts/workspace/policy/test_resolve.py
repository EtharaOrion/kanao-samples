#!/usr/bin/env python3
"""Contract tests for the signed authorization resolver."""
import hashlib
import json
import unittest

from resolve import PolicyError, resolve


def event(authorization="A", revision="1", subject="a", operation="grant",
          start="2029-01-01", end="2031-01-01", parameters=("x",)):
    return {
        "authorization": authorization, "revision": revision,
        "subject": subject, "operation": operation, "start": start,
        "end": end, "parameters": list(parameters),
    }


def request(events, now="2030-01-01", subjects=("a", "b")):
    return {"now": now, "subjects": list(subjects), "events": list(events)}


def transport(pages, commits=None):
    if commits is None:
        encoded = json.dumps(pages[0]["events"], sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        commits = [{"batch": "1", "parts": 1, "sha256": digest}]
    return {"now": "2030-01-01", "subjects": ["a", "b"],
            "transport": {"commits": commits, "pages": pages}}


class ResolverTest(unittest.TestCase):
    def test_active_grant_and_exclusive_end(self):
        self.assertEqual(resolve(request([event()])), {"a": ["x"], "b": None})
        self.assertEqual(resolve(request([event()], now="2031-01-01")),
                         {"a": None, "b": None})

    def test_numeric_replacement_moves_subject(self):
        moved = request([event(), event(revision="2", subject="b",
                                        parameters=("moved",))])
        self.assertEqual(resolve(moved), {"a": None, "b": ["moved"]})

    def test_conflicting_equal_revision_rejected(self):
        with self.assertRaises(PolicyError):
            resolve(request([event(), event(subject="b")]))

    def test_multiple_active_authorizations_rejected(self):
        with self.assertRaises(PolicyError):
            resolve(request([event(), event(authorization="B", subject="a")]))

    def test_segmented_replay_is_idempotent(self):
        page = {"batch": "1", "part": 0, "events": [event()]}
        first = transport([page])
        replay = {**first, "transport": {
            "commits": first["transport"]["commits"] * 2,
            "pages": first["transport"]["pages"] * 2,
        }}
        self.assertEqual(resolve(replay), {"a": ["x"], "b": None})

    def assert_rejected(self, value):
        with self.assertRaises(PolicyError):
            resolve(value)

    def test_transport_faults_rejected(self):
        self.assert_rejected(transport(
            [{"batch": "1", "part": 0, "events": [event()]}],
            [{"batch": "1", "parts": 1, "sha256": "0" * 64}]))
        self.assert_rejected(transport(
            [{"batch": "1", "part": 1, "events": [event()]}],
            [{"batch": "1", "parts": 2, "sha256": "0" * 64}]))
        self.assert_rejected({**transport([{"batch": "2", "part": 0,
                                             "events": [event()]}])})
        conflicting = {"now": "2030-01-01", "subjects": ["a"],
                       "transport": {
                           "commits": [{"batch": "1", "parts": 1,
                                          "sha256": "0" * 64}],
                           "pages": [
                               {"batch": "1", "part": 0, "events": [event()]},
                               {"batch": "1", "part": 0,
                                "events": [event(subject="b")]},
                           ]}}
        self.assert_rejected(conflicting)


if __name__ == "__main__":
    unittest.main()
