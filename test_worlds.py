import copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import worlds as w

# Test-only programs. They are never sent as the experiment's seed or initial code.
MOVING='''def solve(data):
    s=data['state'] or {'x':0,'memory':0}
    s={'x':s['x']+1,'memory':s['memory']+s['x']}
    return {'state':s,'view':{'nodes':[{'id':'a','x':s['x'],'y':0,'kind':'unit','value':s['memory']}],'links':[],'caption':'test'}}
'''
STATIC="def solve(data):\n return {'state':{'tick':data['step']},'view':{'nodes':[{'id':'a','x':0,'y':0}], 'caption':str(data['step'])}}\n"

class WorldTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.frame={'state':{'memory':1},'view':{'nodes':[{'id':'a','x':0,'y':1,'kind':'x','value':2}], 'links':[]}}
    def tearDown(self):self.tmp.cleanup()
    def code(self,source):
        path=self.root/'candidate.py';path.write_text(source);return path
    def test_valid_minimal_projection(self):self.assertEqual(w.validate(self.frame),self.frame)
    def test_empty_state_is_allowed_no_ontology_prescribed(self):
        self.frame['state']={};w.validate(self.frame)
    def test_seed_present_only_before_viable_birth(self):
        c={'seed_text':'UNIQUE_STARTER_TEXT'}
        self.assertEqual(w.seed_context(c,{}),{'initial_seed':'UNIQUE_STARTER_TEXT'})
        self.assertEqual(w.seed_context(c,{'seed_removed':True}),{})
    def test_absolute_deadline_not_extended_by_resume(self):
        w.store(self.root/'control.json',{'desired':'running'})
        self.assertFalse(w.alive(self.root,{'deadline':time.time()-1}))
        self.assertTrue(w.alive(self.root,{'deadline':time.time()+60}))
        w.store(self.root/'control.json',{'desired':'paused'})
        self.assertFalse(w.alive(self.root,{'deadline':time.time()+60}))
    def test_runtime_time_not_generated_world_clock(self):
        w.store(self.root/'control.json',{'desired':'completed'})
        self.assertFalse(w.alive(self.root,{'deadline':time.time()+60}))
    def test_nan_and_infinity_rejected(self):
        for n in [float('nan'),float('inf'),float('-inf'),True,1000001]:
            with self.subTest(n=n):
                f=copy.deepcopy(self.frame);f['view']['nodes'][0]['x']=n
                with self.assertRaises(ValueError):w.validate(f)
    def test_duplicate_ids_rejected(self):
        self.frame['view']['nodes']*=2
        with self.assertRaises(ValueError):w.validate(self.frame)
    def test_dangling_links_rejected(self):
        self.frame['view']['links']=[{'source':'a','target':'missing'}]
        with self.assertRaises(ValueError):w.validate(self.frame)
    def test_oversized_world_rejected(self):
        self.frame['state']={'blob':'x'*48000}
        with self.assertRaises(ValueError):w.validate(self.frame)
    def test_zero_nodes_rejected(self):
        self.frame['view']['nodes']=[]
        with self.assertRaises(ValueError):w.validate(self.frame)
    def test_state_must_be_an_object(self):
        self.frame['state']=[]
        with self.assertRaises(ValueError):w.validate(self.frame)
    def test_descriptor_finite_for_extreme_valid_view(self):
        self.frame['view']['nodes'][0].update(x=-1e6,y=1e6,value=1e6)
        self.assertTrue(all(0<=x<=1 for x in w.descriptor(self.frame['view'])))
    def test_same_descriptor_zero_distance(self):
        d=w.descriptor(self.frame['view']);self.assertEqual(w.distance(d,d),0)
    def test_world_program_runs_in_existing_sandbox(self):
        value=w.execute(self.code(MOVING),None,42,0)
        self.assertEqual(value['state'],{'x':1,'memory':0})
        value=w.execute(self.root/'candidate.py',value['state'],42,1)
        self.assertEqual(value['state'],{'x':2,'memory':1})
    def test_changing_caption_and_counter_is_not_dynamics(self):
        path=self.code(STATIC)
        with self.assertRaisesRegex(ValueError,'No observable'):w.evaluate(path,{},42,[])
    def test_viable_birth_measured_by_actual_execution(self):
        metrics,first=w.evaluate(self.code(MOVING),{},42,[])
        self.assertEqual(metrics['activity'],1)
        self.assertEqual(first['state']['x'],1)
        self.assertEqual(metrics['novelty'],1)
    def test_current_state_continuation_is_checked(self):
        metrics,_=w.evaluate(self.code(MOVING),{'code':MOVING,'state':{'x':100,'memory':50},'frame':80},42,[])
        self.assertEqual(metrics['continuity_activity'],1)
    def test_incompatible_current_state_rejected(self):
        with self.assertRaisesRegex(ValueError,'World execution'):
            w.evaluate(self.code(MOVING),{'code':'previous','state':{'other':1},'frame':2},42,[])
    def test_candidate_cannot_read_host_file(self):
        code="def solve(d):\n return {'state':{'secret':open('/etc/rcp-lab.env').read()},'view':{'nodes':[{'id':'a','x':0,'y':0}]}}"
        with self.assertRaisesRegex(ValueError,'FileNotFoundError'):w.execute(self.code(code),None,42,0)
    def test_invalid_program_output_not_accepted(self):
        with self.assertRaises(ValueError):w.execute(self.code('def solve(d): return 42'),None,42,0)
    def test_request_window_at_most_24_hours(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):w.WorldRequest(hours=25)
        self.assertEqual(w.WorldRequest().hours,24)
    def test_entire_followup_payload_omits_original_seed(self):
        captured=[]
        answer={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'name':'new','hypothesis':'changed','question':'next','code':MOVING})}}]}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(answer).encode()
        def reply(request,**kwargs):
            captured.append(json.loads(request.data));return Response()
        config={'deadline':time.time()+100,'seed_text':'UNIQUE_EXTERNAL_SEED_DO_NOT_REPEAT'}
        with patch.object(w.urllib.request,'urlopen',side_effect=reply):
            w.proposal(config,{'seed_removed':True,'code':MOVING,'question':'Own question','state':{'x':1}},'')
        text=json.dumps(captured[0])
        self.assertNotIn('UNIQUE_EXTERNAL_SEED_DO_NOT_REPEAT',text)
        self.assertNotIn('initial_seed',text)
        self.assertIn('Own question',text)
        self.assertIn('files',text)

    def test_expired_worker_never_generates_or_ticks(self):
        w.store(self.root/'config.json',{'deadline':time.time()-1,'seed_text':'birth','random_seed':42})
        w.store(self.root/'control.json',{'desired':'running'})
        with patch.object(w,'proposal') as model,patch.object(w,'execute') as execute:
            w.worker(self.root)
        model.assert_not_called();execute.assert_not_called()
        self.assertEqual(w.load(self.root/'control.json')['desired'],'completed')
        self.assertEqual(w.load(self.root/'checkpoint.json')['frame'],0)

    def test_replay_error_identifies_salted_hash_and_field(self):
        path=self.code("def solve(d): return hash('node')")
        message=w.replay_error(path,{'state':{'amp':1}}, {'state':{'amp':2}})
        self.assertIn('$.state.amp',message)
        self.assertIn('hash()',message)
        self.assertIn('lines [1]',message)

    def test_long_rollout_catches_delayed_invalid_ids(self):
        source=MOVING.replace("return {'state':s", "return {'state':s")
        source=source.replace("'id':'a'", "'id':('a' if data['step']<50 else '')")
        with self.assertRaisesRegex(ValueError, 'step 50'):
            w.evaluate(self.code(source),{},42,[])

    def test_rollout_budget_prevents_new_execution(self):
        with patch.object(w,'execute') as execute:
            with self.assertRaises(TimeoutError):
                w.probe(self.root/'unused.py',{},42,0,deadline=time.monotonic()-1)
        execute.assert_not_called()

    def test_first_difference_tracks_nested_lists(self):
        self.assertIn('$.nodes[0].phase',w.first_difference({'nodes':[{'phase':1}]},{'nodes':[{'phase':2}]}))

    def test_runtime_fault_uses_repair_lane_not_world_redesign(self):
        captured=[]
        answer={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'name':'same','hypothesis':'repair','question':'own','code':MOVING})}}]}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(answer).encode()
        def reply(request,**kwargs):captured.append(json.loads(request.data));return Response()
        with patch.object(w.urllib.request,'urlopen',side_effect=reply):
            w.proposal({'deadline':time.time()+60,'seed_text':'DO_NOT_REINTRODUCE'},
                {'code':MOVING,'seed_removed':True,'simulation_error':'Duplicate IDs','last_failed_code':'UNRELATED_REDESIGN'},'')
        self.assertEqual(captured[0]['temperature'],0.2)
        self.assertIn('NOT a redesign',captured[0]['messages'][0]['content'])
        self.assertNotIn('DO_NOT_REINTRODUCE',json.dumps(captured[0]))
        self.assertNotIn('UNRELATED_REDESIGN',json.dumps(captured[0]))

    def test_expired_proposal_does_not_call_model(self):
        with patch.object(w.urllib.request,'urlopen') as model:
            with self.assertRaises(TimeoutError):w.proposal({'deadline':time.time()-1,'seed_text':'birth'}, {},'')
        model.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
