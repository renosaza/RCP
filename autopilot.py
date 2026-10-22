"""Durable, local, open-ended project loop around the existing OpenEvolve sandbox."""
import ast
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error

import yaml
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('RCP_DATA', '/var/lib/rcp-lab'))
MISSIONS = DATA / 'missions'
MODEL = 'http://127.0.0.1:8081'
MAX_TESTS = 80
BASE = ('You are a local Python project researcher. Develop only the requested project, not yourself or your tools. '
        'One Python module may contain many functions/classes, but its public entry point is solve(data): '
        'Input and result MUST be JSON OBJECTS (Python dicts), not JSON-encoded strings. '
        'Use a compact extensible object API, e.g. op and arguments or state and action. '
        'State must travel explicitly in JSON. Python standard library only. '
        'No network, shell, host files, package installs, external services or clocks. Deterministic behavior. '
        'You may design a small game engine, simulation, planner or algorithm library behind a stable JSON API. '
        'Keep the original task and existing behavior intact. Choose small steps the local model can implement. '
        'Write titles, explanations and roadmap in Russian; API keys may be English. ')


def load(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def store(path, value):
    tmp = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    with tmp.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def merge_tests(existing, added):
    if not isinstance(added, list) or len(added) > 10:
        raise ValueError('Планировщик должен вернуть не больше 10 тестов за один шаг')
    result = list(existing)
    seen = {canonical(t['input']): canonical(t['expected']) for t in existing}
    for test in added:
        if not isinstance(test, dict) or not {'input', 'expected'} <= test.keys():
            raise ValueError('Некорректный формат теста')
        key, value = canonical(test['input']), canonical(test['expected'])
        if key in seen:
            if seen[key] != value:
                raise ValueError('Новая проверка противоречит уже зафиксированному ответу')
            continue
        result.append({'input': test['input'], 'expected': test['expected']})
        seen[key] = value
    if len(result) > MAX_TESTS or len(canonical(result).encode()) > 18000:
        raise ValueError('Лимит регрессионного набора: 80 тестов / 18 КБ')
    return result


TEST_SCHEMA = {'type': 'array', 'maxItems': 6, 'items': {
    'type': 'object', 'properties': {'input': {'type': 'object'}, 'expected': {'type': 'object'}},
    'required': ['input', 'expected'], 'additionalProperties': False}}
PLAN_SCHEMA = {'type': 'object', 'properties': {
    'title': {'type': 'string'}, 'contract': {'type': 'string'},
    'roadmap': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 6},
    'tests': TEST_SCHEMA, 'assumptions': {'type': 'string'}},
    'required': ['title', 'contract', 'roadmap', 'tests', 'assumptions'], 'additionalProperties': False}
STEP_SCHEMA = {'type': 'object', 'properties': {'title': {'type': 'string'},
    'reason': {'type': 'string'}, 'tests': TEST_SCHEMA},
    'required': ['title', 'reason', 'tests'], 'additionalProperties': False}


def shape_schema(values, depth=0):
    if not values or depth > 10:
        return {}
    if all(isinstance(v, dict) for v in values):
        keys = set.intersection(*(set(v) for v in values))
        return {'type': 'object', 'properties': {
            k: shape_schema([v[k] for v in values], depth + 1) for k in sorted(keys)},
            'required': sorted(keys), 'additionalProperties': True}
    if all(isinstance(v, list) for v in values):
        return {'type': 'array', 'items': shape_schema([x for v in values for x in v], depth + 1)}
    if all(isinstance(v, bool) for v in values):
        return {'type': 'boolean'}
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return {'type': 'number'}
    if all(isinstance(v, str) for v in values):
        return {'type': 'string'}
    if all(v is None for v in values):
        return {'type': 'null'}
    return {}


def check_shape(value, schema, path):
    kind = schema.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'number': (int, float), 'boolean': bool}
    if kind in types and (not isinstance(value, types[kind]) or (kind == 'number' and isinstance(value, bool))):
        raise ValueError(f'{path}: preserve the existing {kind} type')
    if kind == 'null' and value is not None:
        raise ValueError(f'{path}: preserve null')
    if kind == 'object':
        for key in schema.get('required', []):
            if key not in value:
                raise ValueError(f'{path}.{key} is required. Do not move nested fields to a different level.')
            check_shape(value[key], schema['properties'][key], path + '.' + key)
    elif kind == 'array':
        for i, item in enumerate(value):
            check_shape(item, schema['items'], f'{path}[{i}]')


