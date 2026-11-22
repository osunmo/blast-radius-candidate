"""Record concrete source origins read by baseline metadata observers.

Run in the pristine checkout only. Unknown introspection stays opaque; this map
is supplemental evidence and must never replace ordinary execution coverage.
"""
import argparse
import ast
import importlib.abc
import importlib.machinery
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.observers import METADATA

ATTRIBUTES = {'__code__', '__doc__', '__annotations__', 'co_code', 'co_names',
              'co_varnames', 'co_consts', 'co_firstlineno', 'co_filename'}
HELPER = '__blast_metadata_origin_read__'


class MetadataTransformer(ast.NodeTransformer):
    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and node.attr in ATTRIBUTES:
            return ast.copy_location(ast.Call(func=ast.Name(id=HELPER, ctx=ast.Load()),
                                             args=[node.value, ast.Constant(node.attr)], keywords=[]), node)
        return node


def metadata_origin(value):
    if isinstance(value, types.MethodType):
        value = value.__func__
    if isinstance(value, types.FunctionType):
        return value.__code__.co_filename
    if isinstance(value, types.CodeType):
        return value.co_filename
    if isinstance(value, types.ModuleType):
        return getattr(value, '__file__', None)
    # Class/type annotations and instance attributes need more than a function's
    # defining source; retain these observer tests in the opaque set.
    return None


def qualified_observer_source(source):
    """Recognize observers whose code metadata reads are all instrumented.

    File reads still require the ordinary baseline source-reader map. Imports
    from other test helpers deliberately remain opaque without closure analysis.
    """
    tree = ast.parse(source)
    supported = {'__code__', 'co_code', 'co_names', 'co_varnames', 'co_consts'}
    primitives = {'ast', 'importlib', 'pkgutil', 'pathlib', 'unittest'}
    forbidden = {'getattr', 'vars', 'eval', 'exec', 'compile', '__builtins__',
                 'globals', 'locals', 'dir', 'get_type_hints', 'get_annotations',
                 '__getattribute__', '__getattr__', '__dict__'}
    local_functions = {node.name for node in ast.walk(tree)
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    primitive_calls = {'Path', 'set', 'str', 'callable', 'hasattr', 'isinstance',
                       'len', 'list', 'tuple', 'dict', 'sorted', 'any', 'all'}
    primitive_methods = {'walk', 'parse', 'iter_modules', 'import_module',
                         'items', 'get', 'read_text', 'startswith', 'join',
                         'append', 'fail'}
    sql_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root == 'sqlglot':
                    sql_modules.add(alias.asname or root)
                elif root not in primitives:
                    return False
        elif isinstance(node, ast.ImportFrom):
            # Imported package functions may inspect their arguments through
            # uninstrumented helpers. Only known discovery primitives qualify.
            root = (node.module or '').split('.')[0]
            if node.level or root not in primitives or any(a.name == '*' for a in node.names):
                return False
            if any(alias.name in forbidden or alias.name in METADATA for alias in node.names):
                return False
    parents = {child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    def root_name(node):
        while isinstance(node, ast.Attribute):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in forbidden:
            return False
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            return False
        if isinstance(node, ast.Attribute) and (node.attr.startswith('co_') or node.attr in ATTRIBUTES or node.attr in METADATA):
            if node.attr not in supported:
                return False
        if isinstance(node, ast.Attribute) and root_name(node) in sql_modules:
            # SQLGlot modules may supply a discovery path, never callable
            # helpers or aliased values with unknown introspection behavior.
            outer = node
            while isinstance(parents.get(outer), ast.Attribute):
                outer = parents[outer]
            if outer.attr != '__file__':
                return False
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in local_functions | primitive_calls:
                return False
            if node.func.id == 'hasattr' and (len(node.args) != 2 or not isinstance(node.args[1], ast.Constant)
                                             or node.args[1].value != '__code__'):
                return False
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Attribute) or node.func.attr not in primitive_methods:
                return False
    return True


class Recorder:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.current = None
        self.origins = {}
        self.opaque = set()
        self.collection = False
        self.visited = set()

    def read(self, value, attribute):
        origin = metadata_origin(value)
        if attribute in {'__doc__', '__annotations__'}:
            if self.current is None:
                self.collection = True
            else:
                self.opaque.add(self.current)
        if origin:
            try:
                relative = Path(origin).resolve().relative_to(self.root).as_posix()
            except ValueError:
                relative = None
            if relative:
                if self.current is None:
                    self.collection = True
                else:
                    self.origins.setdefault(relative, set()).add(self.current)
        elif self.current is not None:
            self.opaque.add(self.current)
        else:
            self.collection = True
        return getattr(value, attribute)


class ProbeLoader(importlib.abc.Loader):
    def __init__(self, loader, recorder):
        self.loader, self.recorder = loader, recorder

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source = self.loader.get_source(module.__name__)
        tree = ast.parse(source, filename=module.__file__)
        if any(isinstance(n, ast.Name) and n.id == HELPER for n in ast.walk(tree)):
            raise RuntimeError('metadata helper name collision')
        tree = ast.fix_missing_locations(MetadataTransformer().visit(tree))
        module.__dict__[HELPER] = self.recorder.read
        exec(compile(tree, module.__file__, 'exec'), module.__dict__)


class ProbeFinder(importlib.abc.MetaPathFinder):
    def __init__(self, recorder):
        self.recorder = recorder

    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'tests' and not fullname.startswith('tests.'):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            spec.loader = ProbeLoader(spec.loader, self.recorder)
        return spec


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--catalog', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('tests', nargs='+')
    a = p.parse_args()
    sys.path.insert(0, str(a.source.resolve()))
    recorder = Recorder(a.source)
    sys.meta_path.insert(0, ProbeFinder(recorder))
    import pytest

    class Plugin:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_protocol(self, item, nextitem):
            recorder.current = item.nodeid
            recorder.visited.add(item.nodeid)
            source = Path(item.path).read_text(encoding='utf-8')
            if not qualified_observer_source(source):
                recorder.opaque.add(item.nodeid)
            try:
                yield
            finally:
                recorder.current = None

    status = pytest.main(['-q', '--assert=plain', *a.tests], plugins=[Plugin()])
    catalog = json.loads(a.catalog.read_text(encoding='utf-8'))['tests']
    numbers = {row['id']:row['number'] for row in catalog}
    def bits(nodes):
        unknown = set(nodes) - numbers.keys()
        if unknown:
            raise ValueError(f'uncataloged observers: {sorted(unknown)}')
        return format(sum(1 << numbers[node] for node in nodes), 'x')
    data = {'version':1, 'complete':status == 0 and not recorder.collection,
            'catalog':[row['id'] for row in catalog],
            'origins':{path:bits(nodes) for path,nodes in recorder.origins.items()},
            'opaque':bits(recorder.opaque), 'observed_tests':bits(recorder.visited),
            'collection_reads':recorder.collection,
            'base':'eb11aec4be8cb2122afe713a4816662c3f0fb2fa'}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(data, sort_keys=True), encoding='utf-8')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
