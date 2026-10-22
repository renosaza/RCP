"""Small local task launcher around the installed OpenEvolve engine."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from typing import Any
import yaml

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("RCP_DATA", "/var/lib/rcp-lab"))
RUNS = DATA / "runs"
MODEL = "http://127.0.0.1:8081"
PASSWORD = os.environ.get("RCP_PASSWORD")
if not PASSWORD:
    raise RuntimeError("Set RCP_PASSWORD before starting the service")
SHUTTING_DOWN = False
LOCK = threading.Lock()
ACTIVE = {"proc": None, "id": None, "preparing": False}
security = HTTPBasic()


def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    valid_user = secrets.compare_digest(credentials.username.encode(), os.environ["RCP_USERNAME"].encode())
    valid_pass = secrets.compare_digest(credentials.password.encode(), PASSWORD.encode())
    if not (valid_user and valid_pass):
        raise HTTPException(401, "Неверный логин или пароль", headers={"WWW-Authenticate": "Basic"})


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
    temporary.replace(path)


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def stop_process(proc):
    if not proc:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        if proc.poll() is None:
            proc.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if proc.poll() is None:
            proc.wait(timeout=3)


@asynccontextmanager
async def lifespan(app):
    global SHUTTING_DOWN
    SHUTTING_DOWN = False
    RUNS.mkdir(parents=True, exist_ok=True)
    for path in RUNS.glob("*/state.json"):
        state = read(path, {})
        if state.get("status") == "running":
            save(path, {**state, "status": "interrupted", "finished": time.time()})
    autopilot_boot()
    world_boot()
    chat_boot()
    try:
        yield
    finally:
        SHUTTING_DOWN = True
        chat_shutdown()
        stop_process(ACTIVE["proc"])


app = FastAPI(title="RCP Lab", docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(authenticate)], lifespan=lifespan)


@app.middleware("http")
async def boundaries(request: Request, call_next):
    if request.method == "POST":
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"detail": "Нужен application/json"}, status_code=415)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 131072:
                return JSONResponse({"detail": "Запрос больше 128 КиБ"}, status_code=413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    return response


class TestCase(BaseModel):
    input: Any
    expected: Any


class Description(BaseModel):
    task: str = Field(min_length=8, max_length=6000)


class Task(Description):
    tests: list[TestCase] = Field(min_length=1, max_length=50)
    iterations: int = Field(default=5, ge=1, le=30)
    initial: str = Field(default="def solve(data):\n    return None\n", max_length=12000)


def validate_tests(tests):
    seen = {}
    try:
        encoded = json.dumps(tests, allow_nan=False, ensure_ascii=False)
        if len(encoded.encode()) > 64000:
            raise ValueError("Тесты занимают больше 64 КиБ")
        for test in tests:
            key = json.dumps(test["input"], sort_keys=True, allow_nan=False)
            expected = json.dumps(test["expected"], sort_keys=True, allow_nan=False)
            if key in seen and seen[key] != expected:
                raise ValueError("Одинаковому входу заданы разные ответы")
            seen[key] = expected
    except (ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(422, str(exc)) from exc


def model_request(messages):
    payload = {"model": "rcp-coder", "messages": messages, "temperature": 0.2,
               "max_tokens": 1800, "response_format": {"type": "json_object"}}
    request = urllib.request.Request(MODEL + "/v1/chat/completions",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)["choices"][0]["message"]["content"]


def busy():
    return ACTIVE["preparing"] or (ACTIVE["proc"] is not None and ACTIVE["proc"].poll() is None)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "autopilot.html")


@app.get("/api/health")
def health():
    try:
        with urllib.request.urlopen(MODEL + "/health", timeout=2) as response:
            ready = json.load(response).get("status") == "ok"
    except Exception:
        ready = False
    return {"model_ready": ready, "model": "Qwen3.8-27B UD-Q4_K_M",
            "engine": "OpenEvolve 0.4.0", "busy": busy(),
            "active_id": ACTIVE["id"] if ACTIVE.get("kind") == "classic" else None,
            "active_mission": ACTIVE["id"] if ACTIVE.get("kind") == "mission" else None,
            "active_world": ACTIVE["id"] if ACTIVE.get("kind") == "world" else None}


@app.get("/api/presets")
def presets():
    return read(ROOT / "presets.json", [])


@app.post("/api/prepare")
def prepare(body: Description):
    with LOCK:
        if busy():
            raise HTTPException(409, "Сначала дождись окончания или останови текущий запуск")
        ACTIVE["preparing"] = True
    try:
        content = model_request([
            {"role": "system", "content": "Create test examples for a pure Python function solve(data). The function accepts ONE JSON value and returns ONE JSON value. Return ONLY a JSON object with keys tests (array of 6 to 10 objects with input and expected), note (brief Russian explanation of the chosen input/output format). Include empty and boundary cases where meaningful. Compute correct expected answers. No code, files, network, or external libraries. If the request cannot be expressed as a pure JSON function, return an empty tests array and explain the limitation in note. Test generation can be wrong; the user will review it."},
            {"role": "user", "content": body.task}])
        result = json.loads(content)
        raw_tests = result.get("tests", [])
        if not raw_tests:
            raise HTTPException(422, result.get("note", "Задай задачу для функции: вход → выход"))
        tests = [TestCase.model_validate(t).model_dump() for t in raw_tests]
        if not 1 <= len(tests) <= 50:
            raise ValueError("Нужно от 1 до 50 тестов")
        validate_tests(tests)
        return {"tests": tests, "note": str(result.get("note", ""))[:2000], "draft": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Не удалось подготовить тесты: {type(exc).__name__}: {exc}") from exc
    finally:
        with LOCK:
            ACTIVE["preparing"] = False


def monitor(job, proc):
    timed_out = False
    try:
        proc.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_process(proc)
    with LOCK:
        state = read(job / "state.json", {})
        if state.get("status") != "cancelled":
            state["status"] = "timeout" if timed_out else ("completed" if proc.returncode == 0 else "failed")
        state.update(finished=time.time(), exit_code=proc.returncode)
        save(job / "state.json", state)
        if ACTIVE["id"] == job.name:
            ACTIVE["id"] = None
            ACTIVE["proc"] = None


@app.post("/api/jobs")
def launch(body: Task):
    task = body.model_dump()
    validate_tests(task["tests"])
    with LOCK:
        if busy():
            raise HTTPException(409, "Уже выполняется задача. Сначала останови её")
        job = RUNS / uuid.uuid4().hex[:12]
        job.mkdir(mode=0o750)
        task.update(id=job.name, created=time.time())
        save(job / "task.json", task)
        (job / "initial.py").write_text(body.initial or "def solve(data):\n    return None\n")
        system = ("You evolve Python 3.12 programs. Implement a pure function solve(data), accepting exactly ONE JSON value and returning a JSON-serializable result. Standard library only. No files, networking, shell commands or third-party packages. Do not read stdin or print; the external harness calls solve. Preserve the function signature. Correctness on ALL tests is the priority, then shorter clear code. Output the entire replacement Python program in a ```python code block, not a diff.\nTASK:\n" + body.task + "\nTEST EXAMPLES:\n" + json.dumps(task["tests"], ensure_ascii=False))
        config = {"max_iterations": body.iterations, "checkpoint_interval": 1, "random_seed": 42,
            "language": "python", "diff_based_evolution": False, "max_code_length": 12000,
            "llm": {"api_base": MODEL + "/v1", "api_key": "local-only", "models": [{"name": "rcp-coder", "weight": 1.0}], "temperature": 0.7, "max_tokens": 2048, "timeout": 180, "retries": 1},
            "prompt": {"system_message": system, "num_top_programs": 1, "num_diverse_programs": 1, "include_artifacts": True},
            "database": {"population_size": 40, "archive_size": 10, "num_islands": 2},
            "evaluator": {"cascade_evaluation": False, "parallel_evaluations": 1, "timeout": 30, "max_retries": 1, "use_llm_feedback": False}}
        (job / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True))
        command = [str(ROOT / ".venv/bin/openevolve-run"), str(job / "initial.py"),
                   str(ROOT / "evaluator.py"), "--config", str(job / "config.yaml"),
                   "--output", str(job / "output")]
        env = {"PATH": str(ROOT / ".venv/bin") + ":/usr/bin:/bin", "HOME": str(DATA),
               "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1", "OPENAI_API_KEY": "local-only",
               "RCP_JOB_DIR": str(job), "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
        try:
            with (job / "engine.log").open("w") as log:
                proc = subprocess.Popen(command, cwd=job, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            save(job / "state.json", {"status": "failed", "error": str(exc)})
            raise HTTPException(500, "Не удалось запустить OpenEvolve") from exc
        save(job / "state.json", {"status": "running", "started": time.time()})
        ACTIVE.update(proc=proc, id=job.name, kind="classic")
        threading.Thread(target=monitor, args=(job, proc), daemon=True).start()
    return {"id": job.name}


def job_path(job_id):
    if not re.fullmatch(r"[a-f0-9]{12}", job_id) or not (RUNS / job_id / "task.json").is_file():
        raise HTTPException(404, "Запуск не найден")
    return RUNS / job_id


def events(job):
    result = []
    path = job / "events.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # A concurrent evaluator may still be appending the last line.
    return result


@app.get("/api/jobs")
def jobs():
    output = []
    for job in sorted(RUNS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:60]:
        task = read(job / "task.json")
        if task:
            output.append({"id": job.name, "task": task["task"], "created": task["created"],
                           **read(job / "state.json", {})})
    return output


@app.get("/api/jobs/{job_id}")
def details(job_id: str):
    job = job_path(job_id)
    attempts = events(job)
    best = max(attempts, key=lambda e: e["metrics"]["combined_score"], default=None)
    log_path = job / "engine.log"
    log = ""
    if log_path.exists():
        with log_path.open("rb") as stream:
            stream.seek(max(0, log_path.stat().st_size - 18000))
            log = stream.read().decode(errors="replace")
    return {"task": read(job / "task.json"), "state": read(job / "state.json", {}),
            "events": attempts, "best": best, "log": log}


@app.post("/api/jobs/{job_id}/stop")
def stop(job_id: str):
    job = job_path(job_id)
    with LOCK:
        if ACTIVE["id"] != job_id or not ACTIVE["proc"] or ACTIVE["proc"].poll() is not None:
            raise HTTPException(409, "Этот запуск уже не выполняется")
        save(job / "state.json", {**read(job / "state.json", {}), "status": "cancelled", "finished": time.time()})
        proc = ACTIVE["proc"]
    stop_process(proc)
    return {"status": "cancelled"}


@app.get("/api/jobs/{job_id}/best.py")
def download(job_id: str):
    best = max(events(job_path(job_id)), key=lambda e: e["metrics"]["combined_score"], default=None)
    if not best:
        raise HTTPException(404, "Пока нет оценённых вариантов")
    return Response(best["code"], media_type="text/plain", headers={"Content-Disposition": 'attachment; filename="best_program.py"'})


@app.get("/classic")
def classic_page():
    return FileResponse(ROOT / "static/index.html")

import sys
import autopilot
autopilot.install(sys.modules[__name__])


@app.get("/chat.js")
def chat_script():
    return FileResponse(ROOT / "static/chat.js", media_type="application/javascript")

import chat
chat.install(sys.modules[__name__])

import worlds
worlds.install(sys.modules[__name__])