def operation_api(tests):
    return bool(tests) and all(isinstance(t.get('input'), dict) and
                              isinstance(t['input'].get('op'), str) for t in tests)


def validate_extension(existing, added):
    if not existing:
        return
    keyed = operation_api(existing)
    for index, test in enumerate(added):
        group = existing
        if keyed:
            if not isinstance(test.get('input'), dict) or not isinstance(test['input'].get('op'), str):
                raise ValueError('Preserve the string op field in the input object')
            group = [t for t in existing if t['input']['op'] == test['input']['op']]
            if not group:
                if not isinstance(test.get('expected'), dict):
                    raise ValueError('A new operation must return a JSON object')
                continue
        for field in ('input', 'expected'):
            check_shape(test[field], shape_schema([t[field] for t in group]), f'tests[{index}].{field}')


def ask(system, context):
    schema = json.loads(json.dumps(STEP_SCHEMA if 'contract' in context else PLAN_SCHEMA))
    if context.get('existing_tests') and not operation_api(context['existing_tests']):
        for field in ('input', 'expected'):
            schema['properties']['tests']['items']['properties'][field] = shape_schema(
                [t[field] for t in context['existing_tests']])

    payload = {'model': 'rcp-coder', 'messages': [
        {'role': 'system', 'content': BASE + system},
        {'role': 'user', 'content': canonical(context)}],
        'temperature': 0.65, 'max_tokens': 2400,
        'response_format': {'type': 'json_object', 'schema': schema}}
    req = urllib.request.Request(MODEL + '/v1/chat/completions',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=180) as response:
        result = json.load(response)
    if result['choices'][0].get('finish_reason') == 'length':
        raise ValueError('Ответ планировщика обрезан; нужен более короткий план')
    result = json.loads(result['choices'][0]['message']['content'])
    if not isinstance(result, dict):
        raise ValueError('Планировщик вернул не объект')
    return result


def read_events(path):
    events = []
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def update(job, **fields):
    state = load(job / 'state.json', {})
    state.update(fields, updated=time.time())
    store(job / 'state.json', state)
    return state


def note(job, kind, text, **extra):
    history = load(job / 'history.json', [])
    history.append({'time': time.time(), 'kind': kind, 'text': text, **extra})
    store(job / 'history.json', history[-400:])


def clean(job):
    # Bounded retention, not an endlessly growing log/checkpoint directory.
    for path in sorted((job / 'rounds').glob('round-*'))[:-5]:
        shutil.rmtree(path)
    for path in sorted((job / 'versions').glob('v*.json'))[:-20]:
        path.unlink()


def initial_plan(job, config):
    update(job, phase='planning', step='Составляю контракт, стартовые проверки и план развития')
    raw = ask('Return JSON: title (short), contract (precise stable input/output rules for a minimal first version), '
              'roadmap (array of 5 small feature ideas consistent with the goal), '
              'tests (4 to 6 small exact input/expected pairs for ONLY the first minimal feature), '
              'assumptions (brief scope limitations). Compute expected answers carefully. '
              'The contract MUST be a descriptive STRING specifying exact keys, output keys and boundary behavior. '
              'Choose ONE small initial feature. Keep examples compact, under 600 characters each. '
              'input and expected must be native JSON objects, NEVER strings with serialized JSON. '
              'All test inputs must be DISTINCT: vary values, actions and boundary conditions. '
              'Do not repeat examples. No code.', {'goal': config['goal'],
              'latest_user_directive': config.get('guidance', ''),
              'previous_error_to_correct': load(job / 'state.json', {}).get('last_error')})
    store(job / 'planner_last.json', raw)
    tests = merge_tests([], raw.get('tests'))
    if len(tests) < 2 or not isinstance(raw.get('contract'), str):
        raise ValueError('Contract must be a descriptive STRING. At least two DISTINCT test inputs are required, duplicates do not count.')
    roadmap = raw.get('roadmap', [])
    if not isinstance(roadmap, list):
        raise ValueError('План должен быть списком')
    plan = {'title': str(raw.get('title', 'Новый проект'))[:160],
            'contract': raw['contract'][:4000], 'roadmap': [str(x)[:500] for x in roadmap[:8]],
            'tests': tests, 'assumptions': str(raw.get('assumptions', ''))[:1500],
            'test_source': 'local_model_unverified', 'created': time.time()}
    store(job / 'plan.json', plan)
    note(job, 'plan', 'Стартовый контракт зафиксирован. Тесты предложены моделью, а не человеком.')
    return plan


