import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from pydantic import ValidationError
import autopilot as a
import chat


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.job=Path(self.tmp.name)
        a.store(self.job/'config.json',{'goal':'Test project'})
    def tearDown(self): self.tmp.cleanup()

    def test_ask_does_not_change_plan(self):
        message=chat.queue(self.job,'Что уже сделано?','ask')
        self.assertEqual(message['status'],'queued')
        self.assertFalse((self.job/'guidance.json').exists())
        self.assertEqual(len(chat.messages(self.job)),1)

    def test_directive_is_durable_and_revised(self):
        chat.queue(self.job,'Добавь стены','direct')
        chat.queue(self.job,'Сначала добавь энергию','direct')
        self.assertEqual(a.load(self.job/'guidance.json')['revision'],2)
        self.assertEqual(a.load(self.job/'guidance.json')['text'],'Сначала добавь энергию')
        self.assertEqual(len(chat.messages(self.job)),2)

    def test_empty_message_rejected(self):
        with self.assertRaises(HTTPException): chat.queue(self.job,'   ','ask')

    def test_queue_is_bounded(self):
        for i in range(6): chat.queue(self.job,str(i),'ask')
        with self.assertRaises(HTTPException) as caught: chat.queue(self.job,'extra','ask')
        self.assertEqual(caught.exception.status_code,429)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValidationError): chat.MessageRequest(text='Hello',mode='execute')

    def test_guidance_preserves_code_and_tests(self):
        progress={'code':'def solve(d): return d','tests':[{'input':{},'expected':{}}],
                  'pending':{'title':'old'},'attempt_code':'unaccepted'}
        guidance={'revision':1,'text':'Add a feature'}
        self.assertTrue(a.apply_guidance(self.job,progress,guidance))
        self.assertEqual(progress['code'],'def solve(d): return d')
        self.assertEqual(progress['tests'],[{'input':{},'expected':{}}])
        self.assertIsNone(progress['pending'])
        self.assertIsNone(progress['attempt_code'])
        self.assertFalse(a.apply_guidance(self.job,progress,guidance))
        self.assertEqual(len(a.load(self.job/'history.json')),1)

    def test_initial_version_not_discarded_for_guidance(self):
        p={'code':None,'pending':{'title':'Initial','tests':[],'tries':0}}
        a.apply_guidance(self.job,p,{'revision':1,'text':'New idea'})
        self.assertEqual(p['pending']['title'],'Initial')

    def test_planner_requires_objects_not_json_strings(self):
        self.assertEqual(a.TEST_SCHEMA['items']['properties']['input'],{'type':'object'})
        self.assertEqual(a.TEST_SCHEMA['items']['properties']['expected'],{'type':'object'})

    def test_model_context_has_actual_state(self):
        a.store(self.job/'progress.json',{'version':'v00000003','code':'test',
                'tests':[{'input':{},'expected':{}}],'code_changes':2,'guidance_revision':1})
        value=chat.context(self.job)
        self.assertEqual(value['stable_version'],'v00000003')
        self.assertEqual(value['tests_count'],1)
        self.assertEqual(value['applied_guidance_revision'],1)
        self.assertNotIn('password',value)


if __name__=='__main__': unittest.main(verbosity=2)
