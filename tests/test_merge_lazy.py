import copy
import unittest
from tools.merge_lazy import apply_report


class MergeLazyTests(unittest.TestCase):
    def inputs(self):
        common={'base':'base','catalog':['a','b','c']}
        coverage=dict(common,always_include='7',unmapped='0',files={'x.py':{'lines':{'1':'2'},'global':[9]}},fixtures={})
        classes=dict(common,complete=True,always_include='7',untrusted='4',classes={'p:C':'2'})
        operands=dict(common,complete=True,always_include='7',untrusted='4',sites={})
        report=dict(common,version=2,producer='refine_lazy-v2',status='passed',complete=True,rejected=[],groups={
            '1':{'children':1,'classes_complete':True,'operands_complete':True,'operand_sites':{},
                 'lines':{'x.py':[1,2]},'reads':['data.sql'],'classes':['p:C'],'modules':['p']}})
        return coverage,classes,operands,report

    def test_only_verified_bits_cleared_existing_evidence_retained(self):
        original=self.inputs();snapshot=copy.deepcopy(original)
        coverage,classes,operands,mask=apply_report(*original)
        self.assertEqual(original,snapshot)
        self.assertEqual(mask,1)
        self.assertEqual([data['always_include'] for data in (coverage,classes,operands)],['6','6','6'])
        self.assertEqual(coverage['files']['x.py'],{'lines':{'1':'3','2':'1'},'global':[9]})
        self.assertEqual(coverage['fixtures'],{'data.sql':'1'})
        self.assertEqual(classes['classes'],{'p:C':'3'})

    def test_protected_bits_survive_even_when_replay_passed(self):
        args=list(self.inputs());args[1]['untrusted']='1';args[2]['untrusted']='1'
        coverage,classes,operands,_=apply_report(*args,protected_coverage=1)
        self.assertEqual([data['always_include'] for data in (coverage,classes,operands)],['7','7','7'])

    def test_uncertified_legacy_or_mismatched_reports_rejected(self):
        for field,value in [('version',1),('status','failed'),('complete',False),('catalog',['other'])]:
            args=list(self.inputs());args[-1][field]=value
            with self.assertRaises(ValueError):apply_report(*args)
        args=list(self.inputs());del args[-1]['groups']['1']['operand_sites']
        with self.assertRaises(ValueError):apply_report(*args)

    def test_read_paths_cannot_escape_source(self):
        args=list(self.inputs());args[-1]['groups']['1']['reads']=['../outside']
        with self.assertRaises(ValueError):apply_report(*args)
