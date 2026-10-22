"""Executed inside the restricted bubblewrap filesystem, never on the host."""
import contextlib
import importlib.util
import json
import sys

inputs = json.load(sys.stdin)
with contextlib.redirect_stdout(sys.stderr):
    spec = importlib.util.spec_from_file_location("candidate", "/work/program.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = []
    for item in inputs:
        try:
            value = module.solve(item)
            json.dumps(value, allow_nan=False)
            results.append({"ok": True, "value": value})
        except Exception as exc:
            results.append({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:1000]})
json.dump(results, sys.stdout, allow_nan=False, ensure_ascii=False)
