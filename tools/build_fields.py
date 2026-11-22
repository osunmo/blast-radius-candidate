"""Observe baseline class field reads before package imports, without patch inputs.

This produces an uncertified field map. Precise reflection replay and source-only
copy guards certify it; opaque workers retain linked class-consumer fallbacks.
"""
import argparse
import ast
import builtins
import gzip
import importlib.abc
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HOOK = '_blast_baseline_field_read'
ORIGINAL_GETATTR = getattr
ORIGINAL_HASATTR = hasattr
ORIGINAL_VARS = vars


class Transform(ast.NodeTransformer):
    def __init__(self, names=None, shared=()):
        self.shared = set(shared)
        self.scope = ()
        self.names = names
        self.initializing = True
        self.class_name = None

    def visit_FunctionDef(self, node):
        # Defaults/decorators are evaluated by the enclosing definition.
        node.decorator_list = [self.visit(x) for x in node.decorator_list]
        node.args = self.visit(node.args)
        if node.returns:
            node.returns = self.visit(node.returns)
        previous = self.initializing
        old_scope = self.scope
        self.scope = (*self.scope, node.name)
        self.initializing = '.'.join(self.scope) in self.shared
        node.body = [self.visit(x) for x in node.body]
        self.initializing = previous
        self.scope = old_scope
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        node.args = self.visit(node.args)
        previous = self.initializing
        self.initializing = False
        node.body = self.visit(node.body)
        self.initializing = previous
        return node

    def visit_ClassDef(self, node):
        # Even nested classes execute once and may copy metadata.
        previous = self.initializing
        old_name = self.class_name
        old_scope = self.scope
        self.scope = (*self.scope, node.name)
        self.class_name = node.name
        self.initializing = True
        node = self.generic_visit(node)
        self.initializing = previous
        self.class_name = old_name
        self.scope = old_scope
        return node

    def visit_Attribute(self, node):
        node = self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and (self.names is None or node.attr in self.names or node.attr in {'__dict__', '__getattribute__'}):
            name = node.attr
            if self.class_name and name.startswith("__") and not name.endswith("__"):
                name = "_" + self.class_name.lstrip("_") + name
            result = ast.Call(func=ast.Name(id=HOOK, ctx=ast.Load()),
                              args=[node.value, ast.Constant(name), ast.Constant(self.initializing)],
                              keywords=[])
            return ast.copy_location(result, node)
        return node


