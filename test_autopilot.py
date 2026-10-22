"""Deterministic checks; no model calls or generated code outside the existing sandbox."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import autopilot as a


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.job = Path(self.temp.name)
        (self.job / 'rounds').mkdir()
        (self.job / 'versions').mkdir()
        self.tests = [{'input': 2, 'expected': 4}]
        self.code = 'def solve(data):\n    return data * 2\n'
        self.config = {'goal': 'Double numbers and preserve all behavior', 'created': 1}
        self.plan = {'contract': 'Double one number', 'tests': deepcopy(self.tests), 'roadmap': []}
        a.store(self.job / 'config.json', self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_tests_do_not_count_as_progress(self):
        self.assertEqual(a.merge_tests(self.tests, self.tests), self.tests)

    def test_regression_answers_are_immutable(self):
        with self.assertRaises(ValueError):
            a.merge_tests(self.tests, [{'input': 2, 'expected': 99}])

    def test_bool_is_not_integer_expected(self):
        with self.assertRaises(ValueError):
            a.merge_tests([{'input': 0, 'expected': True}], [{'input': 0, 'expected': 1}])

    def test_nonfinite_json_is_rejected(self):
        with self.assertRaises(ValueError):
            a.merge_tests([], [{'input': 0, 'expected': float('nan')}])

    def test_test_budget(self):
        with self.assertRaises(ValueError):
            a.merge_tests([{'input': x, 'expected': x} for x in range(80)], [{'input': 81, 'expected': 81}])

    def test_large_test_input(self):
        with self.assertRaises(ValueError):
            a.merge_tests([], [{'input': 'x' * 20000, 'expected': 1}])

    def test_atomic_save(self):
        path = self.job / 'state.json'
        for i in range(20):
            a.store(path, {'counter': i})
            self.assertEqual(a.load(path)['counter'], i)
        self.assertEqual(list(self.job.glob('*.tmp')), [])

    def exercise_round(self, passed, recheck=True):
        progress = {'tests': deepcopy(self.tests), 'code': self.code, 'cycle': 0,
                    'stalled': 0, 'version': 'v00000000'}
        pending = {'title': 'Support negative inputs', 'tests': [{'input': -2, 'expected': -4}], 'tries': 0}
        event = {'code': self.code + '# another candidate\n', 'metrics': {'combined_score': passed / 2,
                 'passed': passed, 'total': 2, 'code_length': 80}, 'failures': []}
        class Proc:
            def __init__(self, command, **kwargs):
                (Path(kwargs['cwd']) / 'events.jsonl').write_text(json.dumps(event) + '\n')
            def wait(self, timeout=None):
                return 0
        with patch.object(a.subprocess, 'Popen', Proc), patch.object(a, 'check_code', return_value=recheck):
            a.run_round(self.job, self.config, self.plan, progress, pending, 1)
        return a.load(self.job / 'progress.json')

    def test_failed_candidate_never_replaces_stable(self):
        result = self.exercise_round(1)
        self.assertEqual(result['code'], self.code)
        self.assertEqual(result['tests'], self.tests)
        self.assertEqual(result['version'], 'v00000000')
        self.assertFalse(result['last_result']['accepted'])

    def test_independent_recheck_blocks_false_positive(self):
        result = self.exercise_round(2, recheck=False)
        self.assertEqual(result['code'], self.code)
        self.assertFalse(result['last_result']['accepted'])

    def test_passed_round_commits_code_and_tests_together(self):
        result = self.exercise_round(2)
        self.assertEqual(result['tests'][:1], self.tests)
        self.assertEqual(len(result['tests']), 2)
        self.assertTrue(result['last_result']['accepted'])
        self.assertEqual(result['code_changes'], 1)
        self.assertIsNone(result['pending'])
        self.assertTrue((self.job / 'versions/v00000001.json').exists())

    def test_retention_is_bounded(self):
        for i in range(30):
            (self.job / 'rounds' / ('round-%08d' % i)).mkdir()
            a.store(self.job / 'versions' / ('v%08d.json' % i), {'i': i})
        a.clean(self.job)
        self.assertEqual(len(list((self.job / 'rounds').iterdir())), 5)
        self.assertEqual(len(list((self.job / 'versions').iterdir())), 20)

    def test_paused_mission_does_not_plan_or_run(self):
        a.store(self.job / 'control.json', {'desired': 'paused'})
        with patch.object(a, 'ask', side_effect=AssertionError('must not call model')):
            a.worker(self.job)
        self.assertEqual(a.load(self.job / 'state.json')['status'], 'paused')

    def test_five_errors_pause_instead_of_busy_loop(self):
        a.store(self.job / 'control.json', {'desired': 'running'})
        with patch.object(a, 'model_ready', return_value=True), patch.object(a, 'initial_plan', side_effect=ValueError('model offline')), patch.object(a.time, 'sleep'):
            a.worker(self.job)
        self.assertEqual(a.load(self.job / 'control.json')['desired'], 'paused')
        self.assertEqual(a.load(self.job / 'state.json')['errors'], 5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
