import sys
import unittest
from tools.refine_lazy import python_command, verify_report, register_class


class LazyTests(unittest.TestCase):
    def test_only_environment_respecting_same_python_command(self):
        self.assertTrue(python_command([sys.executable,'-c','print(1)']))
        for command in ([sys.executable,'-I','-c','print(1)'], [sys.executable,'-m','module'], 'python -c pass', ['unknown-python','-c','pass']):
            self.assertFalse(python_command(command))

    def test_report_requires_exact_lifecycle_and_package_coverage(self):
        good={'version':2,'operands_complete':True,'classes_complete':True,'operand_sites':{},'pid':12,'nonce':'unique','base':'base','nested_process':False,
              'lines':{'sqlglot/main.py':[1]},'reads':[],'classes':[]}
        self.assertTrue(verify_report(good,12,'unique','base'))
        for field,value in [('version',1),('operands_complete',False),('operand_sites',None),('pid',13),('nonce','stale'),('base','wrong'),('nested_process',True),('lines',{})]:
            bad=dict(good);bad[field]=value
            self.assertFalse(verify_report(bad,12,'unique','base'))

    def test_predeclared_parent_still_traverses_dynamic_class_fields(self):
        class Meta(type):
            def __hash__(cls):
                raise AssertionError('class hashing is observable')
        class Parent(metaclass=Meta):
            pass
        Parent.__module__='sqlglot.synthetic'
        Parent.__qualname__='Parent'
        Parent.generated=type('Generated',(),{'__module__':'sqlglot.synthetic'})
        classes={'sqlglot.synthetic:Parent'}
        register_class(Parent,classes,set())
        self.assertIn('sqlglot.synthetic:Generated',classes)