def validate_directive_target(directive, added):
    # A literal API operation in a request is an exact constraint, not an LLM opinion.
    match = re.search(r"""(?<!\w)["']?op["']?\s*[:=]\s*["']([A-Za-z][A-Za-z0-9_-]{0,47})["']""", directive)
    if match and not any(t.get('input', {}).get('op') == match.group(1) for t in added):
        raise ValueError('The user explicitly requested op=' + match.group(1) +
                         '. Add NEW tests for that operation, not for a different roadmap task.')


def propose(job, config, plan, progress):
    update(job, phase='planning', step='Выбираю следующий шаг развития')
    if len(progress['tests']) >= 74 or len(canonical(progress['tests']).encode()) > 14000:
        if config.get('guidance'):
            raise ValueError('Лимит контекста регрессионных проверок: новое поручение не выполнено. Создай отдельный проект или сними приоритет.')
        return {'title': 'Упростить и улучшить реализацию без регрессий', 'tests': [], 'tries': 0}
    roadmap = plan.get('roadmap', [])
    cursor = progress.get('roadmap_cursor', len(progress.get('completed', [])))
    focus = config.get('guidance') or (roadmap[cursor % len(roadmap)] if roadmap else config['goal'])
    progress['roadmap_cursor'] = cursor + 1
    store(job / 'progress.json', progress)
    raw = ask('Focus on the selected milestone unless an active user directive overrides it. '
              'Do not keep redoing a previously accepted feature. '
              'Suggest ONE next small feature or edge-case improvement. Return JSON with title, '
              'reason, tests (2 to 4 NEW input/expected pairs). Do NOT change old answers, '
              'the original contract or existing output schemas. New operations may add behavior. '
              'Use only JSON values, no assertions or test code. Prefer meaningful functionality '
              'over cosmetic repetition. If no safe extension is useful return tests: [] and suggest '
              'an alternative implementation. Existing tests are immutable. No code.' +
              ('\nTHE USER REQUEST BELOW OVERRIDES THE ROADMAP. Implement this next, not an unrelated feature.\n' + config['guidance'] if config.get('guidance') else ''),
              {'goal': config['goal'], 'contract': plan['contract'], 'roadmap': [] if config.get('guidance') else plan['roadmap'],
               'selected_milestone': focus,
               'already_accepted': progress.get('completed', [])[-12:],
               'avoid_failed_steps': progress.get('skipped', [])[-6:],
               'current_code': progress['code'][:14000], 'existing_tests': progress['tests'],
               'latest_user_directive': config.get('guidance', ''),
              'previous_error_to_correct': load(job / 'state.json', {}).get('last_error')})
    store(job / 'planner_last.json', raw)
    validate_extension(progress['tests'], raw.get('tests', []))
    all_tests = merge_tests(progress['tests'], raw.get('tests', []))
    added = all_tests[len(progress['tests']):]
    validate_directive_target(config.get('guidance', ''), added)
    return {'title': str(raw.get('title', 'Исследовать другую реализацию'))[:300],
            'reason': str(raw.get('reason', ''))[:900], 'tests': added, 'tries': 0}


def check_code(code, tests, directory):
    from evaluator import sandbox, equal
    ast.parse(code)
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        path = Path(tmp) / 'candidate.py'
        path.write_text(code)
        results = sandbox(path, [t['input'] for t in tests])
    return all(isinstance(r, dict) and r.get('ok') is True and equal(r.get('value'), t['expected'])
               for r, t in zip(results, tests))


