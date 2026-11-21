"""Select tests using dependencies of the pristine, pinned checkout only."""

import ast
import gzip
import json
import marshal
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .diff import Change, Unsupported, parse_patch
from .observers import reflective_modules
from .shared import bindings, class_receiver, local_nodes, mutation_owner, shared_producers


BASE = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPES = (*FUNCTIONS, ast.ClassDef)
SIMPLE = (ast.Return, ast.Raise, ast.Expr, ast.Assert, ast.Assign, ast.AugAssign)
SHARED_SETUP = {"setUpClass", "tearDownClass", "setUpModule", "tearDownModule", "load_tests"}
REFLECTIVE = re.compile(r"\b(?:yield|global|nonlocal|__doc__|__code__|__class__)\b")
COOKIE = re.compile(r"^[ \t\f]*\#.*?coding[:=][ \t]*([-\w.]+)")
DIRECTIVE = re.compile(r"#\s*(?:!|type\s*:|pragma\b|noqa\b|fmt\s*:|mypy\s*:|ruff\s*:)", re.I)


@dataclass
class Body:
    name: tuple[str, ...]
    node: ast.AST
    start: int  # inclusive, one-based
    end: int    # inclusive, one-based

    def contains(self, start, end):
        if start == end:
            return self.start - 1 <= start <= self.end
        return self.start <= start + 1 and end <= self.end


class SourceIndex:
    def __init__(self, source, path, validate=False):
        self.lines = source.split("\n")
        tree = ast.parse(source, filename=path)
        if validate:
            # Parsing alone accepts return/continue in illegal contexts.
            compile(tree, path, "exec", dont_inherit=True)
            package = path.replace("/", ".").rsplit(".", 2)[0]
            self.shared_names = bindings(tree, package)[0]
        self.by_name = {}
        self.bodies = []
        self.simple = {}
        self.visit(tree, ())
        self.bodies.sort(key=lambda body: body.end - body.start)

    def visit(self, node, prefix):
        if isinstance(node, SCOPES):
            prefix = (*prefix, node.name)
            if isinstance(node, FUNCTIONS):
                start = max(node.lineno + 1, node.body[0].lineno)
                body = Body(prefix, node, start, node.end_lineno)
                self.bodies.append(body)
                # Repeated overloads, conditional definitions and property
                # setters can share names. A counterpart would be ambiguous.
                self.by_name[prefix] = None if prefix in self.by_name else body
        if isinstance(node, SIMPLE) and node.lineno == node.end_lineno:
            line = node.lineno
            self.simple[line] = None if line in self.simple else node
        # Expressions cannot contain named statements. Skipping them avoids
        # walking thousands of parser dictionary entries just to index scopes.
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.expr):
                self.visit(child, prefix)

    def owner(self, start, end):
        for body in self.bodies:
            if body.contains(start, end):
                return body
        return None

    def writes_shared_state(self, body, start, end):
        if start == end:
            return False
        shared = self.shared_names | {class_receiver(body.node)}
        for node in local_nodes(body.node):
            if start < getattr(node, "lineno", 0) <= end:
                owner = mutation_owner(node)
                if owner and owner in shared:
                    return True
        return False


def matching_bodies(old, new, edit):
    before = old.owner(edit.old_start, edit.old_end) if edit.old_start < edit.old_end else None
    after = new.owner(edit.new_start, edit.new_end) if edit.new_start < edit.new_end else None
    if edit.old_start < edit.old_end and before is None:
        raise Unsupported("module or class definition changed")
    if edit.new_start < edit.new_end and after is None:
        raise Unsupported("module or class definition changed")
    name = (before or after).name
    before = old.by_name.get(name)
    after = new.by_name.get(name)
    if (not before or not after or not before.contains(edit.old_start, edit.old_end)
            or not after.contains(edit.new_start, edit.new_end)):
        raise Unsupported("definition added, removed, renamed or ambiguous")
    if name[-1] in SHARED_SETUP:
        raise Unsupported("shared setup or test discovery")
    for body in (before, after):
        if any(isinstance(n, ast.Name) and n.id in {"fixture", "lru_cache", "cache", "cached_property"}
               or isinstance(n, ast.Attribute) and n.attr in {"fixture", "lru_cache", "cache", "cached_property"}
               for dec in body.node.decorator_list for n in ast.walk(dec)):
            raise Unsupported("shared fixture or memoized body")
    if ast.get_docstring(before.node, clean=False) != ast.get_docstring(after.node, clean=False):
        raise Unsupported("function metadata changed")
    return before, after


