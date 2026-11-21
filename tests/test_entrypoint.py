import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class EntrypointTests(unittest.TestCase):
    def test_uncertain_inputs_emit_one_assignment_each(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "commits").mkdir()
            (data / "tests.json").write_text(json.dumps({"tests": [
                {"number": 0, "id": "tests/test_x.py::test_x", "file": "tests/test_x.py", "seconds": .001}
            ]}), encoding="utf-8")
            (data / "commits" / "broken.patch").write_bytes(b"\xff")
            (data / "commits" / "missing.patch").write_text("unparseable", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", "-m", "src.main", "--mode", "hybrid", "--data", folder,
                 "--artifacts", folder, "--source", folder], capture_output=True, check=True,
                cwd=Path(__file__).resolve().parents[1]
            )
            self.assertEqual(result.stderr, b"")
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],
                             [{"commit": "broken", "workers": [[0]]},
                              {"commit": "missing", "workers": [[0]]}])
