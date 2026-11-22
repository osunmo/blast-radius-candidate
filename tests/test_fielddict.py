import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from build_field_guards import GuardTransform, Guards, PREPARE, GET, DICT, KEYS


class PreciseDictionaryTests(unittest.TestCase):
    def evaluate(self, source):
        observer=Guards(Path('.'),SimpleNamespace(active=True,group=1,all=1))
        scope={'__name__':'sqlglot.synthetic', PREPARE:observer.prepare,
               GET:observer.get, DICT:observer.dictionary, KEYS:observer.keys}
        exec(compile(ast.fix_missing_locations(GuardTransform().visit(ast.parse(source))),'<synthetic>','exec'),scope)
        return observer,scope

    def test_get_records_value_field_and_preserves_argument_order(self):
        observer,scope=self.evaluate('events=[]\nclass A:\n    X=7\ndef receiver():\n    events.append("receiver")\n    return A\ndef key():\n    events.append("key")\n    return "X"\ndef default():\n    events.append("default")\n    return 0\nresult=receiver().__dict__.get(key(),default())\n')
        self.assertEqual(scope['result'],7)
        self.assertEqual(scope['events'],['receiver','key','default'])
        self.assertEqual(observer.fields,{'sqlglot.synthetic:A:X':1})
        self.assertEqual(observer.class_guards,{})

    def test_membership_observes_keys_only(self):
        observer,scope=self.evaluate('class A:\n    X=7\nresult="X" in A.__dict__\n')
        self.assertTrue(scope['result'])
        self.assertEqual(observer.fields,{})
        self.assertEqual(observer.class_guards,{})

    def test_direct_dictionary_exposure_still_guards_class(self):
        observer,scope=self.evaluate('class A:\n    X=7\nresult=A.__dict__\n')
        self.assertEqual(observer.class_guards,{'sqlglot.synthetic:A':1})
