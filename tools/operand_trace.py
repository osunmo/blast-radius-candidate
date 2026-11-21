"""Observe operand types at baseline identity comparisons before any patches exist."""
import ast
import builtins
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path


HOOK = '_blast_baseline_identity'


class Transform(ast.NodeTransformer):
    def __init__(self, path):
        self.path = path

    def visit_Compare(self, node):
        self.generic_visit(node)
        if (len(node.ops) == 1 and isinstance(node.ops[0], (ast.Is, ast.IsNot))
                and (isinstance(node.left, ast.Attribute) or isinstance(node.comparators[0], ast.Attribute))):
            site = f'{self.path}:{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}'
            call = ast.Call(func=ast.Name(id=HOOK, ctx=ast.Load()),
                args=[ast.Constant(site), node.left, node.comparators[0], ast.Constant(isinstance(node.ops[0], ast.IsNot))],
                keywords=[])
            return ast.copy_location(call, node)
        return node


class Observer(importlib.abc.MetaPathFinder):
    def __init__(self, source, context):
        self.source = Path(source)
        self.context = context
        self.sites = {}
        self.cache = {}

    def compare(self, site, left, right, negate):
        tracker = self.context
        if tracker.active:
            a, b = type(left), type(right)
            signature = (a.__module__ + '.' + a.__qualname__, b.__module__ + '.' + b.__qualname__,
                         a.__eq__ is object.__eq__, b.__eq__ is object.__eq__,
                         a.__ne__ is object.__ne__, b.__ne__ is object.__ne__)
            key = self.cache.get(signature)
            if key is None:
                key = json.dumps(signature, separators=(',', ':'))
                self.cache[signature] = key
            pairs = self.sites.setdefault(site, {})
            pairs[key] = pairs.get(key, 0) | (tracker.group or tracker.all)
        result = left is right
        return not result if negate else result

    def install(self):
        if any(name == 'sqlglot' or name.startswith('sqlglot.') for name in sys.modules):
            raise RuntimeError('Operand observer must precede SQLGlot imports')
        setattr(builtins, HOOK, self.compare)
        sys.meta_path.insert(0, self)

    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'sqlglot' and not fullname.startswith('sqlglot.'):
            return None
        relative = Path(*fullname.split('.'))
        file = self.source / relative.with_suffix('.py')
        package = self.source / relative / '__init__.py'
        if package.is_file():
            file = package
        elif not file.is_file():
            return None
        observer = self
        class Loader(importlib.abc.Loader):
            def create_module(self, spec):
                return None
            def exec_module(self, module):
                source = file.read_bytes()
                tree = ast.parse(source, filename=str(file))
                tree = Transform(file.relative_to(observer.source).as_posix()).visit(tree)
                code = compile(ast.fix_missing_locations(tree), str(file), 'exec', dont_inherit=True)
                exec(code, module.__dict__)
        return importlib.util.spec_from_file_location(fullname, file, loader=Loader(),
            submodule_search_locations=[str(file.parent)] if file.name == '__init__.py' else None)


def encoded_sites(observer):
    return {site:{pair:format(bits,'x') for pair,bits in pairs.items()}
            for site,pairs in sorted(observer.sites.items())}