def single_line_change(old, new, edit):
    if edit.old_end - edit.old_start != 1 or edit.new_end - edit.new_start != 1:
        return False
    a = old.simple.get(edit.old_end)
    b = new.simple.get(edit.new_end)
    if a is None or type(a) is not type(b) or a.col_offset != b.col_offset:
        return False
    # New bindings can change the interpretation of another line before
    # the edited statement runs. Comprehensions and lambdas have separate scopes.
    nested = (ast.NamedExpr, ast.Lambda, ast.comprehension, ast.Yield, ast.YieldFrom, ast.Await)
    if any(isinstance(n, nested) for stmt in (a, b) for n in ast.walk(stmt)):
        return False
    if isinstance(a, ast.Assign):
        return [ast.dump(x) for x in a.targets] == [ast.dump(x) for x in b.targets]
    if isinstance(a, ast.AugAssign):
        return ast.dump(a.target) == ast.dump(b.target)
    return True


def compile_bytes(text, path):
    # Marshal traverses nested code objects and retains constants, exception
    # tables and source positions. Plain co_code or AST equality loses these.
    return marshal.dumps(compile(text, path, "exec", dont_inherit=True, optimize=0))


def text_candidate(old, new, edits):
    # Cheap filter only. A # inside a string also passes this check and must
    # still match the complete compiled representation to take the fast path.
    for edit in edits:
        a = old[edit.old_start:edit.old_end]
        b = new[edit.new_start:edit.new_end]
        if any(DIRECTIVE.search(line) for line in a + b):
            return False
        if ([code for line in a if (code := line.partition("#")[0].rstrip())]
                != [code for line in b if (code := line.partition("#")[0].rstrip())]):
            return False
    return True