class Observer(importlib.abc.MetaPathFinder):
    def __init__(self, source, context):
        self.source = Path(source)
        self.names = None
        if (self.source/'sqlglot').is_dir():
            self.names = set()
            for file in (self.source/'sqlglot').rglob('*.py'):
                for node in ast.walk(ast.parse(file.read_bytes())):
                    if isinstance(node, ast.ClassDef):
                        for statement in node.body:
                            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target] if isinstance(statement, ast.AnnAssign) else []
                            self.names.update(target.id for target in targets if isinstance(target, ast.Name))
        self.context = context
        self.shared = {}
        helper = Path(__file__).resolve().parents[1]/'src'/'shared.py'
        if helper.is_file() and (self.source/'sqlglot').is_dir():
            spec = importlib.util.spec_from_file_location('_blast_shared_fields', helper)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.shared = module.shared_producers(self.source)
        self.fields = {}
        self.declared = set()
        self.initialization = set()
        self.class_guards = {}
        self.modules = set()
        self.busy = False
        self.seen = set()
        self.last_group = None
        self.class_cache = {}
        self.mro_cache = {}

    def record(self, receiver, name=None, initialization=False):
        if self.busy or not self.context.active or (name is not None and self.names is not None and name not in self.names and name not in {'__dict__', '__getattribute__'}):
            return
        self.busy = True
        try:
            cls = receiver if isinstance(receiver, type) else type(receiver)
            identity = object.__hash__(cls)
            bases = self.mro_cache.get(identity)
            if bases is None:
                bases = tuple(base for base in type.__getattribute__(cls, '__mro__')
                              if type.__getattribute__(base, '__module__').startswith('sqlglot'))
                self.mro_cache[identity] = bases
                self.class_cache[identity] = cls
            if not bases:
                return
            if self.context.group != self.last_group:
                self.seen.clear()
                self.last_group = self.context.group
            signature = (object.__hash__(cls), name, initialization)
            if signature in self.seen:
                return
            self.seen.add(signature)
            self.class_cache[object.__hash__(cls)] = cls
            # Bypass a custom metaclass attribute implementation and hash.
            for base in bases:
                module = type.__getattribute__(base, '__module__')
                if not module.startswith('sqlglot'):
                    continue
                key = module + ':' + type.__getattribute__(base, '__qualname__')
                if name is None or name in {'__dict__', '__getattribute__'}:
                    self.class_guards[key] = self.class_guards.get(key, 0) | (self.context.all if initialization else self.context.group or self.context.all)
                    continue
                key += ':' + name
                if initialization or not self.context.group:
                    self.initialization.add(key)
                self.fields[key] = self.fields.get(key, 0) | self.context.group
        finally:
            self.busy = False

    def read(self, receiver, name, initialization=False):
        self.record(receiver, name, initialization)
        return ORIGINAL_GETATTR(receiver, name)

    def reflective_getattr(self, receiver, name, *default):
        if not isinstance(name, str):
            return ORIGINAL_GETATTR(receiver, name, *default)
        if self.names is not None and name not in self.names and name not in {'__dict__', '__getattribute__'}:
            return ORIGINAL_GETATTR(receiver, name, *default)
        frame = sys._getframe(1)
        relative = os.path.relpath(frame.f_code.co_filename, self.source).replace(os.sep, '/')
        shared = frame.f_code.co_qualname in self.shared.get(relative, ())
        self.record(receiver, name if isinstance(name, str) else None, shared)
        return ORIGINAL_GETATTR(receiver, name, *default)

    def reflective_hasattr(self, receiver, name):
        if not isinstance(name, str):
            return ORIGINAL_HASATTR(receiver, name)
        self.record(receiver, name if isinstance(name, str) else None)
        return ORIGINAL_HASATTR(receiver, name)

    def reflective_vars(self, *args):
        if args:
            self.record(args[0])
            return ORIGINAL_VARS(*args)
        # vars() means the caller's locals, not this wrapper's locals.
        return sys._getframe(1).f_locals

    def install(self):
        if 'sqlglot' in sys.modules:
            raise RuntimeError('Field observer must precede SQLGlot imports')
        setattr(builtins, HOOK, self.read)
        builtins.getattr = self.reflective_getattr
        builtins.hasattr = self.reflective_hasattr
        builtins.vars = self.reflective_vars
        sys.meta_path.insert(0, self)

    def uninstall(self):
        builtins.getattr = ORIGINAL_GETATTR
        builtins.hasattr = ORIGINAL_HASATTR
        builtins.vars = ORIGINAL_VARS
        if self in sys.meta_path:
            sys.meta_path.remove(self)

    def find_spec(self, fullname, path=None, target=None):
        if not any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in ('sqlglot', 'tests')):
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
                tree = ast.parse(file.read_bytes(), filename=str(file))
                def declarations(node, scope=()):
                    if isinstance(node, ast.ClassDef):
                        scope = (*scope, node.name)
                        for statement in node.body:
                            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target] if isinstance(statement, ast.AnnAssign) else []
                            for name in targets:
                                if isinstance(name, ast.Name):
                                    key = fullname + ':' + '.'.join(scope) + ':' + name.id
                                    observer.declared.add(key)
                                    observer.fields.setdefault(key, 0)
                    for child in ast.iter_child_nodes(node):
                        if isinstance(child, (ast.Module, ast.ClassDef)):
                            declarations(child, scope)
                declarations(tree)
                tree = Transform(observer.names, observer.shared.get(file.relative_to(observer.source).as_posix(), ())).visit(tree)
                code = compile(ast.fix_missing_locations(tree), str(file), 'exec', dont_inherit=True)
                observer.modules.add(fullname)
                exec(code, module.__dict__)
        return importlib.util.spec_from_file_location(fullname, file, loader=Loader(),
            submodule_search_locations=[str(file.parent)] if file.name == '__init__.py' else None)

    def payload(self):
        return {'declared': sorted(self.declared), 'fields': {key: format(mask, 'x') for key, mask in sorted(self.fields.items())},
                'initialization': sorted(self.initialization),
                'class_guards': {key: format(mask, 'x') for key, mask in sorted(self.class_guards.items())},
                'modules': sorted(self.modules)}


def main(args):
    import pytest
    from build_maps import BASE, Collector, compact, prepare_source
    class Contexts(Collector):
        def start(self):
            sys.addaudithook(self.audit)
        def switch(self, group):
            self.group = group
        def stop(self):
            self.active = False
    source = prepare_source(args)
    catalog = json.loads(args.catalog.read_bytes())['tests']
    ids = {row['id']: row['number'] for row in catalog}
    os.chdir(source)
    sys.path.insert(0, str(source))
    os.environ.update(SKIP_INTEGRATION='0', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    tracker = Contexts(source, ids)
    observer = Observer(source, tracker)
    observer.install()
    tracker.start()
    started = time.monotonic()
    try:
        # Disabling assertion rewriting ensures test modules pass through our
        # loader; it does not change assertion success/failure semantics.
        options = ['tests', '-q', '--assert=plain', '--tb=short', '-p', 'no:cacheprovider']
        if args.smoke:
            options = [args.smoke, *options[1:]]
        status = pytest.main(options, plugins=[tracker])
    finally:
        tracker.stop()
        observer.uninstall()
    if status not in (0, 1) or tracker.collection_errors:
        raise RuntimeError('Field profile collection incomplete')
    untrusted = {name for name in ids if tracker.passed_phases[name] != {'setup', 'call', 'teardown'}} | tracker.nonpassing
    mask = sum(1 << ids[name] for name in untrusted if name in ids)
    data = {'base': BASE, 'version': 1, 'complete': False, 'catalog': list(ids),
            'untrusted': format(mask, 'x'), 'opaque': format(tracker.opaque, 'x'),
            'always_include': format(mask | tracker.opaque, 'x'),
            **observer.payload()}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'fields.json.gz').write_bytes(gzip.compress(compact(data), mtime=0))
    (args.out/'fields-manifest.json').write_text(json.dumps({'base': BASE,
        'elapsed': time.monotonic()-started, 'fields': len(observer.fields),
        'initialization': len(observer.initialization), 'class_guards': len(observer.class_guards),
        'collected': len(tracker.collected), 'untrusted': sorted(untrusted)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--smoke')
    main(parser.parse_args())
