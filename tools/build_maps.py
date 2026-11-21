"""Collect baseline dependencies. Run in a fresh Python 3.13 process."""

import argparse
import collections
import functools
import gzip
import hashlib
import importlib.metadata
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tarfile
import time
import zlib
from pathlib import Path

import pytest


BASE = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
MON = sys.monitoring
TOOL = MON.COVERAGE_ID


class Collector:
    def __init__(self, source, ids, explain_processes=False):
        self.root = os.path.normcase(str(source)) + os.sep
        self.ids = ids
        self.all = (1 << len(ids)) - 1
        self.group = 0
        self.in_protocol = False
        self.observed = collections.defaultdict(dict)
        self.contextless = collections.defaultdict(set)
        self.reads = collections.defaultdict(int)
        self.passed_phases = collections.defaultdict(set)
        self.nonpassing = set()
        self.opaque = 0
        self.global_process_events = set()
        self.explain_processes = explain_processes
        self.collection_errors = 0
        self.collected = set()
        self.class_groups = collections.defaultdict(int)
        self.file_groups = collections.defaultdict(int)
        self.active = True
        self.read_callers = {}

    @functools.cache
    def relative(self, absolute):
        normalized = os.path.normcase(os.path.normpath(absolute))
        if normalized.startswith(self.root):
            return os.path.relpath(absolute, self.root).replace(os.sep, "/")
        return None

    def on_start(self, code, offset):
        name = code.co_filename
        # Windows' spawn implementation need not emit os.fork or Popen.
        if code.co_qualname in {"BaseProcess.start", "ProcessPoolExecutor.submit", "Pool._map_async", "Pool.apply_async", "Pool.imap", "Pool.imap_unordered"}:
            filename = name.replace("\\", "/")
            if filename.endswith(("/multiprocessing/process.py", "/concurrent/futures/process.py", "/multiprocessing/pool.py")):
                self.opaque |= self.group or self.all
        if os.path.isabs(name) and self.relative(name):
            MON.set_local_events(TOOL, code, MON.events.LINE)
        return MON.DISABLE

    def on_line(self, code, line):
        if line <= 0:  # CPython's synthetic resume position for empty modules
            return MON.DISABLE
        path = self.relative(code.co_filename)
        if self.group and code.co_flags & 1:
            dest = self.observed[path]
            dest[line] = dest.get(line, 0) | self.group
        # A class or module body may be evaluated just once, under a test
        # that imported it first. Preserve the uncertainty for later users.
        if not self.group or not (code.co_flags & 1):
            self.contextless[path].add(line)
        return MON.DISABLE

    def start(self):
        sys.addaudithook(self.audit)
        MON.use_tool_id(TOOL, "blast-radius baseline")
        MON.register_callback(TOOL, MON.events.PY_START, self.on_start)
        MON.register_callback(TOOL, MON.events.LINE, self.on_line)
        MON.set_events(TOOL, MON.events.PY_START)

    def audit(self, event, args):
        if not self.active:
            return
        if event in ("subprocess.Popen", "os.fork", "os.forkpty", "os.posix_spawn",
                     "os.system", "os.exec", "_winapi.CreateProcess", "_posixsubprocess.fork_exec"):
            self.opaque |= self.group or self.all
            if not self.group or self.explain_processes:
                frame = sys._getframe(1)
                callers = []
                while frame is not None and len(callers) < 8:
                    callers.append(f"{frame.f_globals.get('__name__', '?')}.{frame.f_code.co_name}")
                    frame = frame.f_back
                self.global_process_events.add((event, *callers))
        if event != "open" or not isinstance(args[0], (str, bytes)):
            return
        name, mode, flags = args
        if flags & (os.O_WRONLY | os.O_RDWR) == os.O_WRONLY:
            return
        path = self.relative(os.path.abspath(os.fsdecode(name)))
        if not path or path.startswith((".git/", ".pytest_cache/")) or path.endswith((".pyc", ".pyo")):
            return
        # Bytecode/source opens by the import system represent code loading,
        # not user-level reads of the program's text. These have a separate
        # signal in the contextless line map.
        frame = sys._getframe(1)
        while frame and frame.f_code.co_name == 'audit' and isinstance(frame.f_locals.get('self'), Collector):
            frame = frame.f_back
        caller = frame.f_code.co_filename if frame else ''
        if path.endswith(".py") and caller.startswith("<frozen importlib"):
            return
        self.reads[path] |= self.group or self.all
        if path.endswith('.py') and path not in self.read_callers:
            frame = sys._getframe(1)
            callers = []
            for _ in range(5):
                if frame is None:
                    break
                callers.append(frame.f_code.co_filename + ':' + frame.f_code.co_name)
                frame = frame.f_back
            self.read_callers[path] = callers

    def switch(self, group):
        self.group = group
        MON.restart_events()

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items):
        self.collected = {item.nodeid for item in items}
        for item in items:
            if item.nodeid in self.ids:
                bit = 1 << self.ids[item.nodeid]
                self.class_groups[item.nodeid.rpartition("::")[0]] |= bit
                self.file_groups[item.nodeid.partition("::")[0]] |= bit
        items[:] = [item for item in items if item.nodeid in self.ids]

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors += 1

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        self.in_protocol = True
        self.switch(1 << self.ids[item.nodeid])
        try:
            return (yield)
        finally:
            self.in_protocol = False
            self.switch(0)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_fixture_setup(self, fixturedef, request):
        old = self.group
        scope = fixturedef.scope
        if self.in_protocol and scope != "function":
            if scope == "class":
                self.switch(self.class_groups.get(request.node.nodeid, self.all))
            elif scope == "module":
                self.switch(self.file_groups.get(request.node.nodeid, self.all))
            else:
                self.switch(self.all)
        try:
            return (yield)
        finally:
            if old != self.group:
                self.switch(old)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item, nextitem):
        old = self.group
        # A finalizer can affect all consumers of a shared fixture. Attribute
        # group-boundary teardown broadly, including package/session fixtures.
        if nextitem is None or item.parent is not nextitem.parent:
            self.switch(self.all)
        try:
            return (yield)
        finally:
            if self.group != old:
                self.switch(old)

    def pytest_runtest_logreport(self, report):
        if report.passed:
            self.passed_phases[report.nodeid].add(report.when)
        else:
            self.nonpassing.add(report.nodeid)

    def stop(self):
        self.active = False
        MON.set_events(TOOL, 0)
        MON.free_tool_id(TOOL)


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def report_encoding(files):
    """Measure the line matrix alone, streaming both encodings into DEFLATE."""
    @functools.lru_cache(maxsize=2048)
    def id_list(mask):
        bits = int(mask, 16)
        numbers = []
        while bits:
            first = bits & -bits
            numbers.append(first.bit_length() - 1)
            bits ^= first
        return compact(numbers)

    totals = []
    for lists in (False, True):
        comp = zlib.compressobj(6)
        plain_size = compressed = 0
        for path, file in sorted(files.items()):
            for line, mask in file["lines"].items():
                prefix = compact([path, line])[:-1] + b","
                row = prefix + (id_list(mask) if lists else compact(mask)) + b"]"
                plain_size += len(row)
                compressed += len(comp.compress(row))
        totals.append({"raw_bytes": plain_size, "deflate_bytes": compressed + len(comp.flush())})
    return dict(zip(("hex_bitsets", "integer_lists"), totals))


