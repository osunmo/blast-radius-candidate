"""Synthetic catalog diagnostics, independent of evaluation patches."""
import unittest
from tools.analyze_generalization import catalog_report


class GeneralizationTest(unittest.TestCase):
    def test_permutation_is_not_an_unknown_test(self):
        tests=[{'id':'b','number':0},{'id':'a','number':1}]
        report=catalog_report(tests,['a','b'])
        self.assertTrue(report['same_unique_ids'])
        self.assertFalse(report['exact_order'])
        self.assertEqual(report['baseline_to_input'],[1,0])
        self.assertEqual(report['unknown_ids'],[])

    def test_added_and_absent_ids_are_distinguished(self):
        report=catalog_report([{'id':'new','number':0}],['old'])
        self.assertEqual(report['unknown_ids'],['new'])
        self.assertEqual(report['missing_ids'],['old'])
        self.assertEqual(report['baseline_to_input'],[None])

    def test_row_order_and_timings_do_not_change_identity(self):
        report=catalog_report([{'id':'b','number':1,'seconds':500},{'id':'a','number':0,'seconds':0}],['a','b'])
        self.assertTrue(report['exact_order'])
