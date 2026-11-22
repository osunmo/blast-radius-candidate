import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from refine_fields import protect


class StaticFieldGuardTests(unittest.TestCase):
    def profile(self, source):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'sqlglot').mkdir()
            (root/'sqlglot'/'m.py').write_text(source)
            data={'fields': {'sqlglot.m:A:X':'1', 'sqlglot.m:A:Z':'1',
                             'sqlglot.m:A:foo_sql':'1', 'sqlglot.m:Meta:FLAG':'1'},
                  'initialization': [], 'always_include':'6', 'untrusted':'2'}
            return protect(data,root,{'sqlglot/m.py':['cached']})

    def test_import_copy_metaclass_and_dominating_suffix_filter(self):
        data=self.profile('class Meta(type):\n    FLAG=1\nclass A:\n    X=1\n    Y=X\ndef cached(cls):\n    for name in dir(cls):\n        if not name.endswith("_sql"):\n            continue\n        getattr(cls,name)\n')
        self.assertIn('sqlglot.m:A:X',data['initialization'])
        self.assertIn('sqlglot.m:Meta:FLAG',data['initialization'])
        self.assertIn('sqlglot.m:A:foo_sql',data['initialization'])
        self.assertNotIn('sqlglot.m:A:Z',data['initialization'])
        self.assertEqual(data['opaque'],'4')

    def test_guard_after_lookup_does_not_narrow(self):
        data=self.profile('def cached(cls):\n    for name in dir(cls):\n        getattr(cls,name)\n        if not name.endswith("_sql"):\n            continue\n')
        self.assertEqual(set(data['initialization']),set(data['fields']))

    def test_module_binding_reflection_has_no_class_field_effect(self):
        data=self.profile('def cached(name):\n    module=importlib.import_module("sqlglot.m")\n    return getattr(module,name)\n')
        self.assertEqual(data['initialization'],[])
