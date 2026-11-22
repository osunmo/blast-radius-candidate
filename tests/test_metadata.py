import ast
import types
import unittest
from tools.build_metadata import HELPER, MetadataTransformer, metadata_origin, qualified_observer_source


class MetadataTests(unittest.TestCase):
    def test_probe_preserves_single_evaluation_and_attribute_result(self):
        calls = []
        def value():
            calls.append('value')
            return value
        def probe(obj, attr):
            calls.append(attr)
            return getattr(obj, attr)
        tree = ast.fix_missing_locations(MetadataTransformer().visit(ast.parse('result = value().__code__')))
        scope = {'value':value, HELPER:probe}
        exec(compile(tree, '<probe>', 'exec'), scope)
        self.assertIs(scope['result'], value.__code__)
        self.assertEqual(calls, ['value','__code__'])

    def test_function_and_code_share_origin(self):
        def f(): pass
        self.assertEqual(metadata_origin(f), metadata_origin(f.__code__))
        self.assertIsNone(metadata_origin(object()))

    def test_store_is_not_rewritten(self):
        source = 'x.__doc__ = "updated"'
        self.assertEqual(ast.dump(ast.parse(source)), ast.dump(MetadataTransformer().visit(ast.parse(source))))

    def test_unknown_introspection_is_opaque(self):
        self.assertTrue(qualified_observer_source('value = f.__code__.co_consts'))
        for source in ('import inspect', 'value = f.__annotations__', 'value = getattr(f, key)', 'from tests.helpers import helper'):
            self.assertFalse(qualified_observer_source(source))

    def test_uninstrumented_and_aliased_observers_stay_opaque(self):
        for source in (
            'from typing import get_type_hints\nget_type_hints(f)',
            'import typing\ntyping.get_type_hints(f)',
            'from annotationlib import get_annotations as read\nread(f)',
            'import sys\nsys._getframe().f_lineno',
            'x = f.__traceback__',
            'lookup = getattr\nlookup(f, "__code__")',
            'import builtins\nbuiltins.getattr(f, "__code__")',
            'x = __builtins__["getattr"](f, "__code__")',
            'from otherpackage import helper\nhelper(f)',
            'import sqlglot\nsqlglot.helper(f)',
            'from sqlglot import helper\nhelper(f)',
            'import importlib\nhelper = importlib.import_module("inspect").getsource\nhelper(f)',
            'f.__getattribute__("__code__")',
            'module.helper(f)',
        ):
            with self.subTest(source=source):
                self.assertFalse(qualified_observer_source(source))

    def test_primitive_discovery_and_literal_code_existence_are_allowed(self):
        source = ('import ast\nimport importlib\nimport pkgutil\nimport unittest\n'
                  'from pathlib import Path\nimport sqlglot.typing\n'
                  'root = Path(sqlglot.typing.__file__).parent\n'
                  'hasattr(f, "__code__")\nvalue = f.__code__.co_consts\n')
        self.assertTrue(qualified_observer_source(source))
