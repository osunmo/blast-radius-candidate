"""Propagate baseline class consumers through copied class metadata and imports."""
import argparse
import ast
import gzip
import hashlib
import functools
import importlib.util
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from build_maps import BASE, compact, prepare_source
from refine_classes import import_package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.shared import shared_producers


def key(value):
    if isinstance(value, type) and value.__module__.startswith('sqlglot'):
        return value.__module__ + ':' + value.__qualname__
    return None


def main(args):
    out = args.out.resolve()
    data = json.loads(gzip.decompress((out/'classes.json.gz').read_bytes()))
    coverage = json.loads(gzip.decompress(args.coverage.read_bytes()))
    catalog = json.loads(args.catalog.read_bytes())['tests']
    if data['base'] != BASE or data['catalog'] != [row['id'] for row in catalog] or coverage['base'] != BASE:
        raise ValueError('Baseline mismatch')
    source = prepare_source(args)
    os.chdir(source)
    import_package(source)
    files = {}
    reverse = defaultdict(set)
    source_modules = {}
    for folder in ('sqlglot','tests'):
        for file in (source/folder).rglob('*.py'):
            relative = file.relative_to(source).as_posix()
            module = relative[:-3].replace('/','.').removesuffix('.__init__')
            files[module] = (relative, ast.parse(file.read_bytes()))
    for module,(path,tree) in files.items():
        package = module if path.endswith('/__init__.py') else module.rpartition('.')[0]
        def imports(node):
            if isinstance(node, ast.If) and (isinstance(node.test,ast.Name) and node.test.id=='TYPE_CHECKING'
                    or isinstance(node.test,ast.Attribute) and node.test.attr=='TYPE_CHECKING'):
                return
            if isinstance(node, ast.Import):
                for alias in node.names:
                    reverse[alias.name].add(module)
            elif isinstance(node, ast.ImportFrom):
                target = importlib.util.resolve_name('.'*node.level+(node.module or ''),package)
                reverse[target].add(module)
                for alias in node.names:
                    reverse[target+'.'+alias.name].add(module)
            for child in ast.iter_child_nodes(node):
                imports(child)
        imports(tree)
        if module.startswith('sqlglot'):
            source_modules[module] = (path,tree)
    by_file = defaultdict(int)
    for row in catalog:
        by_file[row['file']] |= 1<<row['number']
    @functools.lru_cache(maxsize=None)
    def module_consumers(module):
        reachable = {module}
        queue = [module]
        while queue:
            for dependent in reverse[queue.pop()] - reachable:
                reachable.add(dependent)
                queue.append(dependent)
        mask = 0
        for name in reachable:
            if name not in files:
                continue
            path = files[name][0]
            mask |= by_file[path]
            for value in coverage['files'].get(path,{}).get('lines',{}).values():
                mask |= int(value,16)
        return mask
    edges = defaultdict(set)
    direct = defaultdict(int)
    shared = shared_producers(source)
    for module,(path,tree) in source_modules.items():
        if module not in sys.modules:
            continue
        namespace = vars(sys.modules[module])
        entry = coverage['files'].get(path,{'lines':{},'global':[]})
        parents = {child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        def observes_fields(node):
            parent = parents.get(node)
            if isinstance(parent,ast.Attribute) and parent.value is node:
                return True
            cursor = node
            while cursor in parents:
                parent = parents[cursor]
                if isinstance(parent,ast.Call):
                    if isinstance(parent.func,ast.Name) and parent.func.id in {'isinstance','issubclass','type','hash','repr','id'}:
                        return False
                    return True
                if isinstance(parent,ast.stmt):
                    return isinstance(parent,ast.ClassDef) and cursor in parent.bases
                cursor=parent
            return False
        def resolve(node):
            if isinstance(node,ast.Name):
                return namespace.get(node.id)
            if isinstance(node,ast.Attribute):
                value=resolve(node.value)
                if isinstance(value,(type,type(sys))):
                    return vars(value).get(node.attr)
            return None
        def walk(node,owner=None,in_function=False,qualified=()):
            if isinstance(node,ast.If) and (isinstance(node.test,ast.Name) and node.test.id=='TYPE_CHECKING'
                    or isinstance(node.test,ast.Attribute) and node.test.attr=='TYPE_CHECKING'):
                return
            if isinstance(node,ast.ClassDef):
                scope = resolve(ast.Name(id=node.name,ctx=ast.Load())) if owner is None else vars(owner).get(node.name)
                owner=scope if isinstance(scope,type) else None
                in_function=False
                qualified = (*qualified, node.name)
            elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.Lambda)):
                in_function=True
                if hasattr(node, 'name'):
                    qualified = (*qualified, node.name)
            if isinstance(node,(ast.Name,ast.Attribute)) and isinstance(getattr(node,'ctx',None),ast.Load):
                value=resolve(node)
                target=key(value)
                if target:
                    if type(value) is type and not observes_fields(node):
                        target=None
                if target:
                    if owner is not None and (not in_function or node.lineno in entry['global']):
                        dependent=key(owner)
                        if dependent and dependent!=target:
                            edges[target].add(dependent)
                    elif node.lineno in entry['global'] or '.'.join(qualified) in shared.get(path, ()):
                        direct[target] |= module_consumers(module)
                    else:
                        direct[target] |= int(entry['lines'].get(str(node.lineno),'0'),16)
            for child in ast.iter_child_nodes(node):
                walk(child,owner,in_function,qualified)
        walk(tree)
    consumers = {name:int(mask,16) for name,mask in data['classes'].items()}
    for name,mask in direct.items():
        consumers[name]=consumers.get(name,0)|mask
    changed=True
    while changed:
        changed=False
        for name,dependents in edges.items():
            mask=consumers.get(name,0)
            for dependent in dependents:
                mask |= consumers.get(dependent,0)
            if mask != consumers.get(name,0):
                consumers[name]=mask
                changed=True
    data['classes']={name:format(mask,'x') for name,mask in sorted(consumers.items())}
    encoded=gzip.compress(compact(data),mtime=0)
    (out/'classes.json.gz').write_bytes(encoded)
    (out/'classes-links.json').write_text(json.dumps({'base':BASE,'edges':sum(map(len,edges.values())),
        'direct':len(direct),'sha256':hashlib.sha256(encoded).hexdigest()},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--coverage',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    main(parser.parse_args())
