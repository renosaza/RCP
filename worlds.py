"""Open-ended world experiments. The model writes laws; this module owns safety and time."""
import ast
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from autopilot import load, store
from evaluator import sandbox

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('RCP_DATA', '/var/lib/rcp-lab')) / 'worlds'
MODEL = os.environ.get('RCP_MODEL_URL', 'http://127.0.0.1:8081')
CODE_LIMIT = 18000
STATE_LIMIT = 48000
CONTRACT = '''Return a JSON object with name (string), hypothesis (string), question (string), code (full Python module string).
The module exports solve(data). data has state (null for birth, otherwise your previous state dict), seed (int), step (int).
Return {"state": dict, "view": {"nodes": [{"id": str, "x": number, "y": number, "kind": str, "value": number}], "links": [{"source": str, "target": str}], "caption": str}}.
Only the view is prescribed for display; its coordinates may be projections of any internal space, not physical laws.
Invent the complete internal state, transition rules, local decisions and memory yourself. No prewritten world code is supplied.
Use Python standard library only, no files, network, subprocesses, external services, package installs or clocks.
Keep code below 18000 characters, state compact (under 48000 bytes), 1..160 visible nodes, at most 600 links.
Finite coordinates and values, unique string node IDs; link endpoints must exist. Preserve deterministic replay with the same data.
Do not claim success, fabricate measured scores or merely increment labels: behavior is executed by an external checker.
The code cannot edit its checker, infrastructure, limits, model weights or runtime. These are not part of the world.
Answer in Russian for name/hypothesis/question/caption; code identifiers can be English. Full code, not a diff. No markdown outside JSON.'''


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def validate(value):
    if not isinstance(value, dict) or not isinstance(value.get('state'), dict):
        raise ValueError('Result must contain state: object')
    if len(packed(value['state']).encode()) > STATE_LIMIT:
        raise ValueError('State exceeds 48000 bytes')
    view = value.get('view')
    if not isinstance(view, dict):
        raise ValueError('Missing view object')
    nodes, links = view.get('nodes'), view.get('links', [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 160:
        raise ValueError('View must contain 1..160 nodes')
    if not isinstance(links, list) or len(links) > 600:
        raise ValueError('View link limit is 600')
    ids = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError('Node must be an object')
        key = node.get('id')
        if not isinstance(key, str) or not 1 <= len(key) <= 64 or key in ids:
            raise ValueError('Node IDs must be unique strings of 1..64 characters')
        ids.add(key)
        for field in ('x', 'y', 'value'):
            number = node.get(field, 0 if field == 'value' else None)
            if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or abs(number) > 1e6:
                raise ValueError('Node coordinates/value must be finite numbers within 1e6')
        if not isinstance(node.get('kind', ''), str) or len(node.get('kind', '')) > 80:
            raise ValueError('Node kind must be a short string')
    for link in links:
        if not isinstance(link, dict) or link.get('source') not in ids or link.get('target') not in ids:
            raise ValueError('Link endpoint is not a visible node')
    if not isinstance(view.get('caption', ''), str) or len(view.get('caption', '')) > 1500:
        raise ValueError('Caption must be a short string')
    if len(packed(value).encode()) > 96000:
        raise ValueError('Combined result exceeds 96 KB')
    return value


def execute(path, state, seed, step):
    results = sandbox(path, [{'state': state, 'seed': seed, 'step': step}])
    if len(results) != 1 or results[0].get('ok') is not True:
        raise ValueError('World execution: ' + str(results[0] if results else 'no output')[:1200])
    return validate(results[0]['value'])


def descriptor(view):
    nodes = view['nodes']; count = len(nodes)
    kinds = len({n.get('kind', '') for n in nodes})
    xs = [n['x'] for n in nodes]; ys = [n['y'] for n in nodes]
    extent = math.hypot(max(xs)-min(xs), max(ys)-min(ys))
    mean = sum(abs(n.get('value', 0)) for n in nodes) / count
    return [math.log1p(count)/math.log(161), kinds/count,
            min(1, math.log1p(extent)/12), min(1, len(view.get('links', []))/count/5),
            min(1, math.log1p(mean)/12)]


def distance(a, b):
    return math.sqrt(sum((x-y)**2 for x,y in zip(a,b))/len(a))


def probe(path, state, seed, step, count=8, deadline=None):
    frames = []
    current = copy.deepcopy(state)
    for i in range(count):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('Validation exceeded its wall-time budget')
        try:
            frame = execute(path, current, seed, step+i)
        except Exception as exc:
            raise ValueError(f'Rollout failed at step {step+i}: {exc}') from exc
        frames.append(frame); current = frame['state']
    # Rendering text and a tick counter alone do not count as observed dynamics.
    structural = [digest({'nodes': f['view']['nodes'], 'links': f['view'].get('links', [])}) for f in frames]
    activity = (len(set(structural))-1)/max(1, count-1)
    return {'activity': activity, 'descriptor': descriptor(frames[-1]['view']),
            'nodes': len(frames[-1]['view']['nodes']), 'links': len(frames[-1]['view'].get('links', [])),
            'replay_steps': count}, frames[-1]


def seed_context(config, snapshot):
    if snapshot.get('seed_removed'):
        return {}  # Deliberately omit the original direction after a viable birth.
    return {'initial_seed': config['seed_text']}


def proposal(config, snapshot, feedback):
    context = {'current_name': snapshot.get('name'), 'current_code': snapshot.get('code'),
               'self_chosen_question': snapshot.get('question'),
               'observed': snapshot.get('metrics'), 'runtime_error':snapshot.get('simulation_error'),
               'last_failed_code_excerpt':(snapshot.get('last_failed_code') or '')[:5000],
               'state_excerpt': packed(snapshot.get('state'))[:4000],
               'view_sample': {'nodes':snapshot.get('view',{}).get('nodes',[])[:16],
                               'links':snapshot.get('view',{}).get('links',[])[:24],
                               'caption':snapshot.get('view',{}).get('caption','')[:600]},
               'last_attempt_feedback': feedback[-1500:],
               **seed_context(config, snapshot)}
    mode = ('Invent a minimal world from the initial seed. Its beings need not resemble life or people. '
            'Let interactions and local memory produce consequences; do not write a fixed story or roadmap.'
            if not snapshot.get('code') else
            'The birth seed is detached. Examine the existing world and observed consequences. '
            'Choose your own next question and a causal change, rather than completing a predefined roadmap. '
            'You may change laws, internal representation and local decision/memory mechanisms. '
            'Your new solve must accept the current state as well as state=null. '
            'Do not erase persistent state merely to restart a predetermined animation. '
            'Novelty metrics are rough observations, not proof of intelligence or improvement.')
    mode += ('\nThe sandbox uses isolated Python processes: builtin hash(str) is process-randomized. '
             'Do not use hash() for deterministic IDs/noise. Use explicit integer IDs or hashlib instead. '
             'Persist a monotonic next-ID counter; len(nodes) is not a unique ID after deletion. '
             'Every field added to state/nodes needs migration defaults for the existing world.')
    if snapshot.get('simulation_error'):
        mode += ('\nThe CURRENT LIVE WORLD is blocked by runtime_error. Repair this fault first, '
                 'preserving its existing state. Do not add unrelated new mechanics until it advances again.')
    schema = {'type':'object','properties':{key:{'type':'string'} for key in ('name','hypothesis','question','code')},
              'required':['name','hypothesis','question','code'],'additionalProperties':False}
    remaining = config['deadline']-time.time()
    if remaining <= 0:
        raise TimeoutError('Experiment deadline reached')
    payload = {'model':'rcp-coder','temperature':0.8,'max_tokens':3500,
               'chat_template_kwargs':{'enable_thinking':False},
               'response_format':{'type':'json_object','schema':schema},
               'messages':[{'role':'system','content':mode+'\n'+CONTRACT},
                           {'role':'user','content':packed(context)}]}
    req = urllib.request.Request(MODEL+'/v1/chat/completions',data=json.dumps(payload).encode(),
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=max(1,min(900,remaining))) as response:
        choice = json.load(response)['choices'][0]
    if choice.get('finish_reason') == 'length':
        raise ValueError('Model answer truncated. Return a smaller complete program, under 160 lines.')
    result = json.loads(choice['message']['content'])
    if not isinstance(result, dict) or not all(isinstance(result.get(k),str) for k in schema['required']):
        raise ValueError('Incomplete proposal')
    if len(result['code']) > CODE_LIMIT:
        raise ValueError('Code exceeds 18000 characters')
    ast.parse(result['code'])
    return result


def first_difference(left, right, path='$', depth=0):
    if depth > 10:
        return path
    if type(left) is not type(right):
        return path + ': types differ'
    if isinstance(left, dict):
        if set(left) != set(right):
            return path + ': keys differ'
        for key in left:
            if packed(left[key]) != packed(right[key]):
                return first_difference(left[key], right[key], path + '.' + str(key), depth+1)
    elif isinstance(left, list):
        if len(left) != len(right):
            return path + ': lengths differ'
        for i, (a, b) in enumerate(zip(left, right)):
            if packed(a) != packed(b):
                return first_difference(a, b, path + f'[{i}]', depth+1)
    return path + ': ' + repr(left)[:120] + ' != ' + repr(right)[:120]


def replay_error(path, first, again):
    message = 'Identical seed/input must replay deterministically. Difference: ' + first_difference(first, again)
    tree = ast.parse(path.read_text())
    hashes = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'hash']
    if hashes:
        message += '. hash() is called at lines ' + str(hashes) + '; builtin string hashes vary across isolated Python processes. Use explicit integer IDs/hashlib/seeded RNG instead.'
    return message


def evaluate(path, snapshot, seed, descriptors, budget_seconds=30):
    deadline = time.monotonic() + budget_seconds
    first = execute(path, None, seed, 0)
    again = execute(path, None, seed, 0)
    if packed(first) != packed(again):
        raise ValueError(replay_error(path, first, again))
    metrics, last = probe(path, first['state'], seed, 1, deadline=deadline)
    if metrics['activity'] <= 0:
        raise ValueError('No observable node/link dynamics in the rollout; text-only changes do not count')
    # Catch delayed birth/death/ID failures before committing an otherwise healthy first frame.
    probe(path, last['state'], seed, 9, 152, deadline=deadline)
    metrics['long_rollout_steps'] = 160
    other = execute(path, None, seed+113, 0)
    probe(path, other['state'], seed+113, 1, 16, deadline=deadline)
    if snapshot.get('code'):
        continuity, _ = probe(path, snapshot['state'], seed, snapshot['frame']+1, 16, deadline=deadline)
        metrics['continuity_activity'] = continuity['activity']
    metrics['novelty'] = min((distance(metrics['descriptor'], d) for d in descriptors), default=1.0)
    return metrics, first


def alive(job, config):
    return time.time() < config['deadline'] and load(job/'control.json',{}).get('desired') == 'running'


def worker(job):
    config = load(job/'config.json')
    lock = threading.RLock()
    state = load(job/'checkpoint.json', {'frame':0,'attempts':0,'accepted':0,'archive':[], 'history':[]})
    state.update(status='running',phase='starting',error=None)
    stop = threading.Event()
    def save():
        state['updated'] = time.time()
        store(job/'checkpoint.json', state)
    def event(kind, text, **extra):
        state['history'] = (state.get('history',[])+[{'time':time.time(),'kind':kind,'text':text,**extra}])[-160:]
    def tick_loop():
        while not stop.is_set() and alive(job,config):
            with lock:
                revision=state.get('version'); source=state.get('code_file')
                before=copy.deepcopy(state.get('state')); frame=state['frame']
            if source:
                try:
                    result=execute(job/source,before,config['random_seed'],frame+1)
                    with lock:
                        if revision==state.get('version') and alive(job,config):
                            state.update(state=result['state'],view=result['view'],frame=frame+1,
                                         simulation_error=None,last_frame_at=time.time())
                            state['trace']=(state.get('trace',[])+[{'frame':frame+1,'nodes':len(result['view']['nodes']),
                                'kinds':len({n.get('kind','') for n in result['view']['nodes']}),'links':len(result['view'].get('links',[]))}])[-300:]
                            save()
                except Exception as exc:
                    with lock:
                        state['simulation_error']=str(exc)[:1200]
                        save()
            stop.wait(2)
    simulation=threading.Thread(target=tick_loop,name='world-simulation',daemon=True)
    simulation.start()
    feedback=''; failures=0
    try:
        while alive(job,config):
            if shutil.disk_usage(job).free < 1536*1024**2:
                with lock:state.update(status='paused',phase='attention',error='Less than 1.5 GiB free');save()
                store(job/'control.json',{'desired':'paused'});break
            with lock:
                state.update(phase='imagining',error=None,attempts=state['attempts']+1)
                snapshot=copy.deepcopy(state); attempt=state['attempts'];save()
            candidate=None
            try:
                candidate=proposal(config,snapshot,feedback)
                if not alive(job,config):break
                name=f'candidate-{attempt:06d}.py';path=job/name;path.write_text(candidate['code'])
                with lock:state.update(phase='testing',hypothesis=candidate['hypothesis'][:1200]);save()
                metrics,birth=evaluate(path,snapshot,config['random_seed'],[x['descriptor'] for x in snapshot.get('archive',[])],
                                       budget_seconds=min(30,max(1,config['deadline']-time.time())))
                changed=digest(candidate['code'])!=digest(snapshot.get('code'))
                # Some valid variants remain in the archive; diversity is not equivalent to improvement.
                accepted=changed and (not snapshot.get('code') or metrics['novelty']>=0.012 or attempt%4==0)
                with lock:
                    if not alive(job,config):break
                    branch={'attempt':attempt,'name':candidate['name'][:100],'hypothesis':candidate['hypothesis'][:1000],
                            'question':candidate['question'][:1000],'parent':state.get('version'),
                            'descriptor':metrics['descriptor'],'metrics':metrics,'code_file':name,'time':time.time()}
                    if accepted:
                        # Recheck against the most recent live state, not the stale proposal snapshot.
                        current=execute(path,state.get('state'),config['random_seed'],state['frame']+1) if state.get('code') else birth
                        state.update(code=candidate['code'],code_file=name,name=candidate['name'][:100],
                                     question=candidate['question'][:1000],state=current['state'],view=current['view'],
                                     version=f'g{attempt:06d}',metrics=metrics,accepted=state['accepted']+1,
                                     seed_removed=True,seed_detached_at=state.get('seed_detached_at',time.time()),
                                     frame=state['frame']+1,simulation_error=None,last_failed_code=None)
                        branch['version']=state['version']
                        store(job/f'version-{attempt:06d}.json',{**branch,'code':candidate['code'],'state':state['state'],'view':state['view']})
                        event('accepted','Приняты новые законы: '+candidate['hypothesis'][:600],version=state['version'])
                    else:
                        event('archived','Вариант жизнеспособен, но мало отличается по наблюдаемым признакам.',attempt=attempt)
                    branch['accepted']=accepted
                    state['archive']=(state.get('archive',[])+[branch])[-128:]
                    state.update(phase='observing',last_metrics=metrics,error=None)
                    save()
                feedback='Last variant '+('accepted' if accepted else 'archived; choose a meaningfully different causal mechanism')+'. Observed: '+packed(metrics)
                failures=0
            except Exception as exc:
                feedback=f'{type(exc).__name__}: {exc}'[:1600];failures+=1
                with lock:
                    state.update(phase='retry',error=feedback)
                    if candidate is not None:
                        state['last_failed_code']=candidate['code'][:8000]
                    event('rejected',feedback,attempt=attempt);save()
            # Retain all selected code and only a short window of rejected proposals.
            with lock: keep={x['code_file'] for x in state.get('archive',[])}|{state.get('code_file')}
            for old in sorted(job.glob('candidate-*.py'))[:-12]:
                if old.name not in keep:old.unlink(missing_ok=True)
            for _ in range(min(60,5+failures*5)):
                if not alive(job,config):break
                stop.wait(1)
    finally:
        stop.set();simulation.join(timeout=7)
        with lock:
            expired=time.time()>=config['deadline']
            state.update(status='completed' if expired else 'paused',phase='completed' if expired else 'paused')
            save()
        if expired:store(job/'control.json',{'desired':'completed'})


class WorldRequest(BaseModel):
    seed_text: str = Field(default='Пусть из минимальной затравки возникнет самостоятельный странный мир с собственными закономерностями и действующими сущностями. Не копируй человеческое общество. Выбери всё остальное сам.',min_length=8,max_length=2000)
    hours: int = Field(default=24,ge=1,le=24)


class WorldQuestion(BaseModel):
    text: str = Field(min_length=1,max_length=2000)


def install(svc):
    DATA.mkdir(parents=True,exist_ok=True)
    def locate(wid):
        if not re.fullmatch(r'[a-f0-9]{12}',wid) or not (DATA/wid/'config.json').is_file():raise HTTPException(404,'Мир не найден')
        return DATA/wid
    def spawn(job):
        config=load(job/'config.json')
        if time.time()>=config['deadline']:raise HTTPException(409,'24-часовое окно завершено; снимки сохранены')
        store(job/'control.json',{'desired':'running'})
        env={'PATH':str(ROOT/'.venv/bin')+':/usr/bin:/bin','HOME':str(DATA.parent),'LANG':'C.UTF-8',
             'RCP_DATA':str(DATA.parent),'PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1',
             'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'}
        with (job/'worker.log').open('a') as log:
            proc=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'worlds.py'),str(job)],
                                  cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        svc.ACTIVE.update(proc=proc,id=job.name,kind='world')
        def watch():
            proc.wait()
            with svc.LOCK:
                if svc.ACTIVE.get('proc') is proc:
                    svc.ACTIVE.update(proc=None,id=None,kind=None)
                if proc.returncode and not getattr(svc,'SHUTTING_DOWN',False) and load(job/'control.json',{}).get('desired')=='running':
                    store(job/'control.json',{'desired':'paused','error':f'Worker exit {proc.returncode}'})
        threading.Thread(target=watch,daemon=True).start()
    def boot():
        with svc.LOCK:
            for job in sorted(DATA.iterdir()):
                if job.is_dir() and load(job/'control.json',{}).get('desired')=='running':
                    if time.time()>=load(job/'config.json',{})['deadline']:
                        store(job/'control.json',{'desired':'completed'})
                    elif not svc.busy():spawn(job)
    svc.world_boot=boot
    @svc.app.get('/world')
    def page():return FileResponse(ROOT/'static/world.html')
    @svc.app.get('/api/worlds')
    def listing():
        return [{**load(p/'config.json',{}),'control':load(p/'control.json',{})} for p in sorted(DATA.iterdir()) if (p/'config.json').exists()]
    @svc.app.post('/api/worlds')
    def create(body:WorldRequest):
        with svc.LOCK:
            if svc.busy():raise HTTPException(409,'Сначала поставь активный эксперимент на паузу')
            job=DATA/secrets.token_hex(6);job.mkdir(mode=0o750)
            now=time.time();store(job/'config.json',{'id':job.name,'seed_text':body.seed_text,'started':now,
                'deadline':now+body.hours*3600,'hours':body.hours,'random_seed':secrets.randbelow(2**31)})
            spawn(job)
        return {'id':job.name}
    @svc.app.get('/api/worlds/{wid}')
    def detail(wid:str):
        job=locate(wid);checkpoint=load(job/'checkpoint.json',{})
        return {'config':load(job/'config.json'),'control':load(job/'control.json'),'checkpoint':checkpoint,'server_time':time.time()}
    @svc.app.post('/api/worlds/{wid}/pause')
    def pause(wid:str):
        job=locate(wid)
        with svc.LOCK:
            store(job/'control.json',{'desired':'paused'})
            if svc.ACTIVE.get('id')==wid and svc.ACTIVE.get('kind')=='world':
                svc.stop_process(svc.ACTIVE['proc']);svc.ACTIVE.update(proc=None,id=None,kind=None)
        return {'status':'paused'}
    @svc.app.post('/api/worlds/{wid}/resume')
    def resume(wid:str):
        with svc.LOCK:
            if svc.busy():raise HTTPException(409,'Модель уже занята другим экспериментом')
            spawn(locate(wid))
        return {'status':'running'}
    @svc.app.get('/api/worlds/{wid}/messages')
    def messages(wid:str):
        from chat import messages as read_messages
        return read_messages(locate(wid))
    @svc.app.post('/api/worlds/{wid}/messages')
    def discuss(wid:str,body:WorldQuestion):
        from chat import queue, answer
        job=locate(wid)
        with svc.LOCK:
            message=queue(job,body.text,'ask')
        checkpoint=load(job/'checkpoint.json',{})
        snapshot={'project': {'name':checkpoint.get('name'),'mode':'open-ended world'},
                  'state':{'phase':checkpoint.get('phase'),'frame':checkpoint.get('frame'),'metrics':checkpoint.get('metrics')},
                  'control':load(job/'control.json'), 'contract':CONTRACT,
                  'roadmap':[], 'stable_version':checkpoint.get('version'),
                  'code_changes':checkpoint.get('accepted',0),'accepted_steps':[],
                  'tests_count':0,'test_examples':[], 'stable_code':checkpoint.get('code','')[:12000],
                  'pending_step':checkpoint.get('question'), 'view':checkpoint.get('view'),
                  'latest_directive':None,'priority_status':'none','applied_guidance_revision':0}
        path=job/'chat'/(message['id']+'.json')
        try:
            reply=answer(job,message,snapshot)
            store(path,{**message,'status':'done','reply':reply,'answered':time.time()})
        except Exception as exc:
            store(path,{**message,'status':'error','reply':'Ошибка локальной модели: '+str(exc)[:300]})
            raise HTTPException(502,'Не удалось получить ответ локальной модели') from exc
        return {'id':message['id'],'reply':reply}
    @svc.app.get('/api/worlds/{wid}/code.py')
    def code(wid:str):
        snapshot=load(locate(wid)/'checkpoint.json',{})
        if not snapshot.get('code'):raise HTTPException(404,'Первая версия ещё не принята')
        return Response(snapshot['code'],media_type='text/plain',headers={'Content-Disposition':'attachment; filename="world.py"'})


if __name__=='__main__':
    job=Path(sys.argv[1]).resolve()
    with (job/'worker.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        worker(job)

# WORLD_REPLAY_DIAGNOSTICS
