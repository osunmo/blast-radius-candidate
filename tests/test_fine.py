import unittest
from src.fine import lambda_selection, changed_setting, setting_consumers
from pathlib import Path
import tempfile

class FineTests(unittest.TestCase):
    def test_lambda_body_has_its_own_execution_dependency(self):
        old = 'TABLE = {"x": lambda x: x + 1}\n'
        new = old.replace('x + 1', 'x + 2')
        self.assertEqual(lambda_selection(old, new, {1: 5}), 5)

    def test_changed_key_or_default_is_initialization(self):
        old = 'TABLE = {"x": lambda x=1: x + 1}\n'
        self.assertIsNone(lambda_selection(old, old.replace('"x"', '"y"'), {1: 1}))
        self.assertIsNone(lambda_selection(old, old.replace('x=1', 'x=2'), {1: 1}))
        immediate='RESULT = (lambda x: x+1)(2)\n'
        self.assertIsNone(lambda_selection(immediate,immediate.replace('x+1','x+2'),{}))

    def test_literal_setting_preserves_binding_and_excludes_calls(self):
        old = 'class Config:\n    LIMIT = 2\n'
        self.assertEqual(changed_setting(old,old.replace('2','3')), ['LIMIT'])
        self.assertIsNone(changed_setting(old,old.replace('LIMIT','OTHER')))
        self.assertIsNone(changed_setting(old,old.replace('2','create()')))
        local='def f():\n    LIMIT = 2\n    return LIMIT\n'
        self.assertIsNone(changed_setting(local,local.replace('2','3')))

    def test_setting_readers_and_dynamic_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'sqlglot').mkdir()
            (root/'sqlglot/demo.py').write_text('def read(x):\n    return x.LIMIT\ndef dynamic(x,n):\n    return getattr(x,n)\n')
            coverage={'sqlglot/demo.py':{'lines':{'2':'1','4':'2'},'global':[]}}
            self.assertEqual(setting_consumers(root,coverage,['LIMIT']),3)
            coverage['sqlglot/demo.py']['global']=[4]
            self.assertIsNone(setting_consumers(root,coverage,['LIMIT']))
