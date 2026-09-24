#!/usr/bin/env python3
"""Checks policy/resolve.py against the reconciliation contract.

Run with:  python3 tools/policycheck/test_resolve.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'policy'))

import resolve as resolver  # noqa: E402

RESOLVE = os.path.join(ROOT, 'policy', 'resolve.py')
FEED = os.path.join(ROOT, 'policy', 'authorizations.json')
MANIFESTS = os.path.join(ROOT, 'policy', 'manifests.json')


def event(authorization='AUTH-1', revision='1', subject='mf-3480',
          operation='grant', start='2026-01-01', end='2100-01-01',
          parameters=('2041-B5', '300')):
    return {'authorization': authorization, 'revision': revision,
            'subject': subject, 'operation': operation, 'start': start,
            'end': end, 'parameters': list(parameters)}


def digest(events):
    return hashlib.sha256(json.dumps(
        events, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False).encode('utf-8')).hexdigest()


def request(events, subjects=('mf-3480',), now='2026-09-05'):
    return {'now': now, 'subjects': list(subjects), 'events': events}


def delivery(batches, subjects=('mf-3480',), now='2026-09-05',
             generation=None):
    """batches: {batch: [events of part 0, events of part 1, ...]}."""
    commits = []
    pages = []
    for batch, parts in batches.items():
        flat = [e for part in parts for e in part]
        commits.append({'batch': batch, 'parts': len(parts),
                        'sha256': digest(flat)})
        for number, part in enumerate(parts):
            pages.append({'batch': batch, 'part': number, 'events': part})
    transport = {'commits': commits, 'pages': pages}
    if generation is not None:
        transport['generation'] = generation
    return {'now': now, 'subjects': list(subjects), 'transport': transport}


class ResolverContract(unittest.TestCase):

    def reject(self, payload):
        with self.assertRaises(resolver.PolicyError):
            resolver.resolve(payload)

    # --- revisions ---------------------------------------------------
    def test_highest_revision_wins(self):
        self.assertEqual(
            resolver.resolve(request([
                event(revision='2', parameters=('2041-B5', '300')),
                event(revision='10', parameters=('2042-B2', '430'))])),
            {'mf-3480': ['2042-B2', '430']})

    def test_revisions_compare_numerically_not_as_text(self):
        self.assertEqual(
            resolver.resolve(request([
                event(revision='9', parameters=('9', '9')),
                event(revision='10', parameters=('10', '10'))])),
            {'mf-3480': ['10', '10']})

    def test_identical_repeated_revision_is_idempotent(self):
        self.assertEqual(resolver.resolve(request([event(), event()])),
                         {'mf-3480': ['2041-B5', '300']})

    def test_conflicting_equal_revisions_are_invalid(self):
        self.reject(request([event(parameters=('a',)),
                             event(parameters=('b',))]))

    def test_conflicting_equal_revisions_are_invalid_when_superseded(self):
        self.reject(request([event(revision='2', parameters=('a',)),
                             event(revision='9'),
                             event(revision='2', parameters=('b',))]))

    def test_winning_withdrawal_leaves_no_decision(self):
        self.assertIsNone(resolver.resolve(request([
            event(revision='2'),
            event(revision='3', operation='withdraw')]))['mf-3480'])

    def test_withdrawn_then_regranted_applies(self):
        self.assertEqual(
            resolver.resolve(request([
                event(revision='3', operation='withdraw'),
                event(revision='4', parameters=('2042-B2', '430'))])),
            {'mf-3480': ['2042-B2', '430']})

    def test_correction_moves_subject_and_leaves_none_behind(self):
        self.assertEqual(
            resolver.resolve(request([
                event(revision='1', subject='mf-3480'),
                event(revision='2', subject='mf-6350')],
                subjects=('mf-3480', 'mf-6350'))),
            {'mf-3480': None, 'mf-6350': ['2041-B5', '300']})

    def test_correction_out_of_window_leaves_none_behind(self):
        self.assertEqual(
            resolver.resolve(request([
                event(revision='1'),
                event(revision='2', start='2030-01-01', end='2031-01-01')])),
            {'mf-3480': None})

    # --- windows and subjects ----------------------------------------
    def test_grant_is_half_open(self):
        self.assertIsNone(resolver.resolve(request(
            [event(start='2020-01-01', end='2026-09-05')]))['mf-3480'])
        self.assertEqual(resolver.resolve(request(
            [event(start='2026-09-05', end='2026-09-06')]))['mf-3480'],
            ['2041-B5', '300'])

    def test_unknown_subjects_are_excluded(self):
        self.assertEqual(
            resolver.resolve(request([event(subject='mf-9999')])),
            {'mf-3480': None})

    def test_two_active_authorizations_for_one_subject_are_invalid(self):
        self.reject(request([event(authorization='AUTH-1'),
                             event(authorization='AUTH-2')]))

    def test_two_active_authorizations_off_projection_are_ignored(self):
        self.assertEqual(
            resolver.resolve(request([
                event(authorization='AUTH-1', subject='mf-9999'),
                event(authorization='AUTH-2', subject='mf-9999')])),
            {'mf-3480': None})

    def test_every_requested_subject_is_a_key(self):
        self.assertEqual(
            sorted(resolver.resolve(request([], subjects=('a', 'b')))),
            ['a', 'b'])

    # --- event validation --------------------------------------------
    def test_superseded_and_withdrawn_events_are_validated(self):
        self.reject(request([event(revision='1', start='2021-01-01',
                                   end='2020-01-01'),
                             event(revision='2')]))
        self.reject(request([event(revision='1', operation='withdraw',
                                   subject=''),
                             event(revision='2')]))

    def test_unrequested_subject_events_are_validated(self):
        self.reject(request([event(subject='mf-9999', revision='01')]))

    def test_event_key_set_is_exact(self):
        extra = event()
        extra['note'] = 'x'
        self.reject(request([extra]))
        missing = event()
        del missing['parameters']
        self.reject(request([missing]))

    def test_malformed_fields_are_invalid(self):
        parameters_not_a_list = dict(event(), parameters='2041-B5')
        parameters_not_strings = dict(event(), parameters=[300])
        for bad in (event(revision='007'), event(revision='-1'),
                    event(revision=7), event(operation='amend'),
                    parameters_not_a_list, parameters_not_strings,
                    event(start='2026-13-01'), event(end='not-a-date'),
                    event(start='2026-01-01', end='2026-01-01'),
                    event(authorization='')):
            self.reject(request([bad]))

    def test_request_shape_is_validated(self):
        self.reject({'now': '2026-09-05', 'subjects': ['mf-3480']})
        self.reject({'subjects': ['mf-3480'], 'events': []})
        self.reject({'now': 'today', 'subjects': ['mf-3480'], 'events': []})
        self.reject({'now': '2026-09-05', 'subjects': 'mf-3480', 'events': []})

    # --- transport ----------------------------------------------------
    def test_parts_concatenate_in_numeric_order(self):
        self.assertEqual(
            resolver.resolve(delivery({'2': [
                [event(revision='1')],
                [event(revision='2', parameters=('x',))]]})),
            {'mf-3480': ['x']})

    def test_all_committed_batches_belong_to_the_history(self):
        self.assertEqual(
            resolver.resolve(delivery({
                '2': [[event(revision='2')]],
                '10': [[event(revision='10', operation='withdraw')]]})),
            {'mf-3480': None})

    def test_identical_page_replay_is_idempotent(self):
        payload = delivery({'2': [[event()]]})
        payload['transport']['pages'].append(
            dict(payload['transport']['pages'][0]))
        payload['transport']['commits'].append(
            dict(payload['transport']['commits'][0]))
        self.assertEqual(resolver.resolve(payload),
                         {'mf-3480': ['2041-B5', '300']})

    def test_conflicting_page_replay_is_invalid(self):
        payload = delivery({'2': [[event()]]})
        payload['transport']['pages'].append(
            {'batch': '2', 'part': 0, 'events': [event(parameters=('z',))]})
        self.reject(payload)

    def test_missing_extra_and_uncommitted_parts_are_invalid(self):
        short = delivery({'2': [[event()], [event(revision='2')]]})
        short['transport']['pages'] = [page for page
                                       in short['transport']['pages']
                                       if page['part'] == 0]
        self.reject(short)

        extra = delivery({'2': [[event()]]})
        extra['transport']['pages'].append(
            {'batch': '2', 'part': 1, 'events': []})
        self.reject(extra)

        uncommitted = delivery({'2': [[event()]]})
        uncommitted['transport']['pages'].append(
            {'batch': '7', 'part': 0, 'events': [event(revision='99')]})
        self.reject(uncommitted)

    def test_digest_mismatch_is_invalid(self):
        payload = delivery({'2': [[event()]]})
        payload['transport']['commits'][0]['sha256'] = '00' * 32
        self.reject(payload)

    def test_request_carrying_both_transport_and_events_is_invalid(self):
        payload = delivery({'2': [[event()]]})
        payload['events'] = []
        self.reject(payload)

    # --- manifests -----------------------------------------------------
    def manifest_file(self, generations):
        handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
        json.dump({'generations': generations}, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def with_manifests(self, path, payload):
        previous = os.environ.get('MARROWFLEET_POLICY_MANIFESTS')
        os.environ['MARROWFLEET_POLICY_MANIFESTS'] = path
        try:
            return resolver.resolve(payload)
        finally:
            if previous is None:
                del os.environ['MARROWFLEET_POLICY_MANIFESTS']
            else:
                os.environ['MARROWFLEET_POLICY_MANIFESTS'] = previous

    def test_generation_must_commit_exactly_its_manifest(self):
        payload = delivery({'2': [[event()]]}, generation='g1')
        listed = {commit['batch']: commit['sha256']
                  for commit in payload['transport']['commits']}

        good = self.manifest_file({'g1': {'batches': listed}})
        self.assertEqual(self.with_manifests(good, payload),
                         {'mf-3480': ['2041-B5', '300']})

        uncommitted = self.manifest_file(
            {'g1': {'batches': dict(listed, **{'9': '00' * 32})}})
        with self.assertRaises(resolver.PolicyError):
            self.with_manifests(uncommitted, payload)

        wrong = self.manifest_file({'g1': {'batches': {'2': '00' * 32}}})
        with self.assertRaises(resolver.PolicyError):
            self.with_manifests(wrong, payload)

        unnamed = self.manifest_file({'g2': {'batches': listed}})
        with self.assertRaises(resolver.PolicyError):
            self.with_manifests(unnamed, payload)

    def test_trial_delivery_needs_no_manifest(self):
        self.assertEqual(resolver.resolve(delivery({'2': [[event()]]})),
                         {'mf-3480': ['2041-B5', '300']})

    def test_default_manifests_are_the_bureau_file(self):
        self.assertEqual(resolver.DEFAULT_MANIFESTS, MANIFESTS)

    # --- the bureau's own feed -------------------------------------------
    def candidate(self, identifier):
        with open(FEED, encoding='utf-8') as handle:
            feed = json.load(handle)
        for candidate in feed['candidates']:
            if candidate['id'] == identifier:
                return candidate['request']
        raise AssertionError('no candidate ' + identifier)

    def run_cli(self, payload):
        return subprocess.run([sys.executable, RESOLVE],
                              input=json.dumps(payload), text=True,
                              capture_output=True)

    def test_feed_candidate_close_19_is_rejected(self):
        # g19's manifest lists batch 10; the transport never commits it.
        self.assertEqual(self.run_cli(self.candidate('close-19')).returncode, 1)

    def test_feed_candidate_close_18_is_rejected(self):
        # batch 30 replays AUTH-1 revision 2 with different parameters.
        self.assertEqual(self.run_cli(self.candidate('close-18')).returncode, 1)

    def test_feed_candidate_close_17_is_the_approved_state(self):
        run = self.run_cli(self.candidate('close-17'))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout),
                         {'mf-3480': None, 'mf-6350': ['2042-B2', '430']})

    def test_selection_is_stable_across_reruns(self):
        first = self.run_cli(self.candidate('close-17')).stdout
        second = self.run_cli(self.candidate('close-17')).stdout
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main(verbosity=2)
