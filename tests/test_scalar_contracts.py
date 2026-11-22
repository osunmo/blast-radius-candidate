import types
import unittest
from tools.build_scalar_contracts import build


class ScalarContractTests(unittest.TestCase):
    def test_only_exact_own_namespace_primitives_are_certified(self):
        class Base:
            inherited = 1
        class Config(Base):
            value = 1
            descriptor = property(lambda self: 1)
            container = []
            class Nested:
                value = None
        module = types.ModuleType('sqlglot.demo')
        module.Config = Config
        keys = ['sqlglot.demo:Config:' + name for name in ('value','inherited','descriptor','container','missing')]
        keys.append('sqlglot.demo:Config.Nested:value')
        result = build({'base':'b','catalog':['test'],'fields':dict.fromkeys(keys,'1')}, lambda _: module)
        self.assertEqual(result['fields'], [keys[-1], keys[0]])
        self.assertTrue(result['complete'])

    def test_import_failure_denies_module_and_is_documented(self):
        def fail(name):
            raise ImportError('missing dependency')
        result = build({'base':'b','catalog':[],'fields':{'sqlglot.bad:X:value':'0'}}, fail)
        self.assertEqual(result['fields'], [])
        self.assertIn('sqlglot.bad', result['errors'])
        self.assertTrue(result['complete'])

    def test_metaclass_and_descriptor_getters_are_not_called(self):
        class Meta(type):
            def __getattribute__(self, name):
                raise AssertionError('custom lookup must not run')
        class Config(metaclass=Meta):
            value = 1
        module = types.ModuleType('sqlglot.demo')
        module.Config = Config
        result = build({'base':'b','catalog':[],'fields':{'sqlglot.demo:Config:value':'0'}}, lambda _: module)
        self.assertEqual(result['fields'], ['sqlglot.demo:Config:value'])

    def test_conditional_metaclass_rewrite_denies_original_literal(self):
        class Meta(type):
            def __new__(cls, name, bases, namespace):
                if namespace.get('value') == 1:
                    namespace['value'] = property(lambda self: 1)
                return super().__new__(cls, name, bases, namespace)
        class Original(metaclass=Meta):
            value = 1
        class Updated(metaclass=Meta):
            value = 2
        module = types.ModuleType('sqlglot.demo')
        module.Original, module.Updated = Original, Updated
        result = build({'base':'b','catalog':[],'fields':{
            'sqlglot.demo:Original:value':'0', 'sqlglot.demo:Updated:value':'0'}}, lambda _: module)
        self.assertEqual(result['fields'], ['sqlglot.demo:Updated:value'])
