import unittest
import tempfile
from pathlib import Path
from tools.foreign_fields import augment, risky_test_source, dynamic_class_lines


class ForeignFieldTests(unittest.TestCase):
    def test_foreign_sql_subclass_and_unknown_alias_are_guarded(self):
        for source in ['from sqlglot import Dialect\nclass Custom(Dialect): pass',
                       'from helper import Alias\nclass Custom(Alias): pass',
                       'def test_it():\n    class Custom(dynamic()): pass']:
            self.assertTrue(risky_test_source(source))

    def test_unittest_local_hierarchy_and_plain_objects_are_safe(self):
        source='import unittest\nclass Base(unittest.TestCase): pass\nclass Test(Base): pass\nclass Plain: pass'
        self.assertFalse(risky_test_source(source))
        self.assertFalse(risky_test_source('from unittest import TestCase as Case\nclass Test(Case): pass'))

    def test_dynamic_builders_and_mangled_type_are_guarded(self):
        for source in ['T = type("T", bases, {})', 'T = types.new_class("T", bases)',
                       'factory = type\nT = factory(name, bases, attrs)',
                       'factory = types.new_class\nT = factory(name, bases)',
                       'class Plain:\n    __module__ = "external"']:
            self.assertTrue(risky_test_source(source))

    def test_ambiguous_class_name_does_not_establish_safe_hierarchy(self):
        self.assertTrue(risky_test_source('from sqlglot import Dialect as Base\ndef local():\n    class Base: pass\nclass Foreign(Base): pass'))

    def test_unittest_skip_decorator_preserves_class_identity(self):
        self.assertFalse(risky_test_source('import unittest as u\n@u.skipIf(flag,"reason")\nclass Test(u.TestCase): pass'))
        self.assertTrue(risky_test_source('import unittest as u\nu=other\n@u.skipIf(flag,"reason")\nclass Test(u.TestCase): pass'))

    def test_type_introspection_is_not_a_class_builder(self):
        self.assertFalse(risky_test_source('result = type(value)'))

    def test_dynamic_source_lines_reported(self):
        self.assertEqual(dynamic_class_lines('x = 1\nT = type("T", bases, {})\n'),{2})

    def test_importing_foreign_helper_guards_consumer_and_requires_recertification(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'tests').mkdir()
            (root/'sqlglot').mkdir()
            (root/'tests/helper.py').write_text('from sqlglot import Dialect\nclass Custom(Dialect): pass')
            (root/'tests/test_demo.py').write_text('from tests.helper import Custom')
            data={'catalog':['tests/test_demo.py::test_demo'],'untrusted':'0','always_include':'0','complete':True,'certificate':{'other':True}}
            augment(data,root,{'files':{}})
            self.assertEqual(data['untrusted'],'1')
            self.assertFalse(data['complete'])
            self.assertTrue(data['certificate']['foreign_receiver_guards'])
            self.assertTrue(data['certificate']['other'])

    def test_local_foreign_class_only_guards_its_enclosing_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'tests').mkdir()
            (root/'sqlglot').mkdir()
            (root/'tests/test_demo.py').write_text('import unittest\nfrom sqlglot import Dialect\nclass Test(unittest.TestCase):\n    def test_foreign(self):\n        class Foreign(Dialect): pass\n    def test_other(self): pass\n')
            data={'catalog':['tests/test_demo.py::Test::test_foreign','tests/test_demo.py::Test::test_other'],'untrusted':'0','always_include':'0'}
            augment(data,root,{'files':{}})
            self.assertEqual(data['untrusted'],'1')
