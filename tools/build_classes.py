"""Record baseline concrete class consumers without patch or answer inputs."""
import argparse
import ast
import functools
import gzip
import hashlib
import importlib
import inspect
import json
import os
import sys
import time
from pathlib import Path

import pytest
from build_maps import BASE, Collector, compact, prepare_source


class Classes(Collector):
    def __init__(self, source, ids):
        super().__init__(source, ids)
        self.classes = {}
        self.seen = set()
        self.originals = []

    def switch(self, group):
        self.group = group
        self.seen.clear()

    def record(self, receiver):
        if not self.active or not self.group:
            return
        cls = receiver if isinstance(receiver, type) else type(receiver)
        identity = object.__hash__(cls)
        if identity in self.seen:
            return
        self.seen.add(identity)
        for base in cls.__mro__:
            if base.__module__.startswith('sqlglot'):
                key = base.__module__ + ':' + base.__qualname__
                self.classes[key] = self.classes.get(key, 0) | self.group

    def wrap(self, function):
        @functools.wraps(function)
        def observed(*args, **kwargs):
            if args and self.group:
                receiver = args[0]
                cls = receiver if isinstance(receiver, type) else type(receiver)
                if object.__hash__(cls) not in self.seen:
                    self.record(receiver)
            return function(*args, **kwargs)
        return observed

    def audit(self, event, args):
        if event in {'subprocess.Popen','os.fork','os.forkpty','os.posix_spawn',
                     'os.system','os.exec','_winapi.CreateProcess','_posixsubprocess.fork_exec'}:
            super().audit(event, args)

    def install(self):
        classes = set()
        for module in list(sys.modules.values()):
            name = getattr(module, '__name__', '')
            if name.startswith('sqlglot'):
                classes.update(value for value in vars(module).values()
                               if isinstance(value, type) and value.__module__.startswith('sqlglot'))
        pending = list(classes)
        while pending:
            cls = pending.pop()
            for value in vars(cls).values():
                if isinstance(value, type) and value.__module__.startswith('sqlglot') and value not in classes:
                    classes.add(value)
                    pending.append(value)
        for cls in classes:
            for name, descriptor in list(vars(cls).items()):
                if name in {'__new__', '__getattribute__', '__setattr__'}:
                    continue
                function = descriptor.__func__ if isinstance(descriptor, (classmethod, staticmethod)) else descriptor
                if not inspect.isfunction(function) or not function.__code__.co_filename.startswith(self.root):
                    continue
                replacement = self.wrap(function)
                if isinstance(descriptor, classmethod):
                    replacement = classmethod(replacement)
                elif isinstance(descriptor, staticmethod):
                    replacement = staticmethod(replacement)
                try:
                    setattr(cls, name, replacement)
                except TypeError:
                    continue
                self.originals.append((cls, name, descriptor))

    def start(self):
        sys.addaudithook(self.audit)
        self.install()

    def stop(self):
        self.active = False
        for cls, name, descriptor in reversed(self.originals):
            setattr(cls, name, descriptor)

    def test_references(self, source, catalog, coverage=None):
        by_file = {}
        for row in catalog:
            by_file[row['file']] = by_file.get(row['file'], 0) | (1 << row['number'])
        self.active = True
        try:
            for path, mask in by_file.items():
                name = path[:-3].replace('/', '.')
                module = sys.modules.get(name)
                if module is None:
                    continue
                namespace = vars(module)
                def resolve(node):
                    if isinstance(node, ast.Name):
                        return namespace.get(node.id)
                    if isinstance(node, ast.Attribute):
                        value = resolve(node.value)
                        if isinstance(value, (type, type(sys))):
                            result = vars(value).get(node.attr)
                            if result is None and isinstance(value, type) and node.attr == 'classes':
                                result = getattr(value, node.attr, None)
                            return result
                    return None
                def references(value, seen=None):
                    seen = set() if seen is None else seen
                    if id(value) in seen:
                        return []
                    seen.add(id(value))
                    if isinstance(value, type):
                        return [value]
                    if isinstance(value, (list, tuple, set, frozenset, dict)) and len(value) < 10000:
                        items = list(value.keys()) + list(value.values()) if isinstance(value, dict) else value
                        return [cls for item in items for cls in references(item, seen)]
                    return []
                for node in ast.walk(ast.parse((source/path).read_bytes())):
                    values = []
                    if isinstance(node, (ast.Name, ast.Attribute)):
                        value = resolve(node)
                        values = references(value)
                    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        value = resolve(node.func.value)
                        if isinstance(value, type) and node.func.attr == '__subclasses__':
                            values = value.__subclasses__()
                        elif (isinstance(value, type) and node.func.attr == 'get'
                              and type(value).__module__.startswith('sqlglot')):
                            try:
                                args = [ast.literal_eval(argument) for argument in node.args]
                                if node.keywords:
                                    raise ValueError('dynamic registry query')
                                values = references(value.get(*args))
                            except (ValueError, TypeError):
                                values = references(getattr(value, 'classes', {}))
                    if values:
                        selected = mask
                        entry = coverage.get(path) if coverage else None
                        if entry is not None and node.lineno not in entry['global']:
                            selected = int(entry['lines'].get(str(node.lineno), '0'), 16)
                        self.switch(selected)
                        for value in values:
                            self.record(value)
        finally:
            self.active = False


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    source = prepare_source(args)
    catalog = json.loads(args.catalog.read_bytes())['tests']
    ids = {row['id']: row['number'] for row in catalog}
    os.environ.update(SKIP_INTEGRATION='0', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    os.chdir(source)
    sys.path.insert(0, str(source))
    for file in sorted((source/'sqlglot').rglob('*.py')):
        parts = file.relative_to(source).with_suffix('').parts
        if parts[-1] == '__main__':
            continue
        name = '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)
        importlib.import_module(name)
    tracker = Classes(source, ids)
    start = time.monotonic()
    tracker.start()
    try:
        options = ['tests', '-q', '--tb=short', '-p', 'no:cacheprovider']
        if args.smoke:
            options.append('--maxfail=1')
        status = pytest.main(options, plugins=[tracker])
    finally:
        tracker.stop()
    if status not in (0, 1) or tracker.collection_errors:
        raise RuntimeError('Class profile collection incomplete')
    untrusted = {node for node in ids if tracker.passed_phases[node] != {'setup','call','teardown'}} | tracker.nonpassing
    untrusted_mask = sum(1 << ids[node] for node in untrusted if node in ids)
    payload = {'base': BASE, 'version': 1, 'complete':False, 'catalog': [row['id'] for row in catalog],
               'classes': {key: format(value,'x') for key,value in sorted(tracker.classes.items())},
               'untrusted': format(untrusted_mask, 'x'),
               'always_include': format(untrusted_mask | tracker.opaque, 'x')}
    encoded = gzip.compress(compact(payload), mtime=0)
    args.out.mkdir(exist_ok=True,parents=True)
    (args.out/'classes.json.gz').write_bytes(encoded)
    (args.out/'classes-manifest.json').write_text(json.dumps({'base':BASE,'python':sys.version,
        'elapsed':time.monotonic()-start,'catalog':len(ids),'collected':len(tracker.collected),
        'untrusted':sorted(untrusted),'wrapped':len(tracker.originals),'classes':len(tracker.classes),
        'sha256':hashlib.sha256(encoded).hexdigest()},indent=2))