def focused_repair(trial, best, tests):
    # A short repair prompt avoids repeatedly copying a long, failing parent.
    payload = {'model': 'rcp-coder', 'temperature': 0.25, 'max_tokens': 3200,
        'messages': [
        {'role': 'system', 'content': 'Repair this Python function using the concrete test failures. '
         'The expected answers are the specification. Keep all passing cases working. '
         'Locate the exact difference between expected and actual values and fix the responsible branch. '
         'Return the FULL corrected Python program in a python code fence. No prose. '
         'Use standard library only, no files/network/processes. Do not hardcode test examples. '
         'solve(data) receives one native JSON object and returns one native JSON object.'},
        {'role': 'user', 'content': canonical({'failing_program': best['code'],
          'failures_to_fix': best['failures'][:3], 'all_tests': tests})}]}
    req = urllib.request.Request(MODEL + '/v1/chat/completions', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=180) as response:
        result = json.load(response)
    choice = result['choices'][0]
    if choice.get('finish_reason') == 'length':
        raise ValueError('Ответ исправляющего шага обрезан')
    content = choice['message']['content'].strip()
    match = re.search(r'```(?:python)?\s*\n(.*?)```', content, re.S)
    code = match.group(1).strip() if match else content
    if len(code) > 18000:
        raise ValueError('Исправление превысило лимит кода')
    ast.parse(code)
    if code.strip() == best['code'].strip():
        return
    path = trial / 'repair.py'
    path.write_text(code)
    from evaluator import evaluate
    previous = os.environ.get('RCP_JOB_DIR')
    os.environ['RCP_JOB_DIR'] = str(trial)
    try:
        evaluate(str(path))
    finally:
        if previous is None:
            os.environ.pop('RCP_JOB_DIR', None)
        else:
            os.environ['RCP_JOB_DIR'] = previous


