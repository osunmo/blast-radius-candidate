"""Observe transitive package initialization in a fresh baseline interpreter."""

import importlib
import json
import sys
from pathlib import Path

from build_maps import Collector


def collect(source, output):
    sys.path.insert(0, str(source))
    if "sqlglot" in sys.modules:
        raise RuntimeError("Already imported SQLGlot")
    tracker = Collector(source, {"import sweep": 0})
    tracker.start()
    modules = []
    errors = []
    try:
        for path in sorted((source / "sqlglot").rglob("*.py")):
            parts = path.relative_to(source).with_suffix("").parts
            if parts[-1] == "__main__":
                continue  # this is the interactive CLI
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            try:
                importlib.import_module(module)
                modules.append(module)
            except ImportError:
                errors.append(module)
    finally:
        tracker.stop()
    output.write_text(json.dumps({
        "modules": len(modules), "import_errors": errors,
        "lines": {p: sorted(lines) for p, lines in tracker.contextless.items()},
        "reads": sorted(tracker.reads), "opaque": bool(tracker.opaque),
    }), encoding="utf-8")


if __name__ == "__main__":
    collect(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
