import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import autopilot as a
import chat


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.job = Path(self.tmp.name)
        a.store(self.job / 'config.json', {'goal': 'Develop a small deterministic JSON project'})
        self.guidance = {'revision': 2, 'text': 'Add a new feature'}

    def tearDown(self):
        self.tmp.cleanup()

    def test_priority_is_pending_until_worker_reads_it(self):
        self.assertEqual(a.guidance_status({}, self.guidance), 'queued')
        self.assertEqual(a.guidance_status({'guidance_revision': 2}, self.guidance), 'active')

    def test_accepted_priority_is_not_repeated_forever(self):
        progress = {'code': 'stable', 'guidance_revision': 2}
        a.finish_priority(self.job, progress, {'guidance_revision': 2, 'tries': 1}, True)
        self.assertEqual(a.guidance_status(progress, self.guidance), 'used')
        self.assertEqual(progress['code'], 'stable')
        self.assertEqual(a.guidance_status(progress, {'revision': 3, 'text': 'Next'}), 'queued')

    def test_failed_priority_is_explicitly_deferred_not_completed(self):
        progress = {'guidance_revision': 2}
        a.finish_priority(self.job, progress, {'guidance_revision': 2, 'tries': 2}, False)
        self.assertEqual(a.guidance_status(progress, self.guidance), 'active')
        a.finish_priority(self.job, progress, {'guidance_revision': 2, 'tries': 3}, False)
        self.assertEqual(a.guidance_status(progress, self.guidance), 'deferred')
        self.assertNotIn('guidance_consumed_revision', progress)

    def test_clear_does_not_replay_old_priority(self):
        self.assertEqual(a.guidance_status({'guidance_revision': 1}, {'revision': 2, 'text': ''}), 'none')

    def test_ordinary_round_does_not_consume_new_directive(self):
        progress = {}
        a.finish_priority(self.job, progress, {'tries': 1}, True)
        self.assertEqual(progress, {})

    def test_model_unavailable_preserves_project_and_accepts_pause(self):
        a.store(self.job / 'control.json', {'desired': 'running'})
        def pause(_): a.store(self.job / 'control.json', {'desired': 'paused'})
        with patch.object(a, 'model_ready', return_value=False), patch.object(a.time, 'sleep', side_effect=pause), patch.object(a, 'ask') as ask:
            a.worker(self.job)
        ask.assert_not_called()
        self.assertEqual(a.load(self.job / 'state.json')['status'], 'paused')
        self.assertFalse((self.job / 'plan.json').exists())

    def test_new_operation_can_have_a_distinct_result_shape(self):
        old=[{'input': {'op':'stats','numbers':[1]}, 'expected': {'count':1}}]
        a.validate_extension(old,[{'input': {'op':'sort','numbers':[2,1]}, 'expected': {'sorted':[1,2]}}])

    def test_existing_operation_keeps_its_own_required_fields(self):
        old=[{'input': {'op':'stats','numbers':[1]}, 'expected': {'count':1}},
             {'input': {'op':'sort','numbers':[1]}, 'expected': {'sorted':[1]}}]
        with self.assertRaises(ValueError):
            a.validate_extension(old,[{'input': {'op':'stats','numbers':[1]}, 'expected': {'sorted':[1]}}])

    def test_operation_discriminator_cannot_disappear(self):
        with self.assertRaises(ValueError):
            a.validate_extension([{'input': {'op':'stats'}, 'expected': {}}],
                                 [{'input': {}, 'expected': {}}])

    def test_literal_requested_operation_cannot_be_silently_replaced(self):
        with self.assertRaisesRegex(ValueError, 'op=range'):
            a.validate_directive_target('Add {op:"range",numbers:[]}', [{'input':{'op':'unique'},'expected':{}}])
        a.validate_directive_target('Add {"op":"range"}', [{'input':{'op':'range'},'expected':{}}])
        a.validate_directive_target('Improve grid boundaries', [])

    def test_user_directive_overrides_roadmap_focus(self):
        directive='Add {op:"range",numbers:[]}'
        progress={'code':'def solve(d): return {}','tests':[{'input':{'op':'stats','numbers':[1]},'expected':{'count':1}}]}
        plan={'contract':'Object API','roadmap':['unique','median']}
        def ask(system, context):
            self.assertEqual(context['selected_milestone'],directive)
            self.assertEqual(context['roadmap'],[])
            self.assertIn('OVERRIDES THE ROADMAP',system)
            return {'title':'range','reason':'User request','tests':[{'input':{'op':'range','numbers':[1,2]},'expected':{'range':1}}]}
        with patch.object(a,'ask',side_effect=ask):
            a.propose(self.job,{'goal':'Statistics','guidance':directive},plan,progress)

    def test_full_test_bank_does_not_falsely_complete_user_directive(self):
        progress={'tests':[{'input':{'x':x},'expected':{'x':x}} for x in range(74)]}
        with self.assertRaisesRegex(ValueError, 'новое поручение не выполнено'):
            a.propose(self.job, {'guidance':'Add a feature'}, {}, progress)
        self.assertEqual(a.propose(self.job, {}, {}, progress)['tests'], [])

    def test_model_health_fails_closed(self):
        with patch.object(a.urllib.request, 'urlopen', side_effect=OSError('offline')):
            self.assertFalse(a.model_ready())

    def test_events_read_handles_retention_race_and_partial_line(self):
        self.assertEqual(a.read_events(self.job / 'missing'), [])
        path = self.job / 'events.jsonl'
        path.write_text('{"ok":true}\n{"unfinished":')
        self.assertEqual(a.read_events(path), [{'ok': True}])

    def test_roadmap_rotates_instead_of_repeating_one_topic(self):
        plan = {'contract': 'One object', 'roadmap': ['Move', 'Walls', 'Items']}
        progress = {'code': 'def solve(d): return d', 'tests': [{'input': {'x': 1}, 'expected': {'x': 1}}], 'completed': []}
        focuses = []
        def ask(_, context):
            focuses.append(context['selected_milestone'])
            return {'title': 'Step', 'reason': 'A new behavior', 'tests': [{'input': {'x': 2}, 'expected': {'x': 2}}]}
        with patch.object(a, 'ask', side_effect=ask):
            for _ in range(4): a.propose(self.job, {'goal': 'Example'}, plan, progress)
        self.assertEqual(focuses, ['Move', 'Walls', 'Items', 'Move'])

    def test_exited_worker_descendants_are_still_terminated(self):
        os.environ.setdefault('RCP_PASSWORD', 'test-only-not-a-real-password')
        import app
        proc = MagicMock(pid=123456)
        proc.poll.return_value = 0
        with patch.object(app.os, 'killpg') as kill:
            app.stop_process(proc)
        self.assertEqual([c.args for c in kill.call_args_list], [(123456, signal.SIGTERM), (123456, signal.SIGKILL)])


class TryConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.job = Path(self.tmp.name)
        self.snapshot = {'stable_version': 'v00000001', 'stable_code': 'def solve(data):\n    return {"sum": sum(data["numbers"])}\n',
                         'contract': 'Input numbers array; output sum', 'test_examples': [{'input': {'numbers': [1]}, 'expected': {'sum': 1}}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_directive_acknowledgement_cannot_invent_a_completed_change(self):
        with patch.object(chat.urllib.request, 'urlopen') as model:
            reply = chat.answer(self.job, {'mode':'direct','text':'Add a feature'}, {'control':{'desired':'paused'}})
        model.assert_not_called()
        self.assertIn('не означает', reply)
        self.assertIn('на паузе', reply)

    def test_try_mode_is_accepted(self):
        self.assertEqual(chat.MessageRequest(text='Try something', mode='try').mode, 'try')

    def test_exact_json_is_not_modified_by_model(self):
        with patch.object(chat.urllib.request, 'urlopen') as model:
            reply, execution = chat.run_example(self.job, {'text': '{"numbers":[3,7,-2]}'}, self.snapshot)
        model.assert_not_called()
        self.assertEqual(execution['input'], {'numbers': [3, 7, -2]})
        self.assertEqual(execution['result'], {'ok': True, 'value': {'sum': 8}})
        self.assertFalse(execution['input_interpreted_by_model'])
        self.assertEqual(execution['version'], 'v00000001')
        self.assertFalse((self.job / 'progress.json').exists())
        self.assertIn('Ни код, ни план', reply)

    def test_natural_language_input_is_explicitly_labelled(self):
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"input":{"numbers":[4,8]}}'}}]}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps(response).encode()
        with patch.object(chat.urllib.request, 'urlopen', return_value=Response()):
            _, execution = chat.run_example(self.job, {'text': 'Сложи четыре и восемь'}, self.snapshot)
        self.assertTrue(execution['input_interpreted_by_model'])
        self.assertEqual(execution['result']['value'], {'sum': 12})

    def test_missing_stable_version_is_not_faked(self):
        with self.assertRaisesRegex(ValueError, 'ещё не найдена'):
            chat.run_example(self.job, {'text': 'Try'}, {})

    def test_program_error_is_actual_execution_not_chat_success(self):
        self.snapshot['stable_code'] = 'def solve(d):\n    raise ValueError("demo error")\n'
        _, execution = chat.run_example(self.job, {'text': '{"numbers":[]}'}, self.snapshot)
        self.assertFalse(execution['result']['ok'])
        self.assertIn('demo error', execution['result']['error'])

    def test_oversized_input_is_rejected_before_execution(self):
        with self.assertRaises(ValueError):
            chat.run_example(self.job, {'text': json.dumps({'numbers': ['x'*17000]})}, self.snapshot)

    def test_truncated_model_input_is_not_executed(self):
        response = {'choices': [{'finish_reason': 'length', 'message': {'content': '{"input":'}}]}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps(response).encode()
        with patch.object(chat.urllib.request, 'urlopen', return_value=Response()):
            with self.assertRaisesRegex(ValueError, 'не закончила'):
                chat.run_example(self.job, {'text': 'Сделай что-нибудь'}, self.snapshot)


if __name__ == '__main__':
    unittest.main(verbosity=2)
