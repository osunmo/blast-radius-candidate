"""Refine wildcard dictionary guards with a baseline-only replay.

Only dictionary reflection is observed; class field reads retain the original
full profile. Never inputs a patch or answers.
"""
import argparse
import ast
import builtins
import gzip
import json
from pathlib import Path
import build_fields as fields

PREPARE = '_blast_prepare_field_dictionary_get'
GET = '_blast_observe_field_dictionary_get'
DICT = '_blast_observe_field_dictionary'
KEYS = '_blast_observe_field_dictionary_keys'


class GuardTransform(ast.NodeTransformer):
    def __init__(self, *args, **kwargs):
        pass

    def visit_Call(self, node):
        if (isinstance(node.func, ast.Attribute) and node.func.attr == 'get'
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == '__dict__'):
            receiver = self.visit(node.func.value.value)
            prepare = ast.Call(func=ast.Name(id=PREPARE, ctx=ast.Load()), args=[receiver], keywords=[])
            result = ast.Call(func=ast.Name(id=GET, ctx=ast.Load()),
                              args=[prepare, *[self.visit(arg) for arg in node.args]],
                              keywords=[self.visit(keyword) for keyword in node.keywords])
            return ast.copy_location(result, node)
        return self.generic_visit(node)

    def visit_Compare(self, node):
        if len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)):
            other = node.comparators[0]
            if isinstance(other, ast.Attribute) and other.attr == '__dict__':
                result = ast.Call(func=ast.Name(id=KEYS, ctx=ast.Load()), args=[self.visit(other.value)], keywords=[])
                node.left = self.visit(node.left)
                node.comparators = [ast.copy_location(result, other)]
                return node
        return self.generic_visit(node)

    def visit_Attribute(self, node):
        node = self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and node.attr == '__dict__':
            result = ast.Call(func=ast.Name(id=DICT, ctx=ast.Load()), args=[node.value], keywords=[])
            return ast.copy_location(result, node)
        return node


class Guards(fields.Observer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.global_guard_classes = set()
        self.membership_consumers = {}

    def record(self, receiver, name=None, initialization=False):
        if name is None and (initialization or not self.context.group):
            if not self.context.active:
                return
            cls = receiver if isinstance(receiver, type) else type(receiver)
            for base in type.__getattribute__(cls, '__mro__'):
                module = type.__getattribute__(base, '__module__')
                if module.startswith('sqlglot'):
                    self.global_guard_classes.add(module + ':' + type.__getattribute__(base, '__qualname__'))
            return
        super().record(receiver, name, initialization)

    def payload(self):
        result = super().payload()
        result['global_guard_classes'] = sorted(self.global_guard_classes)
        result['membership_consumers'] = {key: format(value, 'x') for key, value in self.membership_consumers.items()}
        return result

    def prepare(self, receiver):
        # Preserve original evaluation order: fetch dictionary and bind its get
        # method before evaluating the key/default argument expressions.
        mapping = fields.ORIGINAL_GETATTR(receiver, '__dict__')
        from types import MappingProxyType
        getter = type.__getattribute__(type(receiver), '__getattribute__')
        expected = type.__getattribute__ if isinstance(receiver, type) else object.__getattribute__
        if getter is not expected or type(mapping) not in (dict, MappingProxyType):
            self.record(receiver)
        function = fields.ORIGINAL_GETATTR(mapping, 'get')
        return receiver, function

    def get(self, prepared, *args, **kwargs):
        receiver, function = prepared
        name = args[0] if args and isinstance(args[0], str) else None
        self.record(receiver, name)
        return function(*args, **kwargs)

    def dictionary(self, receiver):
        self.record(receiver)
        return fields.ORIGINAL_GETATTR(receiver, '__dict__')

    def keys(self, receiver):
        mapping = fields.ORIGINAL_GETATTR(receiver, '__dict__')
        if isinstance(receiver, type) and self.context.group:
            for base in type.__getattribute__(receiver, '__mro__'):
                module = type.__getattribute__(base, '__module__')
                if module.startswith('sqlglot'):
                    key = module + ':' + type.__getattribute__(base, '__qualname__')
                    self.membership_consumers[key] = self.membership_consumers.get(key, 0) | self.context.group
        # A class mapping's attribute names are unchanged by existing value edits.
        # Custom mappings/getters may depend on class settings, so keep a guard.
        from types import MappingProxyType
        if (not isinstance(receiver, type) or not isinstance(mapping, MappingProxyType)
                or type.__getattribute__(type(receiver), '__getattribute__') is not type.__getattribute__):
            self.record(receiver)
        return mapping

    def reflective_getattr(self, receiver, name, *default):
        if isinstance(name, str) and name in {'__dict__', '__getattribute__'}:
            self.record(receiver)
        return fields.ORIGINAL_GETATTR(receiver, name, *default)

    def reflective_hasattr(self, receiver, name):
        if isinstance(name, str) and name in {'__dict__', '__getattribute__'}:
            self.record(receiver)
        return fields.ORIGINAL_HASATTR(receiver, name)

    def install(self):
        super().install()
        setattr(builtins, PREPARE, self.prepare)
        setattr(builtins, GET, self.get)
        setattr(builtins, DICT, self.dictionary)
        setattr(builtins, KEYS, self.keys)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--smoke')
    fields.Observer = Guards
    fields.Transform = GuardTransform
    fields.main(parser.parse_args())
