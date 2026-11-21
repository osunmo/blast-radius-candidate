"""Refine baseline Python spawn-worker dependencies using verified child reports."""

import argparse
import ast
import atexit
import concurrent.futures
import gzip
import hashlib
import json
import multiprocessing
import multiprocessing.process
import multiprocessing.util
import os
import platform
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from build_maps import BASE, Collector, compact, prepare_source, report_encoding


def trace_child():
    """Invoked by the generated sitecustomize only during a registered spawn."""
    target = Path(os.environ.pop("BLAST_CHILD_REPORT"))
    source = Path(os.environ["BLAST_CHILD_SOURCE"])
    if any(name == "sqlglot" or name.startswith("sqlglot.") for name in sys.modules):
        return  # Import-time dependencies escaped observation; leave no report.
    tracker = Collector(source, {"child": 0})
    tracker.group = 1
    tracker.start()

    def finish():
        if not tracker.active:
            return
        tracker.stop()
        # Reject descendants, including other Python processes. They would
        # require their own identity/attribution/success handshake.
        result = {
            "pid": os.getpid(), "base": BASE, "nested_process": bool(tracker.opaque),
            "lines": {p: sorted(lines) for p, lines in tracker.observed.items()},
            "global": {p: sorted(lines) for p, lines in tracker.contextless.items()},
            "reads": sorted(tracker.reads),
        }
        target.write_bytes(gzip.compress(compact(result), compresslevel=1, mtime=0))

    # Windows/POSIX spawn can exit via different finalization paths.
    atexit.register(finish)
    multiprocessing.util.Finalize(None, finish, exitpriority=-999)


class Replay(Collector):
    def __init__(self, source, ids, directory):
        super().__init__(source, ids)
        self.directory = directory
        self.source = source
        self.started = {}
        self.foreign = 0
        self.spawning = threading.local()
        self.pool_owner = {}
        self.hook = directory / "hook"
        self.hook.mkdir()
        (self.hook / "sitecustomize.py").write_text(
            "import os\nif 'BLAST_CHILD_REPORT' in os.environ:\n"
            "    from refine_children import trace_child\n    trace_child()\n",
            encoding="utf-8",
        )

    def audit(self, event, args):
        if self.active and event in (
            "subprocess.Popen", "os.fork", "os.forkpty", "os.system",
            "os.posix_spawn", "os.exec", "_winapi.CreateProcess", "_posixsubprocess.fork_exec",
        ) and not getattr(self.spawning, "expected", False):
            self.foreign |= self.group or self.all
        super().audit(event, args)

    def launch(self, fn, proc):
        group = self.group
        # Only the standard library's spawned interpreter has the lifecycle
        # validated below. Record fork/forkserver/custom launches as opaque.
        if type(proc) is not multiprocessing.get_context("spawn").Process or group.bit_count() != 1:
            self.foreign |= group or self.all
            return fn(proc)
        target = self.directory / (uuid.uuid4().hex + ".json.gz")
        search = [str(self.hook), str(Path(__file__).resolve().parent), str(self.source)]
        if os.environ.get("PYTHONPATH"):
            search.append(os.environ["PYTHONPATH"])
        env = {"PYTHONPATH": os.pathsep.join(search), "BLAST_CHILD_REPORT": str(target),
               "BLAST_CHILD_SOURCE": str(self.source)}
        with patch.dict(os.environ, env):
            self.spawning.expected = True
            try:
                result = fn(proc)
            finally:
                self.spawning.expected = False
        self.started.setdefault(group, []).append((proc, proc.pid, target))
        return result

    def on_start(self, code, offset):
        # This verifier tracks concurrent.futures executor ownership. Other
        # shared pool APIs are intentionally opaque through the second pass.
        if code.co_qualname.startswith("Pool.") and code.co_filename.replace("\\", "/").endswith("/multiprocessing/pool.py"):
            self.foreign |= self.group or self.all
        return super().on_start(code, offset)


def merge_child(payload, records, bit):
    for record in records:
        for path, lines in record["lines"].items():
            dest = payload["files"].setdefault(path, {"lines": {}, "global": []})["lines"]
            for line in lines:
                key = str(line)
                dest[key] = format(int(dest.get(key, "0"), 16) | bit, "x")
        for path, lines in record["global"].items():
            dest = payload["files"].setdefault(path, {"lines": {}, "global": []})
            dest["global"] = sorted(set(dest["global"]) | set(lines))
        for path in record["reads"]:
            dest = payload["fixtures"]
            dest[path] = format(int(dest.get(path, "0"), 16) | bit, "x")


def submission_contexts(source, payload):
    """Cross-check per-item task submissions, including already running pools."""
    groups = 0
    for path, coverage in payload["files"].items():
        if not path.endswith(".py"):
            continue
        text = (source / path).read_text(encoding="utf-8")
        if "ProcessPoolExecutor" not in text and "multiprocessing" not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"submit", "map", "map_async", "apply", "apply_async", "imap", "imap_unordered"}:
                continue
            for line in range(node.lineno, node.end_lineno + 1):
                groups |= int(coverage["lines"].get(str(line), "0"), 16)
                if line in coverage["global"]:
                    groups |= (1 << len(payload["catalog"])) - 1
    return groups


