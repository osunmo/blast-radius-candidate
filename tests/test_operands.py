import ast
import json
import unittest
from src.operands import identity_consumers


class OperandTests(unittest.TestCase):
    def test_enum_proof_keeps_mutation_guards_and_mixed_domains(self):
        source='def f(self):\n    return self.value is self.other\n'
        node=next(node for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Compare))
        key=f'sqlglot/demo.py:{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}'
        sites={key:{json.dumps(['E','E',False,False,False,False]):'1',
                    json.dumps(['E','str',False,False,False,False]):'2'}}
        updated=source.replace(' is ',' == ')
        self.assertEqual(identity_consumers(source,updated,'sqlglot/demo.py',sites),3)
        domains={'enums':{'E':{'kind':'canonical-str-enum','members':['a','b']}},'always_include':'4'}
        self.assertEqual(identity_consumers(source,updated,'sqlglot/demo.py',sites,domains),6)

    def test_object_equality_can_be_proved_and_custom_equality_remains(self):
        source='def f(self):\n    return self.value is None\n'
        node=next(node for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Compare))
        key=f'sqlglot/demo.py:{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}'
        sites={key:{json.dumps(['Example','NoneType',True,True,True,True]):'1',
                    json.dumps(['Custom','NoneType',False,True,True,True]):'2'}}
        self.assertEqual(identity_consumers(source,source.replace(' is ',' == '),'sqlglot/demo.py',sites),2)
        self.assertIsNone(identity_consumers(source,source.replace(' is None',' == None or True'),'sqlglot/demo.py',sites))

    def test_missing_evidence_expands(self):
        source='def f(self):\n    return self.value is None\n'
        self.assertIsNone(identity_consumers(source,source.replace(' is ',' == '),'sqlglot/demo.py',{}))

    def test_none_slots_preserve_identity_with_plain_object_equality(self):
        source='def f(self):\n    return self.value is None\n'
        node=next(node for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Compare))
        key=f'sqlglot/demo.py:{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}'
        sites={key:{json.dumps(['Example','builtins.NoneType',True,False,True,False]):'1',
                    json.dumps(['builtins.NoneType','builtins.NoneType',False,False,False,False]):'2'}}
        self.assertEqual(identity_consumers(source,source.replace(' is ',' == '),'sqlglot/demo.py',sites),0)
