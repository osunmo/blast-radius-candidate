"""Generate the compact test-reflection index from the pinned checkout."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.observers import reflective_modules
from src.selector import BASE
from src.shared import shared_producers


def pinned_checkout(path):
    source = path.resolve()
    git = ["git", "-c", f"safe.directory={source.as_posix()}", "-C", str(source)]
    if subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip() != BASE:
        raise ValueError("source must be the pinned SQLGlot commit")
    if subprocess.check_output(git + ["status", "--porcelain"], text=True).strip():
        raise ValueError("source must be a clean checkout")
    return source


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("artifacts", type=Path)
    args = parser.parse_args()
    source = pinned_checkout(args.source)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    files = reflective_modules(source)
    shared = shared_producers(source)
    output = args.artifacts / "observers.json"
    index = {"base": BASE, "files": files, "shared_producers": shared}
    output.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"indexed {len(files)} reflective test modules and {sum(map(len, shared.values()))} shared-state routines", file=sys.stderr)
