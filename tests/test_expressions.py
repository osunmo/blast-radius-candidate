import ast
import unittest
from src.diff import Edit
from src.expressions import expression_anchor


class ExpressionTests(unittest.TestCase):
    def test_short_circuit_outer_guard_is_preserved(self):
        source = 'def f(x,y,z):\n    return (\n        x\n        and (\n            y\n            and z\n        )\n    )\n'
        updated = source.replace('and z','or z')
        before, after = ast.parse(source).body[0], ast.parse(updated).body[0]
        self.assertEqual(expression_anchor(before,after,Edit(5,6,5,6)),5)
        self.assertEqual(ast.dump(before),ast.dump(ast.parse(source).body[0]))

    def test_binding_changes_and_two_distinct_statements_expand(self):
        source='def f(x):\n    value = x + 1\n    return value\n'
        before=ast.parse(source).body[0]
        after=ast.parse(source.replace('value =','other =')).body[0]
        self.assertIsNone(expression_anchor(before,after,Edit(1,2,1,2)))
        after=ast.parse(source.replace('x + 1','(value := 2)')).body[0]
        self.assertIsNone(expression_anchor(before,after,Edit(1,2,1,2)))
