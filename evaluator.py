"""Trusted evaluator: candidate code never runs in this Python process."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import uuid

from openevolve.evaluation_result import EvaluationResult

ROOT = Path(__file__).resolve().parent


def equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-9)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def sandbox(program_path, inputs, timeout=5):
    command = [
        "/usr/bin/prlimit", "--as=268435456", "--cpu=3", "--nproc=64",
        "--nofile=64", "--fsize=1048576", "--core=0", "--",
        "/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session",
        "--cap-drop", "ALL", "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--dev", "/dev", "--size", "67108864", "--tmpfs", "/tmp",

        "--dir", "/work", "--ro-bind", str(Path(program_path).resolve()), "/work/program.py",
        "--ro-bind", str(ROOT / "runner.py"), "/work/runner.py",
        "--chdir", "/work", "--clearenv", "--setenv", "PATH", "/usr/bin",
        "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONHASHSEED", "0",
        "/usr/bin/python3", "-I", "-B", "/work/runner.py",
    ]
    # File-backed output avoids an unbounded pipe from a generated program.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=errors,
                                start_new_session=True, env={"PATH": "/usr/bin:/bin"})
        try:
            proc.communicate(json.dumps(inputs, allow_nan=False).encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise RuntimeError("Превышен лимит времени: 5 секунд")
        output.seek(0)
        errors.seek(0)
        raw = output.read(1048577)
        diagnostic = errors.read(4000).decode(errors="replace")
        if proc.returncode:
            raise RuntimeError(f"Sandbox exit {proc.returncode}: {diagnostic[-2000:]}")
        if len(raw) > 1048576:
            raise RuntimeError("Ответ программы превышает 1 МиБ")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError(f"Программа не вернула корректный JSON: {diagnostic[-1000:]}") from exc
        if not isinstance(result, list) or len(result) != len(inputs):
            raise RuntimeError("Неверное количество результатов")
        return result


def evaluate(program_path):
    job = Path(os.environ["RCP_JOB_DIR"])
    tests = json.loads((job / "task.json").read_text())["tests"]
    code = Path(program_path).read_text()
    start = time.monotonic()
    passed, failures = 0, []
    try:
        results = sandbox(program_path, [t["input"] for t in tests])
        for index, (result, test) in enumerate(zip(results, tests)):
            if isinstance(result, dict) and result.get("ok") is True and equal(result.get("value"), test["expected"]):
                passed += 1
            elif len(failures) < 5:
                failures.append({"test": index + 1, "input": test["input"],
                                 "expected": test["expected"], "result": result})
    except Exception as exc:
        failures = [{"error": f"{type(exc).__name__}: {exc}"[:3000]}]
    correctness = passed / len(tests)
    # Test correctness dominates; compactness only breaks close ties. Not a speed benchmark.
    score = 0.99 * correctness + 0.01 / (1 + len(code) / 500) if passed else 0.0
    metrics = {"combined_score": score, "correctness": correctness,
               "passed": passed, "total": len(tests), "code_length": len(code)}
    event = {"id": uuid.uuid4().hex[:12], "time": time.time(), "code": code,
             "metrics": metrics, "elapsed_ms": round((time.monotonic() - start) * 1000, 1),
             "failures": failures}
    with (job / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
    return EvaluationResult(metrics=metrics, artifacts={"test_feedback": json.dumps(failures, ensure_ascii=False)})