def prepare_source(args):
    source = args.source.resolve()
    if args.archive:
        source.mkdir(parents=True, exist_ok=True)
        if any(source.iterdir()):
            raise ValueError("Archive extraction needs an empty source directory")
        with tarfile.open(args.archive.resolve(), "r:") as archive:
            archive.getmembers()  # Read global PAX records before checking revision.
            if archive.pax_headers.get("comment") != BASE:
                raise ValueError("Archive must be generated by git archive of the pinned commit")
            archive.extractall(source, filter="data")
    else:
        git = ["git", "-c", f"safe.directory={source.as_posix()}", "-C", str(source)]
        if subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip() != BASE:
            raise ValueError("Wrong source commit")
        if subprocess.check_output(git + ["status", "--porcelain", "--untracked-files=normal"], text=True).strip():
            raise ValueError("Source checkout must be clean before tracing")
    return source


def generate(args):
    source, dst = prepare_source(args), args.out.resolve()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))["tests"]
    ids = {row["id"]: row["number"] for row in catalog}
    if [row["number"] for row in catalog] != list(range(len(ids))):
        raise ValueError("Unexpected catalog numbering or repeated node ID")
    os.environ.update(SKIP_INTEGRATION="0", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    os.chdir(source)
    sys.path.insert(0, str(source))
    tracker = Collector(source, ids)
    start = time.monotonic()
    tracker.start()
    try:
        status = pytest.main(["tests", "-q", "--tb=line", "-p", "no:cacheprovider"], plugins=[tracker])
    finally:
        tracker.stop()
    elapsed = time.monotonic() - start
    if status not in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED) or tracker.collection_errors:
        raise RuntimeError(f"Collection/run incomplete ({status}, {tracker.collection_errors} collection errors)")
    needed = {"setup", "call", "teardown"}
    untrusted = {node for node in ids if tracker.passed_phases[node] != needed} | tracker.nonpassing
    bad = tracker.opaque
    for node in untrusted & ids.keys():
        bad |= 1 << ids[node]
    ordered = [x["id"] for x in catalog]
    files = {
        path: {
            "lines": {str(line): format(bits, "x") for line, bits in sorted(tracker.observed[path].items())},
            "global": sorted(tracker.contextless[path]),
        } for path in sorted(tracker.observed.keys() | tracker.contextless.keys())
    }
    payload = {
        "version": 1, "base": BASE, "catalog": ordered, "files": files,
        "fixtures": {p: format(v, "x") for p, v in sorted(tracker.reads.items())},
        # Every isolated context with an untrusted trace is accounted for
        # explicitly. Global collection errors above prohibit emitting a map.
        "always_include": format(bad, "x"), "unmapped": "0",
    }
    raw = compact(payload)
    encoded = gzip.compress(raw, compresslevel=6, mtime=0)
    dst.mkdir(parents=True, exist_ok=True)
    details = {
        "base": BASE, "map_format": 1, "python": sys.version, "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version, "SKIP_INTEGRATION": "0",
        "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions() if d.metadata["Name"]},
        "catalog": len(ids), "collected": len(tracker.collected), "catalog_collected": len(ids.keys() & tracker.collected),
        "additional_collected": sorted(tracker.collected - ids.keys()), "uncollected": sorted(ids.keys() - tracker.collected),
        "nonpassing_or_missing_phase": sorted(untrusted), "opaque_child_process": [n for n in ordered if tracker.opaque & (1 << ids[n])],
        "unattributed_process_events": sorted(tracker.global_process_events),
        "always_include_count": bad.bit_count(), "unmapped_count": 0,
        "files_with_coverage": len(files), "recorded_reads": len(tracker.reads),
        "global_lines": sum(len(f["global"]) for f in files.values()), "attributed_lines": sum(len(f["lines"]) for f in files.values()),
        "elapsed_seconds": round(elapsed, 3), "map_sha256": hashlib.sha256(encoded).hexdigest(),
        "json_bytes": len(raw), "gzip_bytes": len(encoded),
    }
    if args.compare_encodings:
        details["line_matrix_encoding_comparison"] = report_encoding(files)
    (dst / "coverage.json.gz").write_bytes(encoded)
    (dst / "manifest.json").write_text(json.dumps(details, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Recorded {len(files)} source files, {len(tracker.reads)} reads, {bad.bit_count()} always-selected tests.", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--archive", type=Path, help="Extract an exact git archive into an empty --source instead of checking git HEAD")
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--compare-encodings", action="store_true")
    generate(parser.parse_args())
