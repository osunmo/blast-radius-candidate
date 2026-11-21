import json
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.main import main


class EntrypointTests(unittest.TestCase):
    def test_time_budget_finishes_remaining_commits_with_full_schedule(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "commits").mkdir()
            for name in ("a", "b", "c"):
                (root / "commits" / (name + ".patch")).write_text("synthetic", encoding="utf-8")
            (root / "tests.json").write_text(json.dumps({"tests": [
                {"number": 0, "id": "tests/test_x.py::test_a", "file": "tests/test_x.py", "seconds": .1}
            ]}), encoding="utf-8")
            out = io.StringIO()
            with patch.object(sys, "argv", ["entry", "--data", folder, "--artifacts", folder,
                                           "--mode", "hybrid"]), \
                 patch("src.main.time.monotonic", side_effect=[0, 1, 141, 142]), \
                 patch("src.selector.Selector.select", return_value=set()) as select, \
                 contextlib.redirect_stdout(out):
                main()
            self.assertEqual(select.call_count, 1)
            self.assertEqual([json.loads(s)["workers"] for s in out.getvalue().splitlines()],
                             [[], [[0]], [[0]]])

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
