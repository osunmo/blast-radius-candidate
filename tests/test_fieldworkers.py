import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from refine_fields import protect_workers


class WorkerFieldGuardTests(unittest.TestCase):
    def test_original_worker_contexts_preserved_and_indexed(self):
        data={'base':'base','catalog':['a','b'],'opaque':'0','untrusted':'1'}
        protect_workers(data,json.dumps({'base':'base','opaque_child_process':['b']}).encode())
        self.assertEqual(data['opaque'],'2')
        self.assertEqual(data['always_include'],'3')
        self.assertTrue(data['certificate']['worker_contexts_indexed'])
        self.assertEqual(data['certificate']['worker_contexts_count'],1)
        self.assertEqual(len(data['certificate']['worker_manifest_sha256']),64)

    def test_unknown_worker_context_rejected(self):
        data={'base':'base','catalog':['a'],'opaque':'0','untrusted':'0'}
        with self.assertRaises(ValueError):
            protect_workers(data,json.dumps({'base':'base','opaque_child_process':['b']}).encode())

    def test_wrong_baseline_rejected(self):
        data={'base':'base','catalog':['a'],'opaque':'0','untrusted':'0'}
        with self.assertRaises(ValueError):
            protect_workers(data,json.dumps({'base':'wrong','opaque_child_process':[]}).encode())
