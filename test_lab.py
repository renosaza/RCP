import json
import os
from pathlib import Path
import tempfile
import unittest
import evaluator


class SandboxTests(unittest.TestCase):
    def run_code(self, code, inputs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.py"
            path.write_text(code)
            return evaluator.sandbox(path, inputs)

    def test_function(self):
        result = self.run_code("def solve(x): return x * 2\n", [2, 4])
        self.assertEqual(result, [{"ok": True, "value": 4}, {"ok": True, "value": 8}])

    def test_files_are_hidden(self):
        result = self.run_code('def solve(x):\n    return open("/etc/rcp-lab.env").read()\n', [None])
        self.assertFalse(result[0]["ok"])
        self.assertIn("FileNotFoundError", result[0]["error"])

    def test_no_gpu_in_sandbox(self):
        result = self.run_code('import os\ndef solve(x): return os.path.exists("/dev/nvidia0")\n', [None])
        self.assertFalse(result[0]["value"])

    def test_no_lan_network(self):
        result = self.run_code('import socket\ndef solve(x):\n    s=socket.socket();s.settimeout(0.5)\n    s.connect(("192.0.2.1",22))\n    return True\n', [None])
        self.assertFalse(result[0]["ok"])

    def test_timeout(self):
        with self.assertRaises(RuntimeError):
            self.run_code("def solve(x):\n    while True: pass\n", [1])

    def test_comparison(self):
        self.assertFalse(evaluator.equal(True, 1))
        self.assertTrue(evaluator.equal([1, {"a": 0.1 + 0.2}], [1, {"a": 0.3}]))
        self.assertFalse(evaluator.equal([1, 2], [1]))

    def test_score_is_external(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "task.json").write_text(json.dumps({"tests": [{"input": 3, "expected": 6}]}))
            code = job / "candidate.py"
            code.write_text('def solve(x): return {"combined_score": 1, "passed": 100}\n')
            os.environ["RCP_JOB_DIR"] = str(job)
            result = evaluator.evaluate(code)
            self.assertEqual(result.metrics["passed"], 0)
            self.assertEqual(result.metrics["combined_score"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
