"""Record baseline process-pool work signatures, independently of coverage."""

import concurrent.futures
import hashlib
import json
import os
import pickle
import sys
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

import pytest


class Jobs:
    def __init__(self):
        self.tasks = defaultdict(lambda: defaultdict(list))
        self.completed = defaultdict(list)
        self.failed = set()

    def submit(self, original, pool, node, args, kwargs):
        try:
            digest = hashlib.sha256(pickle.dumps((args, kwargs), protocol=4)).hexdigest()
        except Exception:
            digest = "unverifiable"
            self.failed.add(pool)
        self.tasks[pool][node].append(digest)
        future = original(pool, *args, **kwargs)
        def complete(result):
            if result.cancelled() or result.exception() is not None:
                self.failed.add(pool)
            self.completed[pool].append(1)
        future.add_done_callback(complete)
        return future

    def signature(self, pool):
        jobs = self.tasks[pool]
        if pool in self.failed or not jobs or "" in jobs or len(self.completed[pool]) != sum(map(len, jobs.values())):
            return None
        raw = json.dumps(jobs, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


class Census:
    node = ""

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        self.node = item.nodeid
        try:
            return (yield)
        finally:
            self.node = ""


if __name__ == "__main__":
    source, config, output = (Path(p).resolve() for p in sys.argv[1:])
    os.chdir(source)
    sys.path.insert(0, str(source))
    targets = json.loads(config.read_text())
    census, jobs = Census(), Jobs()
    original = concurrent.futures.ProcessPoolExecutor.submit
    def submit(pool, *args, **kwargs):
        return jobs.submit(original, pool, census.node, args, kwargs)
    with patch.object(concurrent.futures.ProcessPoolExecutor, "submit", submit):
        status = pytest.main([*targets, "-q", "--tb=line", "-p", "no:cacheprovider"], plugins=[census])
    output.write_text(json.dumps([jobs.signature(x) for x in jobs.tasks] if status == 0 else []))
