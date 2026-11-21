import ast
import builtins
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tools.operand_trace import HOOK, Observer, Transform


class OperandTraceTests(unittest.TestCase):
    def test_identity_observer_preserves_evaluation_order_without_calling_equality(self):
        calls = []
        class Value:
            def __eq__(self, other):
                raise AssertionError('identity comparison called equality')
        value = Value()
        def left():
            calls.append('left')
            return SimpleNamespace(value=value)
        def right():
            calls.append('right')
            return SimpleNamespace(value=value)
        context = SimpleNamespace(active=True, group=1, all=3)
        observer = Observer(Path('.'), context)
        tree = Transform('sqlglot/demo.py').visit(ast.parse('result = left().value is right().value'))
        namespace = {'left':left, 'right':right}
        with patch.object(builtins, HOOK, observer.compare, create=True):
            exec(compile(ast.fix_missing_locations(tree), 'demo.py', 'exec'), namespace)
        self.assertIs(namespace['result'], True)
        self.assertEqual(calls, ['left','right'])
        self.assertEqual(len(observer.sites), 1)

    def test_identity_negation_and_object_method_facts(self):
        observer = Observer(Path('.'), SimpleNamespace(active=True, group=2, all=3))
        value = object()
        self.assertFalse(observer.compare('site', value, value, True))
        self.assertTrue(observer.compare('site', value, None, True))
        self.assertTrue(all(bits == 2 for bits in observer.sites['site'].values()))
