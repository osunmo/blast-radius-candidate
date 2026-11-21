"""Print one deterministic JSON work assignment per independent patch."""

import argparse
import json
import math
import sys
from pathlib import Path

from .scheduler import Scheduler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("/data"))
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--artifacts", type=Path, default=Path(__file__).resolve().parents[1] / "artifacts")
    parser.add_argument("--mode", choices=("all", "file", "block", "line", "hybrid", "verified"), default="hybrid")
    parser.add_argument("--refine", action="store_true")
    parser.add_argument("--explain", action="store_true")
    args = parser.parse_args()
    tests = json.loads((args.data / "tests.json").read_bytes())["tests"]
    durations = {t["number"]: math.floor(t["seconds"] * 1000 + 0.5) for t in tests}
    scheduler = Scheduler(durations, args.refine)
    selector = None
    if args.mode != "all":
        from .selector import Selector
        selector = Selector(tests, args.source, args.artifacts, args.mode, args.explain)
    for patch in sorted((args.data / "commits").glob("*.patch")):
        if args.explain:
            print(f"{patch.name} ({args.mode})", file=sys.stderr)
        selected = durations.keys()
        if selector is not None:
            try:
                selected = selector.select(patch.read_bytes().decode("utf-8"))
            except (OSError, UnicodeError):
                pass
        workers = scheduler.schedule(selected)
        print(json.dumps({"commit": patch.stem, "workers": workers}, separators=(",", ":")))


if __name__ == "__main__":
    main()