def run_round(job, config, plan, progress, pending, cycle):
    target_tests = merge_tests(progress['tests'], pending['tests'])
    trial = job / 'rounds' / ('round-%08d' % cycle)
    trial.mkdir(exist_ok=True)
    seed = progress.get('attempt_code') or progress.get('code') or 'def solve(data):\n    return None\n'
    (trial / 'initial.py').write_text(seed)
    store(trial / 'task.json', {'task': config['goal'], 'tests': target_tests})
    system = (BASE + 'Output the ENTIRE replacement Python module in one ```python fenced block, not a diff. '
              'Preserve solve(data), and implement general rules, not a lookup table for examples. '
              'You may add helper functions and classes. Do not read stdin or print. '
              'Do not claim completion: an external evaluator will run the code. '
              'Passing ALL regression tests is mandatory. Then prefer clear, compact code.\n'
              'USER GOAL:\n' + config['goal'] + '\nCURRENT USER PRIORITY (preserve existing tests):\n' + config.get('guidance', '') + '\nFROZEN CONTRACT:\n' + plan['contract'] +
              '\nCURRENT DEVELOPMENT STEP:\n' + pending['title'] + '\n' + pending.get('reason', '') +
              '\nREGRESSION AND NEW TESTS:\n' + canonical(target_tests) +
              '\nACTUAL ERRORS TO FIX FROM PREVIOUS ROUND:\n' + canonical(progress.get('last_result', {}).get('failures', [])) +
              '\nReturn changed code that fixes the reported failures. Do not repeat a known failing implementation.')
    cfg = {'max_iterations': 3, 'checkpoint_interval': 3, 'random_seed': cycle * 43 + 7,
           'language': 'python', 'diff_based_evolution': False, 'max_code_length': 18000,
           'llm': {'api_base': MODEL + '/v1', 'api_key': 'local-only',
                   'models': [{'name': 'rcp-coder', 'weight': 1.0}],
                   'temperature': min(1.1, 0.65 + progress.get('stalled', 0) * 0.08),
                   'max_tokens': 3200, 'timeout': 180, 'retries': 1},
           'prompt': {'system_message': system, 'num_top_programs': 1, 'num_diverse_programs': 1,
                      'include_artifacts': True},
           'database': {'population_size': 24, 'archive_size': 6, 'num_islands': 2},
           'evaluator': {'cascade_evaluation': False, 'parallel_evaluations': 1, 'timeout': 30,
                         'max_retries': 1, 'use_llm_feedback': False}}
    (trial / 'config.yaml').write_text(yaml.safe_dump(cfg, allow_unicode=True))
    update(job, phase='evolving', cycle=cycle, step=pending['title'],
           pending_tests=len(pending['tests']), round_dir=trial.name, last_error=None)
    env = {'PATH': str(ROOT / '.venv/bin') + ':/usr/bin:/bin', 'HOME': str(DATA),
           'LANG': 'C.UTF-8', 'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
           'OPENAI_API_KEY': 'local-only', 'RCP_JOB_DIR': str(trial),
           'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
    command = [str(ROOT / '.venv/bin/openevolve-run'), str(trial / 'initial.py'),
               str(ROOT / 'evaluator.py'), '--config', str(trial / 'config.yaml'),
               '--output', str(trial / 'output')]
    with (trial / 'engine.log').open('w') as log:
        proc = subprocess.Popen(command, cwd=trial, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            rc = proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise RuntimeError('Один раунд превысил 15 минут; эксперимент приостановлен')
    if rc != 0:
        diagnostic = (trial / 'engine.log').read_text(errors='replace')[-1800:]
        raise RuntimeError('OpenEvolve завершился с ошибкой: ' + diagnostic)
    attempts = read_events(trial / 'events.jsonl')
    if not attempts:
        raise RuntimeError('Движок не вернул ни одного проверенного кандидата')
    best = max(attempts, key=lambda x: x['metrics']['combined_score'])
    if best['metrics']['passed'] < len(target_tests) and progress.get('stalled', 0) >= 1:
        update(job, phase='repairing', step='Исправляю конкретные различия в ответах тестов')
        try:
            focused_repair(trial, best, target_tests)
            attempts = read_events(trial / 'events.jsonl')
            best = max(attempts, key=lambda x: x['metrics']['combined_score'])
        except Exception as exc:
            note(job, 'retry', 'Исправляющий шаг не принят: ' + str(exc)[:1000])
    progress['evaluated'] = progress.get('evaluated', 0) + len(attempts)
    progress['cycle'] = cycle
    pending['tries'] += 1
    metrics = best['metrics']
    all_pass = metrics['passed'] == len(target_tests)
    changed = best['code'] != progress.get('code')
    worthwhile = not progress.get('code') or bool(pending['tests']) or (
        changed and len(best['code']) < len(progress['code']))
    accepted = False
    if all_pass and worthwhile and check_code(best['code'], target_tests, job):
        accepted = True
        kind = 'code' if changed else 'coverage'
        version = 'v%08d' % cycle
        snapshot = {'version': version, 'time': time.time(), 'cycle': cycle,
                    'feature': pending['title'], 'kind': kind, 'code': best['code'],
                    'tests': target_tests, 'metrics': metrics, 'parent': progress.get('version')}
        store(job / 'versions' / (version + '.json'), snapshot)
        progress.update(code=best['code'], tests=target_tests, version=version,
                        revisions=progress.get('revisions', 0) + 1,
                        code_changes=progress.get('code_changes', 0) + int(changed),
                        attempt_code=None, pending=None, stalled=0)
        progress['completed'] = (progress.get('completed', []) + [pending['title']])[-30:]
        note(job, kind, pending['title'], cycle=cycle, tests=len(target_tests), version=version)
    else:
        progress['stalled'] = progress.get('stalled', 0) + 1
        progress['attempt_code'] = best['code']
        progress['pending'] = pending
        if progress.get('code') and pending['tries'] >= 3:
            progress['skipped'] = (progress.get('skipped', []) + [pending['title']])[-20:]
            progress.update(pending=None, attempt_code=None)
            note(job, 'retry', 'Шаг отложен после трёх раундов без принятия: ' + pending['title'], cycle=cycle)
        else:
            note(job, 'rejected', 'Рабочая версия сохранена; текущая попытка не принята',
                 cycle=cycle, passed=metrics['passed'], total=len(target_tests))
    finish_priority(job, progress, pending, accepted)
    progress['last_result'] = {'accepted': accepted, 'passed': metrics['passed'],
        'total': len(target_tests), 'failures': best['failures'], 'code': best['code']}
    # This single commit is the authoritative checkpoint, including the old stable code/tests.
    store(job / 'progress.json', progress)
    update(job, phase='cooldown', step='Следующий раунд через 10 секунд',
           tests=len(progress['tests']), revisions=progress.get('revisions', 0),
           evaluated=progress['evaluated'], stalled=progress['stalled'], errors=0)
    clean(job)


def model_ready():
    try:
        with urllib.request.urlopen(MODEL + '/health', timeout=2) as response:
            return json.load(response).get('status') == 'ok'
    except (OSError, ValueError):
        return False


def guidance_status(progress, guidance):
    if not guidance or not guidance.get('text'):
        return 'none'
    revision = guidance.get('revision', 0)
    if progress.get('guidance_consumed_revision', 0) >= revision:
        return 'used'
    if progress.get('guidance_deferred_revision', 0) >= revision:
        return 'deferred'
    return 'queued' if progress.get('guidance_revision', 0) < revision else 'active'


def finish_priority(job, progress, pending, accepted):
    revision = pending.get('guidance_revision', 0)
    if not revision:
        return
    if accepted:
        progress['guidance_consumed_revision'] = max(revision, progress.get('guidance_consumed_revision', 0))
        note(job, 'instruction', 'По текущему приоритету принята версия. Возвращаюсь к общему плану; это не гарантия полного выполнения поручения.')
    elif pending['tries'] >= 3:
        progress['guidance_deferred_revision'] = max(revision, progress.get('guidance_deferred_revision', 0))
        note(job, 'instruction', 'Поручение отложено после трёх неудачных раундов, не выполнено. Можно уточнить его в чате.')


def apply_guidance(job, progress, guidance):
    revision = guidance.get('revision', 0)
    if revision <= progress.get('guidance_revision', 0):
        return False
    progress['guidance_revision'] = revision
    if progress.get('code'):
        progress.update(pending=None, attempt_code=None)
    store(job / 'progress.json', progress)
    note(job, 'instruction', 'Поручение учтено при планировании: ' + guidance.get('text', '')[:500])
    return True


def worker(job):
    config = load(job / 'config.json')
    (job / 'rounds').mkdir(exist_ok=True)
    (job / 'versions').mkdir(exist_ok=True)
    update(job, phase='starting', status='running', step='Загружаю сохранённый прогресс')
    errors = 0
    while load(job / 'control.json', {}).get('desired') == 'running':
        try:
            if shutil.disk_usage(DATA).free < 1536 * 1024 * 1024:
                raise RuntimeError('На диске осталось меньше 1,5 ГБ. Освободи место и продолжи')
            if not model_ready():
                update(job, phase='waiting_model', step='Ожидаю восстановления локальной модели; прогресс сохранён')
                for _ in range(10):
                    if load(job / 'control.json', {}).get('desired') != 'running':
                        break
                    time.sleep(1)
                continue
            guidance = load(job / 'guidance.json', {})
            old_progress = load(job / 'progress.json', {})
            config['guidance'] = guidance.get('text', '') if guidance_status(old_progress, guidance) in ('queued', 'active') else ''
            plan = load(job / 'plan.json') or initial_plan(job, config)
            progress = load(job / 'progress.json')
            if progress is None:
                progress = {'tests': plan['tests'], 'code': None, 'cycle': 0, 'stalled': 0,
                            'pending': {'title': 'Минимальная рабочая версия', 'tests': [], 'tries': 0}}
                store(job / 'progress.json', progress)
            apply_guidance(job, progress, guidance)
            if progress.get('code') and progress.get('pending'):
                try:
                    validate_extension(progress['tests'], progress['pending']['tests'])
                except ValueError as exc:
                    progress.update(pending=None, attempt_code=None)
                    store(job / 'progress.json', progress)
                    update(job, last_error=str(exc))
                    note(job, 'retry', 'Отклонены проверки, нарушающие формат данных: ' + str(exc))
            pending = progress.get('pending') or propose(job, config, plan, progress)
            pending.setdefault('guidance_revision', guidance.get('revision', 0) if config['guidance'] else 0)
            progress['pending'] = pending
            store(job / 'progress.json', progress)
            if load(job / 'control.json', {}).get('desired') != 'running':
                break
            # Never overwrite a half-written round after a restart.
            old_rounds = [int(p.name[6:]) for p in (job / 'rounds').glob('round-*')]
            cycle = max([progress['cycle']] + old_rounds) + 1
            run_round(job, config, plan, progress, pending, cycle)
            errors = 0
            for _ in range(10):
                if load(job / 'control.json', {}).get('desired') != 'running':
                    break
                time.sleep(1)
        except Exception as exc:
            errors += 1
            message = f'{type(exc).__name__}: {exc}'[:2500]
            note(job, 'error', message)
            update(job, errors=errors, phase='retry', step='Повтор после ошибки', last_error=message)
            if errors >= 5 or '15 минут' in message or '1,5 ГБ' in message:
                store(job / 'control.json', {'desired': 'paused'})
                update(job, status='paused', phase='attention', step='Нужна проверка: ' + message[:300])
                return
            for _ in range(15):
                if load(job / 'control.json', {}).get('desired') != 'running':
                    break
                time.sleep(1)
    update(job, status='paused', phase='paused', step='Приостановлено пользователем')


class MissionRequest(BaseModel):
    goal: str = Field(min_length=12, max_length=5000)


class PlaygroundRequest(BaseModel):
    input: Any


def install(svc):
    MISSIONS.mkdir(parents=True, exist_ok=True)

    def location(mid):
        if not re.fullmatch(r'[a-f0-9]{12}', mid) or not (MISSIONS / mid / 'config.json').is_file():
            raise HTTPException(404, 'Проект не найден')
        return MISSIONS / mid

    def watch(job, proc):
        proc.wait()
        svc.stop_process(proc)  # Clean up any descendants even after the worker has exited.
        with svc.LOCK:
            desired = load(job / 'control.json', {}).get('desired')
            if desired == 'running' and not getattr(svc, 'SHUTTING_DOWN', False):
                store(job / 'control.json', {'desired': 'paused'})
                update(job, status='paused', phase='attention', step='Процесс прерван; прогресс сохранён',
                       last_error='Worker exit: ' + str(proc.returncode))
            if svc.ACTIVE['id'] == job.name and svc.ACTIVE['proc'] is proc:
                svc.ACTIVE.update(id=None, proc=None, kind=None)

    def spawn(job):
        # Caller holds the shared lock: classic mode and autopilot cannot race for the model.
        store(job / 'control.json', {'desired': 'running'})
        update(job, status='running', phase='starting', step='Возобновление из контрольной точки')
        env = {'PATH': str(ROOT / '.venv/bin') + ':/usr/bin:/bin', 'HOME': str(DATA),
               'LANG': 'C.UTF-8', 'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
               'RCP_DATA': str(DATA), 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
        try:
            with (job / 'worker.log').open('w') as log:
                proc = subprocess.Popen([str(ROOT / '.venv/bin/python'), str(ROOT / 'autopilot.py'),
                                         str(job)], env=env, cwd=ROOT, stdout=log,
                                         stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            store(job / 'control.json', {'desired': 'paused'})
            update(job, status='paused', phase='attention', last_error=str(exc))
            raise HTTPException(500, 'Не удалось запустить автопилот') from exc
        svc.ACTIVE.update(proc=proc, id=job.name, kind='mission')
        threading.Thread(target=watch, args=(job, proc), daemon=True).start()

    def boot():
        with svc.LOCK:
            for job in sorted(MISSIONS.iterdir()):
                if load(job / 'control.json', {}).get('desired') == 'running':
                    if not svc.busy():
                        spawn(job)
                    else:
                        store(job / 'control.json', {'desired': 'paused'})
                        update(job, status='paused', phase='paused', step='Другой проект уже выполняется')

    svc.autopilot_boot = boot

    @svc.app.get('/autopilot')
    def page():
        return FileResponse(ROOT / 'static/autopilot.html')

    @svc.app.get('/api/missions')
    def listing():
        result = []
        for job in MISSIONS.iterdir():
            config = load(job / 'config.json')
            if config:
                result.append({**config, **load(job / 'state.json', {}),
                               'desired': load(job / 'control.json', {}).get('desired')})
        return sorted(result, key=lambda x: x['created'], reverse=True)[:80]

    @svc.app.post('/api/missions')
    def create(body: MissionRequest):
        with svc.LOCK:
            if svc.busy():
                raise HTTPException(409, 'Сначала приостанови текущий проект или классический запуск')
            job = MISSIONS / secrets.token_hex(6)
            job.mkdir(mode=0o750)
            store(job / 'config.json', {'id': job.name, 'goal': body.goal, 'created': time.time()})
            spawn(job)
        return {'id': job.name}

    @svc.app.get('/api/missions/{mid}')
    def detail(mid: str):
        job = location(mid)
        state = load(job / 'state.json', {})
        trial = job / 'rounds' / str(state.get('round_dir', 'none'))
        attempts = read_events(trial / 'events.jsonl')
        log = ''
        path = trial / 'engine.log'
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 12000))
                log = stream.read().decode(errors='replace')
        return {'config': load(job / 'config.json'), 'state': state,
                'control': load(job / 'control.json'), 'plan': load(job / 'plan.json'),
                'progress': load(job / 'progress.json', {}),
                'history': load(job / 'history.json', []), 'attempts': attempts,
                'versions': [p.stem for p in sorted((job / 'versions').glob('v*.json'), reverse=True)],
                'log': log}

    @svc.app.post('/api/missions/{mid}/pause')
    def pause(mid: str):
        job = location(mid)
        with svc.LOCK:
            store(job / 'control.json', {'desired': 'paused'})
            proc = svc.ACTIVE['proc'] if svc.ACTIVE['id'] == mid else None
            update(job, status='pausing', phase='pausing', step='Останавливаю текущий раунд')
            svc.stop_process(proc)
            if svc.ACTIVE['id'] == mid:
                svc.ACTIVE.update(id=None, proc=None, kind=None)
            update(job, status='paused', phase='paused', step='Приостановлено; рабочая версия сохранена')
        return {'status': 'paused'}

    @svc.app.post('/api/missions/{mid}/resume')
    def resume(mid: str):
        job = location(mid)
        with svc.LOCK:
            if svc.busy():
                raise HTTPException(409, 'Другой процесс ещё использует модель')
            spawn(job)
        return {'status': 'running'}

    @svc.app.get('/api/missions/{mid}/versions/{version}')
    def version_info(mid: str, version: str):
        job = location(mid)
        if not re.fullmatch(r'v[0-9]{8}', version):
            raise HTTPException(404)
        snapshot = load(job / 'versions' / (version + '.json'))
        if snapshot is None:
            raise HTTPException(404, 'Версия вне окна хранения последних 20 снимков')
        return snapshot

    @svc.app.get('/api/missions/{mid}/best.py')
    def download(mid: str):
        progress = load(location(mid) / 'progress.json', {})
        if not progress.get('code'):
            raise HTTPException(404, 'Пока нет принятой версии')
        return Response(progress['code'], media_type='text/plain',
                        headers={'Content-Disposition': 'attachment; filename="project.py"'})

    @svc.app.post('/api/missions/{mid}/try')
    def playground(mid: str, body: PlaygroundRequest):
        job = location(mid)
        progress = load(job / 'progress.json', {})
        if not progress.get('code'):
            raise HTTPException(409, 'Рабочая версия ещё не найдена')
        try:
            if len(canonical(body.input).encode()) > 16000:
                raise ValueError('Вход ограничен 16 КБ')
            from evaluator import sandbox
            with tempfile.TemporaryDirectory(dir=job) as tmp:
                path = Path(tmp) / 'program.py'
                path.write_text(progress['code'])
                result = sandbox(path, [body.input])[0]
            return {'version': progress['version'], 'result': result}
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc


if __name__ == '__main__':
    job = Path(sys.argv[1]).resolve()
    with (job / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        worker(job)

# PLANNER_FIX_V2

# CHAT_GUIDANCE_V1

# SHAPE_FIX_V1

# FOCUSED_REPAIR_V1

# RELIABILITY_V2

# OPERATION_SCHEMA_V2

# DIRECTIVE_FOCUS_V2
