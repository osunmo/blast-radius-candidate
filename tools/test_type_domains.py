"""Independent checks for finite enum eligibility and constructor guards."""
import enum
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from build_type_domains import build


class DomainTests(unittest.TestCase):
    def test_canonical_members_only_and_reflective_bypass_disables_proof(self):
        class Value(str, enum.Enum):
            A = 'a'
            B = 'b'
        class Custom(str, enum.Enum):
            A = 'a'
            def __eq__(self, other):
                return True
        Value.__module__ = Custom.__module__ = 'sqlglot.synthetic'
        module = types.ModuleType('sqlglot.synthetic')
        module.Value, module.Custom = Value, Custom
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'sqlglot').mkdir()
            (root/'tests').mkdir()
            file = root/'tests/test_demo.py'
            path = 'tests/test_demo.py'
            catalog = [{'id':'test','file':path,'number':0}]
            coverage = {'files':{path:{'lines':{'1':'1'},'global':[]}}}
            with patch.dict(sys.modules, {'sqlglot.synthetic':module}):
                file.write_text('x = 1\n')
                data = build(root,coverage,catalog)
                self.assertEqual(len(data['enums']),1)
                self.assertEqual(next(iter(data['enums'].values()))['members'],['a','b'])
                file.write_text('setattr(Value, "__eq__", replacement)\n')
                self.assertEqual(build(root,coverage,catalog)['always_include'],'1')
                file.write_text('getattr(str, "__new__")(Value, "a")\n')
                data = build(root,coverage,catalog)
                self.assertFalse(data['enums'])
                self.assertTrue(data['constructor_bypasses'])


if __name__ == '__main__':
    unittest.main()
