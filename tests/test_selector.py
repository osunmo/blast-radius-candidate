import difflib
from contextlib import redirect_stderr, redirect_stdout
import gzip
import json
import io
import sys
import tempfile
import unittest
from pathlib import Path

from src.selector import Selector


SOURCE = """LIMIT = 3

def convert(x):
    if x:
        return 10
    return 20

def other():
    return 30
"""
PATH = "sqlglot/example.py"
BASE = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def diff(old, new, path=PATH, n=3):
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        "a/" + path, "b/" + path, n=n))


class SelectorTest(unittest.TestCase):
    def test_class_consumers_and_import_initialization_guard(self):
        self.put('sqlglot/__init__.py', '')
        source = 'class Config:\n    VALUE = 1\n    if VALUE == 2:\n        raise RuntimeError("invalid configuration")\n'
        self.put(PATH, source)
        self.map['files'][PATH] = {'lines':{},'global':[1,2,3,4]}
        data = {'base':BASE,'catalog':self.map['catalog'],'complete':True,
                'classes':{'sqlglot.example:Config':'2'},'always_include':'8'}
        with gzip.open(self.root/'classes.json.gz','wt',encoding='utf-8') as file:
            json.dump(data,file)
        selector = self.selector('fine')
        self.assertEqual(selector.select(diff(source,source.replace('VALUE = 1','VALUE = 3'))),{1,3})
        self.assertEqual(selector.select(diff(source,source.replace('VALUE = 1','VALUE = 2'))),set(range(5)))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.put(PATH, SOURCE)
        self.data = "SELECT 1;\n"
        self.put("tests/fixtures/case.sql", self.data)
        self.tests = [
            {"number": n, "file": "tests/test_demo.py", "id": f"tests/test_demo.py::test_{n}"}
            for n in range(5)
        ]
        self.map = {"version": 1, "base": BASE, "catalog": [t["id"] for t in self.tests],
                    "files": {PATH: {"lines": {"4": "3", "5": "1", "6": "2", "9": "4"},
                                     "global": [1, 3, 8]}},
                    "fixtures": {"tests/fixtures/case.sql": "8"}, "unmapped": "0"}

    def put(self, name, value):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")

    def selector(self, mode="hybrid"):
        with gzip.open(self.root / "coverage.json.gz", "wt", encoding="utf-8") as file:
            json.dump(self.map, file)
        return Selector(self.tests, self.root, self.root, mode=mode)

    def test_replacement_granularities(self):
        change = diff(SOURCE, SOURCE.replace("return 10", "return 100"))
        for mode, expected in [("file", {0, 1, 2}), ("block", {0, 1}),
                               ("line", {0}), ("hybrid", {0}), ("all", set(range(5)))]:
            with self.subTest(mode=mode):
                self.assertEqual(self.selector(mode).select(change), expected)

    def test_inserted_branch_includes_all_previous_callers(self):
        change = diff(SOURCE, SOURCE.replace("    if x:", "    if x < 0:\n        return 4\n    if x:"), n=0)
        self.assertEqual(self.selector().select(change), {0, 1})

    def test_unrelated_hunks_union(self):
        changed = SOURCE.replace("return 10", "return 1").replace("return 30", "return 3")
        self.assertEqual(self.selector().select(diff(SOURCE, changed, n=0)), {0, 2})

    def test_fixture_and_code_union(self):
        patched = diff(self.data, "SELECT 2;\n", "tests/fixtures/case.sql") + diff(SOURCE, SOURCE.replace("return 20", "return 2"))
        self.assertEqual(self.selector().select(patched), {1, 3})

    def test_unattributed_initialization_expands(self):
        change = diff(SOURCE, SOURCE.replace("return 10", "return 1"))
        self.map["files"][PATH]["global"].append(5)
        self.assertEqual(self.selector().select(change), set(range(5)))

    def test_invalid_patch_and_collection_changes_expand(self):
        changes = [
            diff(SOURCE, SOURCE.replace("LIMIT = 3", "LIMIT = 7")),
            diff(SOURCE, SOURCE.replace("return 20", "continue")),
            diff(SOURCE, SOURCE.replace("convert(x)", "convert(x=3)")),
            diff(SOURCE, SOURCE.replace("return 20", "yield 20")),
            diff(SOURCE, SOURCE.replace("return 20", "return 21")).replace("-    return 20", "-    return 19"),
            "diff --git a/one b/two\nsimilarity index 100%\nrename from one\nrename to two\n",
            diff("x\n", "y\n", "../outside"),
            diff("x\n", "y\n", "pytest.ini"),
            diff(SOURCE, SOURCE.replace("return 10", "return 1")) + "unsupported trailer\n",
        ]
        for patch in changes:
            with self.subTest(patch=patch):
                self.assertEqual(self.selector().select(patch), set(range(5)))

    def test_textual_edit_retains_source_readers(self):
        self.map["fixtures"][PATH] = "10"
        change = diff(SOURCE, SOURCE.replace("return 10", "return 10  # detail"))
        self.assertIn(4, self.selector().select(change))

    def test_compiled_equivalence_retains_readers_and_opaque_tests(self):
        self.map["fixtures"][PATH] = "10"
        self.map["always_include"] = "8"
        change = diff(SOURCE, SOURCE.replace("LIMIT = 3", "LIMIT = 3  # maximum"))
        self.assertEqual(self.selector("verified").select(change), {3, 4})

    def test_shifted_line_table_and_encoding_directive_expand(self):
        for modified in ["# detail\n" + SOURCE, SOURCE.replace("LIMIT = 3", "# coding: latin-1\nLIMIT = 3")]:
            with self.subTest(modified=modified):
                self.assertEqual(self.selector("verified").select(diff(SOURCE, modified)), set(range(5)))

    def test_comment_marker_inside_string_is_code(self):
        source = SOURCE.replace("LIMIT = 3", 'LIMIT = "# three"')
        self.put(PATH, source)
        change = diff(source, source.replace("# three", "# four"))
        self.assertEqual(self.selector("verified").select(change), set(range(5)))

    def test_metadata_reader_reached_through_reverse_imports(self):
        self.put("tests/meta.py", "def fingerprint(f):\n    return f.__code__.co_consts\n")
        self.put("tests/common.py", "from .meta import fingerprint\n")
        self.put("tests/test_demo.py", "from tests.common import fingerprint\n")
        change = diff(SOURCE, SOURCE.replace("return 10", "return 1"))
        self.assertEqual(self.selector().select(change), set(range(5)))

    def test_shared_cache_producer_covers_warm_consumers(self):
        source = """registry = {}

def make_value():
    return 17

def acquire(key):
    if key not in registry:
        registry[key] = make_value()
    return registry[key]
"""
        self.put(PATH, source)
        self.map["files"][PATH] = {"lines": {"4": "1", "7": "7", "8": "1", "9": "7"},
                                   "global": [1, 3, 6]}
        delta = diff(source, source.replace("return 17", "return 23"))
        self.assertEqual(self.selector().select(delta), set(range(5)))

    def test_new_global_mutation_can_affect_other_test_items(self):
        old = SOURCE.replace("LIMIT = 3", "registry = {}")
        self.put(PATH, old)
        new = old.replace("return 10", 'registry["key"] = x\n        return 10')
        self.assertEqual(self.selector().select(diff(old, new)), set(range(5)))

    def test_duplicate_function_names_and_changed_docstrings_expand(self):
        source = SOURCE + "\ndef convert(x):\n    return 42\n"
        self.put(PATH, source)
        patch = diff(source, source.replace("return 42", "return 0"))
        self.assertEqual(self.selector().select(patch), set(range(5)))
        self.put(PATH, SOURCE)
        patch = diff(SOURCE, SOURCE.replace("    if x:", '    """new documentation"""\n    if x:'))
        self.assertEqual(self.selector().select(patch), set(range(5)))

    def test_explanation_names_evidence_on_stderr(self):
        selector = self.selector()
        selector.explain = True
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            selector.select(diff(SOURCE, SOURCE.replace("return 10", "return 100")))
        self.assertEqual(out.getvalue(), "")
        self.assertIn("executed line 5", err.getvalue())
        self.assertIn(PATH, err.getvalue())

    def test_indexed_hybrid_parity_and_source_provenance(self):
        from src.selector import pack_scopes
        packed = {"base": BASE, "version": 1, "python_tag": sys.implementation.cache_tag,
                  "files": {PATH: pack_scopes(SOURCE, PATH)}}
        with gzip.open(self.root / "scopes.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(packed, handle)
        hybrid, indexed = self.selector(), self.selector("indexed")
        for source in (SOURCE.replace("10", "100"), SOURCE.replace("return 10", "x += 5\n        return x"),
                       "# heading\n" + SOURCE, SOURCE.replace("other():", "other(x):"),
                       SOURCE.replace("return 10", "continue"), SOURCE.replace("return 30", "yield 30")):
            patch = diff(SOURCE, source, n=0)
            self.assertEqual(indexed.select(patch), hybrid.select(patch))
        self.put(PATH, SOURCE + "# mismatched checkout\n")
        indexed = self.selector("indexed")
        self.assertEqual(indexed.select(diff(SOURCE, SOURCE.replace("10", "100"))), set(range(5)))
        packed["python_tag"] = "different-interpreter"
        with gzip.open(self.root / "scopes.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(packed, handle)
        indexed = self.selector("indexed")
        self.assertEqual(indexed.select(diff(SOURCE, SOURCE.replace("10", "100"))), {0})

    def test_mismatched_catalog_and_missing_coverage_expand(self):
        change = diff(SOURCE, SOURCE.replace("return 10", "return 1"))
        self.map["catalog"].reverse()
        self.assertEqual(self.selector().select(change), set(range(5)))
        self.map["catalog"].reverse()
        self.map["unmapped"] = "4"
        self.assertEqual(self.selector().select(change), set(range(5)))
        self.map["unmapped"] = "0"
        del self.map["files"][PATH]
        self.assertEqual(self.selector().select(change), set(range(5)))


if __name__ == "__main__":
    unittest.main()
