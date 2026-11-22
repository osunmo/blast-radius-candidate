import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from build_fields import HOOK, Observer, Transform


class FieldObserverTests(unittest.TestCase):
    def run_source(self, source, observer):
        scope={'__name__':'sqlglot.synthetic', HOOK:observer.read}
        exec(compile(ast.fix_missing_locations(Transform().visit(ast.parse(source))),'<synthetic>','exec'),scope)
        return scope

    def test_inherited_read_and_private_mangling(self):
        observer=Observer(Path('.'),SimpleNamespace(active=True,group=2,all=3))
        scope=self.run_source('class A:\n    __value=5\n    def read(self):\n        return self.__value\nclass B(A):\n    pass\n',observer)
        self.assertEqual(scope['B']().read(),5)
        self.assertEqual(observer.fields['sqlglot.synthetic:A:_A__value'],2)
        self.assertEqual(observer.fields['sqlglot.synthetic:B:_A__value'],2)

    def test_import_copy_and_reflection_guards(self):
        context=SimpleNamespace(active=True,group=0,all=3)
        observer=Observer(Path('.'),context)
        scope=self.run_source('class A:\n    X={"x": 1}\nclass B:\n    Y=dict(A.X)\n',observer)
        self.assertIn('sqlglot.synthetic:A:X',observer.initialization)
        context.group=1
        self.assertEqual(observer.reflective_getattr(scope['A'],'X'),{'x':1})
        self.assertEqual(observer.reflective_vars(scope['A'])['X'],{'x':1})
        self.assertEqual(observer.class_guards['sqlglot.synthetic:A'],1)

    def test_receiver_and_descriptor_evaluated_once(self):
        observer=Observer(Path('.'),SimpleNamespace(active=True,group=1,all=1))
        scope=self.run_source('calls=[]\nclass A:\n    @property\n    def x(self):\n        calls.append("descriptor")\n        return 3\ndef make():\n    calls.append("receiver")\n    return A()\nresult=make().x\n',observer)
        self.assertEqual(scope['calls'],['receiver','descriptor'])
        self.assertEqual(scope['result'],3)

    def test_foreign_subclass_records_sqlglot_base_fields(self):
        observer=Observer(Path('.'),SimpleNamespace(active=True,group=2,all=3))
        Base=type('Base',(),{'__module__':'sqlglot.synthetic','X':1})
        Child=type('Child',(Base,),{'__module__':'tests.synthetic'})
        self.assertEqual(observer.read(Child(),'X'),1)
        self.assertEqual(observer.fields,{'sqlglot.synthetic:Base:X':2})
