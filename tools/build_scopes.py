"""Cache the exact old-side AST facts used by the selector."""

import argparse
import gzip
import json
import sys
from pathlib import Path

from build_observers import pinned_checkout
from src.selector import BASE, pack_scopes


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("artifacts", type=Path)
    args = parser.parse_args()
    source = pinned_checkout(args.source)
    files = {}
    for package in ("sqlglot", "tests"):
        for path in sorted((source / package).rglob("*.py")):
            name = path.relative_to(source).as_posix()
            files[name] = pack_scopes(path.read_text(encoding="utf-8"), name)
    payload = json.dumps({"version": 1, "base": BASE, "python_tag": sys.implementation.cache_tag,
                          "files": files}, separators=(",", ":"))
    args.artifacts.mkdir(exist_ok=True, parents=True)
    dest = args.artifacts / "scopes.json.gz"
    dest.write_bytes(gzip.compress(payload.encode("utf-8"), compresslevel=6, mtime=0))
    print(f"indexed {len(files)} source files in {dest.stat().st_size:,} bytes", file=sys.stderr)
