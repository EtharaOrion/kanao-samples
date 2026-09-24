#!/usr/bin/env python3
"""Checks for policy/resolve.py - the adapter the release reconciler drives.

Run: python3 tools/policycheck/test_resolve.py
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESOLVE = os.path.join(ROOT, 'policy', 'resolve.py')
FEED = os.path.join(ROOT, 'policy', 'authorizations.json')

_spec = importlib.util.spec_from_file_location('mf_resolve', RESOLVE)
resolve_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolve_mod)
resolve = resolve_mod.resolve
PolicyError = resolve_mod.PolicyError


def event(authorization='AUTH-1', revision='1', subject='mf-1', operation='grant',
          start='2026-01-01', end='2100-01-01', parameters=('2042-B2', '430')):
    return {'authorization': authorization, 'revision': revision,
            'subject': subject, 'operation': operation, 'start': start,
            'end': end, 'parameters': list(parameters)}


def digest(events):
    return hashlib.sha256(json.dumps(events, sort_keys=True,
                                     separators=(',', ':'),
                                     ensure_ascii=False)
                          .encode('utf-8')).hexdigest()


def transport(batches, generation=None):
    """batches: {batch: [part_events, ...]}."""
    commits, pages = [], []
    for batch, parts in batches.items():
        whole = [e for part in parts for e in part]
        commits.append({'batch': batch, 'parts': len(parts),
                        'sha256': digest(whole)})
        for number, part in enumerate(parts):
            pages.append({'batch': batch, 'part': number, 'events': part})
    out = {'commits': commits, 'pages': pages}
    if generation is not None:
        out['generation'] = generation
    return out


def request(subjects, events=None, transport_=None, now='2026-09-05'):
    out = {'now': now, 'subjects': list(subjects)}
    if events is not None:
        out['events'] = events
    if transport_ is not None:
        out['transport'] = transport_
    return out


class ShippedFeed(unittest.TestCase):
    """The candidates the release controller actually walks."""

    def setUp(self):
        with open(FEED, encoding='utf-8') as handle:
            self.feed = json.load(handle)
        self.candidates = {c['id']: c['request'] for c in self.feed['candidates']}

    def test_generation_missing_a_manifest_batch_is_rejected(self):
        with self.assertRaises(PolicyError):
            resolve(self.candidates['close-19'])

    def test_conflicting_equal_revision_is_rejected(self):
        with self.assertRaises(PolicyError):
            resolve(self.candidates['close-18'])

    def test_selected_candidate_matches_the_signed_limitations(self):
        self.assertEqual(resolve(self.candidates['close-17']),
                         {'mf-3480': None, 'mf-6350': ['2042-B2', '430']})

    def test_candidate_order_selects_close_17(self):
        selected = None
        for candidate in self.feed['candidates']:
            try:
                decision = resolve(candidate['request'])
            except PolicyError:
                continue
            selected = candidate['id']
            break
        self.assertEqual(selected, 'close-17')
        self.assertEqual(decision,
                         {'mf-3480': None, 'mf-6350': ['2042-B2', '430']})

    def test_replay_is_stable(self):
        first = resolve(self.candidates['close-17'])
        self.assertEqual(first, resolve(self.candidates['close-17']))


class Projection(unittest.TestCase):
    def test_events_only_request(self):
        req = request(['mf-1'], events=[event()])
        self.assertEqual(resolve(req), {'mf-1': ['2042-B2', '430']})

    def test_unapproved_subject_is_null(self):
        req = request(['mf-1', 'mf-2'], events=[event()])
        self.assertEqual(resolve(req), {'mf-1': ['2042-B2', '430'], 'mf-2': None})

    def test_revisions_order_numerically_not_textually(self):
        req = request(['mf-1'], events=[
            event(revision='9', parameters=['2041-B5', '300']),
            event(revision='10', parameters=['2042-B2', '430'])])
        self.assertEqual(resolve(req), {'mf-1': ['2042-B2', '430']})

    def test_winning_withdrawal_contributes_nothing(self):
        req = request(['mf-1'], events=[
            event(revision='2'),
            event(revision='10', operation='withdraw')])
        self.assertEqual(resolve(req), {'mf-1': None})

    def test_withdrawal_that_loses_does_not_apply(self):
        req = request(['mf-1'], events=[
            event(revision='2', operation='withdraw'),
            event(revision='3')])
        self.assertEqual(resolve(req), {'mf-1': ['2042-B2', '430']})

    def test_correction_moves_subject_and_the_older_revision_dies(self):
        req = request(['mf-1', 'mf-2'], events=[
            event(revision='1', subject='mf-1'),
            event(revision='2', subject='mf-2')])
        self.assertEqual(resolve(req), {'mf-1': None, 'mf-2': ['2042-B2', '430']})

    def test_correction_outside_the_window_leaves_no_decision(self):
        req = request(['mf-1'], events=[
            event(revision='1'),
            event(revision='2', start='2027-01-01', end='2028-01-01')])
        self.assertEqual(resolve(req), {'mf-1': None})

    def test_window_is_half_open(self):
        self.assertEqual(
            resolve(request(['mf-1'], events=[
                event(start='2026-09-05', end='2026-09-06')])),
            {'mf-1': ['2042-B2', '430']})
        self.assertEqual(
            resolve(request(['mf-1'], events=[
                event(start='2026-09-04', end='2026-09-05')])),
            {'mf-1': None})

    def test_two_active_authorizations_for_one_subject_are_an_error(self):
        req = request(['mf-1'], events=[
            event(authorization='AUTH-1'), event(authorization='AUTH-2')])
        with self.assertRaises(PolicyError):
            resolve(req)

    def test_unrequested_subjects_are_excluded_after_resolving_revisions(self):
        req = request(['mf-1'], events=[
            event(authorization='AUTH-1'),
            event(authorization='AUTH-2', subject='mf-9'),
            event(authorization='AUTH-3', subject='mf-9')])
        self.assertEqual(resolve(req), {'mf-1': ['2042-B2', '430']})

    def test_identical_equal_revisions_are_idempotent(self):
        req = request(['mf-1'], events=[event(revision='4'), event(revision='4')])
        self.assertEqual(resolve(req), {'mf-1': ['2042-B2', '430']})

    def test_conflicting_equal_revisions_are_invalid(self):
        req = request(['mf-1'], events=[
            event(revision='4'), event(revision='4', parameters=['x'])])
        with self.assertRaises(PolicyError):
            resolve(req)

    def test_host_clock_is_irrelevant(self):
        req = request(['mf-1'], events=[
            event(start='2020-01-01', end='2021-01-01')], now='2020-06-01')
        self.assertEqual(resolve(req), {'mf-1': ['2042-B2', '430']})


class EventValidation(unittest.TestCase):
    def bad(self, *events):
        with self.assertRaises(PolicyError):
            resolve(request(['mf-1'], events=list(events)))

    def test_superseded_records_are_validated(self):
        self.bad(event(revision='1', operation='transfer'), event(revision='2'))

    def test_out_of_window_records_are_validated(self):
        self.bad(event(revision='1', start='2027-01-01', end='2026-01-01'))

    def test_unrequested_subject_records_are_validated(self):
        self.bad(event(subject='mf-9', revision='01'))

    def test_withdrawn_records_are_validated(self):
        self.bad(event(operation='withdraw', parameters=[1]))

    def test_extra_key(self):
        record = event()
        record['note'] = 'x'
        self.bad(record)

    def test_missing_key(self):
        record = event()
        del record['end']
        self.bad(record)

    def test_revision_must_be_a_canonical_string(self):
        self.bad(event(revision=3))
        self.bad(event(revision='007'))
        self.bad(event(revision='-1'))
        self.bad(event(revision=''))

    def test_identifiers_must_be_nonempty(self):
        self.bad(event(subject=''))
        self.bad(event(authorization=''))

    def test_dates(self):
        self.bad(event(start='2026-1-01'))
        self.bad(event(end='2026-13-01'))
        self.bad(event(start='2026-02-30', end='2027-01-01'))
        self.bad(event(start='2026-01-01', end='2026-01-01'))

    def test_parameters_must_be_a_list_of_strings(self):
        record = event()
        record['parameters'] = '2042-B2'
        self.bad(record)
        self.bad(event(parameters=[430]))

    def test_request_shape(self):
        with self.assertRaises(PolicyError):
            resolve({'subjects': ['mf-1'], 'events': []})
        with self.assertRaises(PolicyError):
            resolve({'now': '2026-09-05', 'events': []})
        with self.assertRaises(PolicyError):
            resolve(request(['mf-1']))


class Transport(unittest.TestCase):
    def test_parts_concatenate_in_part_order(self):
        req = request(['mf-1'], transport_=transport({'b': [
            [event(revision='1')], [event(revision='2', parameters=['p'])]]}))
        self.assertEqual(resolve(req), {'mf-1': ['p']})

    def test_batches_combine_regardless_of_transport_order(self):
        early = [event(revision='1')]
        late = [event(revision='5', operation='withdraw')]
        req = request(['mf-1'], transport_=transport({'9': [late], '1': [early]}))
        self.assertEqual(resolve(req), {'mf-1': None})

    def test_identical_page_and_commit_replay_is_idempotent(self):
        base = transport({'b': [[event()]]})
        base['pages'].append(dict(base['pages'][0]))
        base['commits'].append(dict(base['commits'][0]))
        self.assertEqual(resolve(request(['mf-1'], transport_=base)),
                         {'mf-1': ['2042-B2', '430']})

    def test_conflicting_page_replay_is_invalid(self):
        base = transport({'b': [[event()]]})
        base['pages'].append({'batch': 'b', 'part': 0,
                              'events': [event(parameters=['x'])]})
        with self.assertRaises(PolicyError):
            resolve(request(['mf-1'], transport_=base))

    def test_conflicting_commit_replay_is_invalid(self):
        base = transport({'b': [[event()]]})
        base['commits'].append({'batch': 'b', 'parts': 2,
                                'sha256': base['commits'][0]['sha256']})
        with self.assertRaises(PolicyError):
            resolve(request(['mf-1'], transport_=base))

    def test_missing_part_is_invalid(self):
        base = transport({'b': [[event(revision='1')], [event(revision='2')]]})
        base['pages'] = [p for p in base['pages'] if p['part'] != 1]
        with self.assertRaises(PolicyError):
            resolve(request(['mf-1'], transport_=base))

    def test_extra_part_is_invalid(self):
        base = transport({'b': [[event()]]})
        base['pages'].append({'batch': 'b', 'part': 1, 'events': []})
        with self.assertRaises(PolicyError):
            resolve(request(['mf-1'], transport_=base))

    def test_uncommitted_page_is_invalid(self):
        base = transport({'b': [[event()]]})
        base['pages'].append({'batch': 'c', 'part': 0, 'events': []})
        with self.assertRaises(PolicyError):
            resolve(request(['mf-1'], transport_=base))

    def test_digest_mismatch_is_invalid(self):
        base = transport({'b': [[event()]]})
        base['commits'][0]['sha256'] = digest([event(parameters=['x'])])
        with self.assertRaises(PolicyError):
            resolve(request(['mf-1'], transport_=base))

    def test_transport_and_events_together_are_invalid(self):
        req = request(['mf-1'], events=[event()],
                      transport_=transport({'b': [[event()]]}))
        with self.assertRaises(PolicyError):
            resolve(req)


class Manifests(unittest.TestCase):
    def manifests(self, generations):
        handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
        json.dump({'generations': generations}, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        self.addCleanup(os.environ.pop, resolve_mod.MANIFESTS_ENV, None)
        os.environ[resolve_mod.MANIFESTS_ENV] = handle.name

    def test_generation_committing_exactly_its_manifest_is_accepted(self):
        events = [event()]
        self.manifests({'g1': {'batches': {'b': digest(events)}}})
        req = request(['mf-1'],
                      transport_=transport({'b': [events]}, generation='g1'))
        self.assertEqual(resolve(req), {'mf-1': ['2042-B2', '430']})

    def test_unnamed_generation_is_invalid(self):
        self.manifests({})
        req = request(['mf-1'],
                      transport_=transport({'b': [[event()]]}, generation='g1'))
        with self.assertRaises(PolicyError):
            resolve(req)

    def test_batch_the_manifest_lists_but_the_transport_omits(self):
        events = [event()]
        self.manifests({'g1': {'batches': {'b': digest(events),
                                           'c': digest([])}}})
        req = request(['mf-1'],
                      transport_=transport({'b': [events]}, generation='g1'))
        with self.assertRaises(PolicyError):
            resolve(req)

    def test_batch_the_transport_commits_but_the_manifest_omits(self):
        events = [event()]
        self.manifests({'g1': {'batches': {'b': digest(events)}}})
        req = request(['mf-1'], transport_=transport(
            {'b': [events], 'c': [[]]}, generation='g1'))
        with self.assertRaises(PolicyError):
            resolve(req)

    def test_committed_digest_differing_from_the_manifest(self):
        events = [event()]
        self.manifests({'g1': {'batches': {'b': digest([])}}})
        req = request(['mf-1'],
                      transport_=transport({'b': [events]}, generation='g1'))
        with self.assertRaises(PolicyError):
            resolve(req)

    def test_trial_delivery_without_a_generation_needs_no_manifest(self):
        self.manifests({})
        req = request(['mf-1'], transport_=transport({'b': [[event()]]}))
        self.assertEqual(resolve(req), {'mf-1': ['2042-B2', '430']})

    def test_released_generations_check_against_the_repository_manifests(self):
        os.environ.pop(resolve_mod.MANIFESTS_ENV, None)
        with open(FEED, encoding='utf-8') as handle:
            feed = json.load(handle)
        candidate = {c['id']: c for c in feed['candidates']}['close-17']
        self.assertEqual(resolve(candidate['request']),
                         {'mf-3480': None, 'mf-6350': ['2042-B2', '430']})


class CommandLine(unittest.TestCase):
    def run_cli(self, req):
        return subprocess.run([sys.executable, RESOLVE], input=json.dumps(req),
                              text=True, capture_output=True)

    def test_valid_request_prints_the_projection(self):
        done = self.run_cli(request(['mf-1'], events=[event()]))
        self.assertEqual(done.returncode, 0)
        self.assertEqual(json.loads(done.stdout), {'mf-1': ['2042-B2', '430']})

    def test_invalid_history_exits_nonzero_with_no_partial_result(self):
        done = self.run_cli(request(['mf-1'], events=[event(revision='x')]))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, '')

    def test_malformed_json_exits_nonzero(self):
        done = subprocess.run([sys.executable, RESOLVE], input='{',
                              text=True, capture_output=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