def gather(args):
    out = args.out.resolve()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))["tests"]
    ids = {row["id"]: row["number"] for row in catalog}
    blob = (out / "coverage.json.gz").read_bytes()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    payload = json.loads(gzip.decompress(blob))
    for record in payload["files"].values():
        record["lines"].pop("0", None)
        record["global"] = [ln for ln in record["global"] if ln > 0]
    if (manifest["map_sha256"] != hashlib.sha256(blob).hexdigest() or
        manifest["base"] != BASE or payload["catalog"] != list(ids) or
        manifest["python"] != sys.version or manifest["platform"] != platform.platform()):
        raise ValueError("Refinement requires original map, catalog and interpreter platform")
    if "always_include" in payload and int(payload["unmapped"], 16):
        raise ValueError("Unresolved collection-wide uncertainty")
    source = prepare_source(args)
    already_opaque = set(manifest["opaque_child_process"])
    submitters = submission_contexts(source, payload)
    already_opaque.update(n for n in ids if submitters & (1 << ids[n]))
    manifest["opaque_child_process"] = sorted(already_opaque)
    bad = int(payload.get("always_include", payload["unmapped"]), 16) | submitters
    # A previous schema revision stored known, isolated untrusted contexts
    # in unmapped. A successfully generated map accounted for every test.
    payload["unmapped"] = "0"
    manifest["unmapped_count"] = 0
    targets = sorted(n for n in already_opaque if n not in manifest["nonpassing_or_missing_phase"] and bad & (1 << ids[n]))
    os.chdir(source)
    sys.path.insert(0, str(source))
    os.environ.update(SKIP_INTEGRATION="0", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    resolved = {}
    original = multiprocessing.process.BaseProcess.start
    init_pool = concurrent.futures.ProcessPoolExecutor.__init__
    submit_pool = concurrent.futures.ProcessPoolExecutor.submit
    with tempfile.TemporaryDirectory(prefix="blast-children-") as directory:
        # The first test to trigger a lazy import can hide helper dependencies
        # from subsequent tests. Mark transitive import-time execution globally.
        import_path = Path(directory) / "imports.json"
        subprocess.run([sys.executable, str(Path(__file__).with_name("collect_imports.py")),
                        str(source), str(import_path)], check=True)
        imports = json.loads(import_path.read_text(encoding="utf-8"))
        merge_child(payload, [{"lines": {}, "global": imports["lines"], "reads": imports["reads"]}],
                    (1 << len(ids)) - 1)
        manifest["import_sweep"] = {k: imports[k] for k in ("modules", "import_errors", "opaque")}
        if imports["opaque"]:
            raise ValueError("An untracked child process started during the import sweep")
        replay = Replay(source, ids, Path(directory))
        def start(process):
            return replay.launch(original, process)
        def initialize(pool, *args, **kwargs):
            init_pool(pool, *args, **kwargs)
            replay.pool_owner[pool] = replay.group
        def submit(pool, *args, **kwargs):
            owner = replay.pool_owner.get(pool, 0)
            if owner != replay.group or owner.bit_count() != 1:
                replay.foreign |= replay.group or replay.all
                replay.foreign |= owner
            return submit_pool(pool, *args, **kwargs)
        replay.start()
        try:
            with (patch.object(multiprocessing.process.BaseProcess, "start", start),
                  patch.object(concurrent.futures.ProcessPoolExecutor, "__init__", initialize),
                  patch.object(concurrent.futures.ProcessPoolExecutor, "submit", submit)):
                status = (pytest.main([*targets, "-q", "--tb=line", "-p", "no:cacheprovider"], plugins=[replay])
                          if targets else pytest.ExitCode.OK)
        finally:
            replay.stop()
        if status not in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED) or replay.collection_errors:
            raise RuntimeError("Targeted child collection incomplete")
        for name in targets:
            bit = 1 << ids[name]
            expected = replay.started.get(bit, [])
            if not expected or bit & replay.foreign or name in replay.nonpassing or replay.passed_phases[name] != {"call", "setup", "teardown"}:
                continue
            records = []
            for proc, pid, file in expected:
                try:
                    exitcode = proc.exitcode
                except ValueError:  # Closed process handle means status is unavailable.
                    exitcode = None
                if exitcode != 0 or not file.is_file():
                    break
                record = json.loads(gzip.decompress(file.read_bytes()))
                if record["pid"] != pid or record["base"] != BASE or record["nested_process"]:
                    break
                if not record["lines"] or not any(p.startswith("sqlglot/") for p in record["lines"]):
                    break
                records.append(record)
            if len(records) == len(expected):
                parent = {"lines": {p: [k for k,v in d.items() if v & bit] for p,d in replay.observed.items()},
                          "global": replay.contextless, "reads": [p for p,v in replay.reads.items() if v & bit]}
                merge_child(payload, [parent, *records], bit)
                resolved[name] = len(records)
                bad &= ~bit
    payload["always_include"] = format(bad, "x")
    raw = compact(payload)
    encoded = gzip.compress(raw, compresslevel=6, mtime=0)
    manifest.update(child_reports_verified=resolved, always_include_count=bad.bit_count(),
                    map_sha256=hashlib.sha256(encoded).hexdigest(), json_bytes=len(raw), gzip_bytes=len(encoded),
                    files_with_coverage=len(payload["files"]), recorded_reads=len(payload["fixtures"]),
                    global_lines=sum(len(v["global"]) for v in payload["files"].values()),
                    attributed_lines=sum(len(v["lines"]) for v in payload["files"].values()))
    if args.compare_encodings or "line_matrix_encoding_comparison" in manifest:
        manifest["line_matrix_encoding_comparison"] = report_encoding(payload["files"])
    (out / "coverage.json.gz").write_bytes(encoded)
    (out / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"Verified complete child coverage for {len(resolved)} tests", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--archive", type=Path)
    ap.add_argument("--catalog", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--compare-encodings", action="store_true")
    gather(ap.parse_args())