class Selector:
    def __init__(self, tests, source_root, artifact_dir, mode="hybrid", explain=False):
        if mode not in {"all", "file", "block", "line", "hybrid", "verified"}:
            raise ValueError(f"unknown selector mode {mode!r}")
        self.mode = mode
        self.explain = explain
        self.root = Path(source_root).resolve()
        self.tests = sorted(tests, key=lambda row: row["number"])
        self.all = {t["number"] for t in self.tests}
        self.mask = (1 << len(self.tests)) - 1
        self.coverage = None
        self.always = 0
        self.observers = 0
        self.shared = {}
        self.error = "all-tests baseline"
        if mode == "all":
            return
        try:
            with gzip.open(Path(artifact_dir) / "coverage.json.gz", "rt", encoding="utf-8") as f:
                data = json.load(f)
            if (data["version"] != 1 or data["base"] != BASE
                    or any(t["number"] != n for n, t in enumerate(self.tests))
                    or [t["id"] for t in self.tests] != data["catalog"]):
                raise Unsupported("catalog or source revision mismatch")
            if self.bits(data["unmapped"]):
                raise Unsupported("coverage collection incomplete")
            self.always = self.bits(data.get("always_include", "0"))
            self.fixture_bits = {path: self.bits(bits) for path, bits in data["fixtures"].items()}
            if mode in {"hybrid", "verified"}:
                index_file = Path(artifact_dir) / "observers.json"
                if index_file.is_file():
                    index = json.loads(index_file.read_text(encoding="utf-8"))
                    if index["base"] != BASE:
                        raise Unsupported("reflection index revision mismatch")
                    observers = set(index["files"])
                    self.shared = index["shared_producers"]
                else:
                    observers = set(reflective_modules(self.root))
                    self.shared = shared_producers(self.root)
                self.observers = sum(1 << t["number"] for t in self.tests if t["file"] in observers)
            self.coverage = data["files"]
        except (ValueError, TypeError, KeyError, AttributeError, OSError, EOFError, SyntaxError, ImportError) as exc:
            self.error = str(exc)

    def bits(self, value):
        value = int(value, 16)
        if value < 0 or value & ~self.mask:
            raise Unsupported("out-of-catalog bits")
        return value

    @lru_cache(maxsize=64)
    def lines(self, path):
        entry = self.coverage[path]
        return {int(n): self.bits(bits) for n, bits in entry["lines"].items()}, set(entry["global"])

    @lru_cache(maxsize=64)
    def source(self, path):
        # diff parsing has already rejected absolute paths and parent traversal.
        actual = (self.root / path).resolve()
        if not actual.is_relative_to(self.root):
            raise Unsupported("path escapes the pinned checkout")
        return actual.read_text(encoding="utf-8")

    @lru_cache(maxsize=40)
    def index(self, path):
        return SourceIndex(self.source(path), path)

    @lru_cache(maxsize=24)
    def compiled(self, path):
        return compile_bytes(self.source(path), path)

    def select(self, patch_text):
        if self.coverage is None:
            if self.explain:
                print(f"full suite: {self.error}", file=sys.stderr)
            return self.all.copy()
        location = "patch"
        try:
            mask = self.always
            if self.explain and self.always:
                print(f"always include {self.always.bit_count()} opaque tests", file=sys.stderr)
            for change in parse_patch(patch_text):
                location = change.path
                selected = self.file_selection(change)
                mask |= selected
            if self.explain:
                print(f"selected {mask.bit_count()} tests in union", file=sys.stderr)
            return {n for n in self.all if (mask >> n) & 1}
        except (ValueError, TypeError, KeyError, AttributeError, OSError, SyntaxError, ImportError, RecursionError) as exc:
            if self.explain:
                print(f"{location}: full suite ({exc})", file=sys.stderr)
            return self.all.copy()

    def file_selection(self, change: Change):
        path = change.path
        source = self.source(path)
        updated, edits = change.apply(source)
        readers = self.fixture_bits.get(path, 0)
        if not path.endswith(".py"):
            # Configuration and discovery may be read before any item exists.
            if self.mode in {"hybrid", "verified"} and path.startswith("tests/fixtures/") and readers:
                if self.explain:
                    print(f"{path}: {readers.bit_count()} observed fixture readers", file=sys.stderr)
                return readers
            raise Unsupported("non-source dependency with uncertain scope")
        if Path(path).name in {"conftest.py", "setup.py"} or path not in self.coverage:
            raise Unsupported("test discovery or missing source coverage")
        for text in (source, updated):
            for line in text.split("\n", 2)[:2]:
                cookie = COOKIE.match(line)
                if cookie and cookie[1].lower().replace("_", "-") not in {"utf-8", "utf8"}:
                    raise Unsupported("non-UTF-8 source declaration")
        mask = readers | self.observers
        if self.explain and mask:
            print(f"{path}: {readers.bit_count()} source readers, "
                  f"{self.observers.bit_count()} metadata observers", file=sys.stderr)
        if self.mode == "verified" and text_candidate(source.split("\n"), updated.split("\n"), edits):
            if self.compiled(path) == compile_bytes(updated, path):
                if self.explain:
                    print(f"{path}: identical compiled code and source positions", file=sys.stderr)
                return mask
        old = self.index(path)
        new = SourceIndex(updated, path, validate=True)
        lines, initial = self.lines(path)
        for edit in edits:
            before, after = matching_bodies(old, new, edit)
            if ".".join(before.name) in self.shared.get(path, ()):
                raise Unsupported("producer of shared state or one of its helpers")
            if new.writes_shared_state(after, edit.new_start, edit.new_end):
                raise Unsupported("change mutates shared module or class state")
            changed_lines = old.lines[edit.old_start:edit.old_end] + new.lines[edit.new_start:edit.new_end]
            if any(REFLECTIVE.search(line) for line in changed_lines):
                raise Unsupported("generator, scope or reflective metadata changed")
            narrow = (self.mode in {"line", "hybrid", "verified"} and single_line_change(old, new, edit)
                      and edit.old_end in lines)
            start, end = (edit.old_end, edit.old_end) if narrow else (before.start, before.end)
            if any(start <= n <= end for n in initial):
                raise Unsupported("also executed outside attributed tests")
            if self.mode == "file":
                for bits in lines.values():
                    mask |= bits
                if self.explain:
                    print(f"{path}: module execution coverage", file=sys.stderr)
            else:
                for n in range(start, end + 1):
                    mask |= lines.get(n, 0)
                if self.explain:
                    scope = ".".join(before.name)
                    evidence = f"executed line {start}" if narrow else f"function lines {start}-{end}"
                    print(f"{path}: {scope}, {evidence}", file=sys.stderr)
        return mask
