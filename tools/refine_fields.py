"""Conservative source-only guards for field observation maps.

Run after dynamic collection. Never reads mutations, sample archives or labels.
"""
import argparse
import ast
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path


def protect(data, source, shared):
    initialization = set(data['initialization'])
    shared_names = set()
    reflected_shared = []
    shared_dictionary = False
    metaclasses = set()
    files = []
    for file in [file for folder in ('sqlglot', 'tests') for file in (source/folder).rglob('*.py')]:
        relative = file.relative_to(source).as_posix()
        tree = ast.parse(file.read_bytes())
        module = relative[:-3].replace('/', '.').removesuffix('.__init__')
        files.append((relative, module, tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(isinstance(base, ast.Name) and base.id == 'type' for base in node.bases):
                metaclasses.add(node.name)
    changed = True
    while changed:
        changed = False
        for _, _, tree in files:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name not in metaclasses and any(isinstance(base, ast.Name) and base.id in metaclasses for base in node.bases):
                    metaclasses.add(node.name)
                    changed = True

    def visit(node, module, path, scope=(), class_scope=None):
        nonlocal shared_dictionary
        if isinstance(node, ast.ClassDef):
            scope = (*scope, node.name)
            class_scope = scope
            targets = {target.id for statement in node.body if isinstance(statement, ast.Assign)
                       for target in statement.targets if isinstance(target, ast.Name)}
            # References through a class body's local namespace bypass LOAD_ATTR.
            for statement in node.body:
                for reference in ast.walk(statement):
                    if isinstance(reference, ast.Name) and isinstance(reference.ctx, ast.Load) and reference.id in targets:
                        initialization.add(module + ':' + '.'.join(scope) + ':' + reference.id)
            if node.name in metaclasses:
                initialization.update(module + ':' + '.'.join(scope) + ':' + name for name in targets)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = (*scope, node.name)
            if '.'.join(scope) in shared.get(path, ()):
                parents = {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}
                for reference in ast.walk(node):
                    if isinstance(reference, ast.Attribute) and reference.attr == '__dict__':
                        parent = parents.get(reference)
                        call = parents.get(parent)
                        if (isinstance(parent, ast.Attribute) and parent.attr == 'get' and isinstance(call, ast.Call) and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                            shared_names.add(call.args[0].value)
                        elif (isinstance(parent, ast.Compare) and len(parent.ops) == 1 and isinstance(parent.ops[0], (ast.In, ast.NotIn)) and isinstance(parent.left, ast.Constant) and isinstance(parent.left.value, str)):
                            shared_names.add(parent.left.value)
                        else:
                            shared_dictionary = True
                    if isinstance(reference, ast.Call) and isinstance(reference.func, ast.Name) and reference.func.id == 'vars':
                        shared_dictionary = True
                    if isinstance(reference, ast.Attribute) and isinstance(reference.ctx, ast.Load):
                        shared_names.add(reference.attr)
                    elif isinstance(reference, ast.Call) and isinstance(reference.func, ast.Name) and reference.func.id in {'getattr', 'hasattr'}:
                        if len(reference.args) > 1 and isinstance(reference.args[1], ast.Constant) and isinstance(reference.args[1].value, str):
                            shared_names.add(reference.args[1].value)
                        else:
                            argument = reference.args[1] if len(reference.args) > 1 else None
                            receiver = reference.args[0] if reference.args else None
                            module_bindings = {target.id for statement in ast.walk(node)
                                if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
                                and isinstance(statement.value.func, ast.Attribute)
                                and isinstance(statement.value.func.value, ast.Name)
                                and statement.value.func.value.id == 'importlib'
                                and statement.value.func.attr == 'import_module'
                                for target in statement.targets if isinstance(target, ast.Name)}
                            if isinstance(receiver, ast.Name) and receiver.id in module_bindings:
                                stores = [item for item in ast.walk(node) if isinstance(item, ast.Name)
                                          and isinstance(item.ctx, ast.Store) and item.id == receiver.id]
                                dominated = False
                                if len(stores) == 1:
                                    assignment = parents.get(stores[0])
                                    block = parents.get(assignment)
                                    for body in (getattr(block, 'body', ()), getattr(block, 'orelse', ())):
                                        if assignment in body:
                                            later = body[body.index(assignment)+1:]
                                            dominated |= any(any(item is reference for item in ast.walk(statement)) for statement in later)
                                if dominated:
                                    continue
                            candidates = {key.rpartition(':')[2] for key in data['fields']}
                            suffixes = []
                            if isinstance(argument, ast.Name):
                                guards = []
                                for loop in ast.walk(node):
                                    if not isinstance(loop, ast.For) or not isinstance(loop.target, ast.Name) or loop.target.id != argument.id:
                                        continue
                                    if any(isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store) and item.id == argument.id for statement in loop.body for item in ast.walk(statement)):
                                        continue
                                    prior = []
                                    for statement in loop.body:
                                        if any(item is reference for item in ast.walk(statement)):
                                            guards.extend(prior)
                                            break
                                        prior.append(statement)
                                for condition in guards:
                                    if not isinstance(condition, ast.If) or len(condition.body) != 1 or not isinstance(condition.body[0], ast.Continue):
                                        continue
                                    terms = condition.test.values if isinstance(condition.test, ast.BoolOp) and isinstance(condition.test.op, ast.Or) else [condition.test]
                                    for term in terms:
                                        if not isinstance(term, ast.UnaryOp) or not isinstance(term.op, ast.Not):
                                            continue
                                        call = term.operand
                                        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                                            and isinstance(call.func.value, ast.Name) and call.func.value.id == argument.id
                                            and call.func.attr == 'endswith' and len(call.args) == 1
                                            and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                                            suffixes.append(call.args[0].value)
                            if suffixes:
                                candidates = {name for name in candidates if all(name.endswith(suffix) for suffix in suffixes)}
                            shared_names.update(candidates)
                            reflected_shared.append({'path': path, 'function': '.'.join(scope),
                                                     'line': reference.lineno, 'candidate_names': sorted(candidates)})
        for child in ast.iter_child_nodes(node):
            visit(child, module, path, scope, class_scope)
    for path, module, tree in files:
        visit(tree, module, path)
    initialization.update(key for key in data['fields'] if key.rpartition(':')[2] in shared_names and int(data['fields'][key], 16)
                          or shared_dictionary and key.rpartition(':')[0] in data.get('class_guards', {}))
    direct_getattribute = any(isinstance(node, ast.Attribute) and node.attr == '__getattribute__'
                              for _, _, tree in files for node in ast.walk(tree))
    legacy = data.pop('legacy_class_guards', {})
    if direct_getattribute:
        for cls, value in legacy.items():
            data['class_guards'][cls] = format(int(data['class_guards'].get(cls, '0'), 16) | int(value, 16), 'x')
    data['initialization'] = sorted(initialization)
    data['opaque'] = format(int(data['always_include'], 16) & ~int(data['untrusted'], 16), 'x')
    data['static_guards'] = {'metaclasses': sorted(metaclasses), 'shared_names': sorted(shared_names),
                             'shared_dynamic_reflection': reflected_shared, 'shared_dictionary_reflection': shared_dictionary, 'legacy_getattribute_guard_required': direct_getattribute}
    # Unknown dynamic shared reflection conservatively initializes every eligible
    # field name; proven module lookups and rejected suffixes need no guard.
    return data


def protect_workers(data, manifest_bytes):
    manifest = json.loads(manifest_bytes)
    if manifest.get('base') != data['base']:
        raise ValueError('Worker manifest baseline mismatch')
    workers = manifest.get('opaque_child_process')
    if not isinstance(workers, list) or any(not isinstance(node, str) for node in workers):
        raise ValueError('Worker manifest has no explicit child context list')
    indices = {node: number for number, node in enumerate(data['catalog'])}
    if not set(workers) <= indices.keys():
        raise ValueError('Worker manifest contains unknown test contexts')
    opaque = int(data.get('opaque', '0'), 16)
    opaque |= sum(1 << indices[node] for node in set(workers))
    data['opaque'] = format(opaque, 'x')
    data['always_include'] = format(opaque | int(data['untrusted'], 16), 'x')
    data.setdefault('certificate', {}).update({
        'worker_contexts_indexed': True,
        'worker_contexts_count': len(set(workers)),
        'worker_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
        'worker_policy': 'linked-class-consumer fallback'})
    return data


def main(args):
    helper = Path(__file__).resolve().parents[1]/'src'/'shared.py'
    spec = importlib.util.spec_from_file_location('_blast_field_shared', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = json.loads(gzip.decompress(args.map.read_bytes()))
    if args.update_workers:
        certificate = data.get('certificate', {})
        if not data.get('complete') or not certificate.get('foreign_receiver_guards') or not certificate.get('static_guards') or not data.get('reflection_replay') or not args.worker_manifest:
            raise ValueError('Worker update requires an otherwise certified field map')
        protect_workers(data, args.worker_manifest.read_bytes())
        args.map.write_bytes(gzip.compress(json.dumps(data, separators=(',', ':'), sort_keys=True).encode(), mtime=0))
        return
    if args.guards:
        guards = json.loads(gzip.decompress(args.guards.read_bytes()))
        if guards['base'] != data['base'] or guards['catalog'] != data['catalog']:
            raise ValueError('Reflection replay baseline mismatch')
        guard_manifest = json.loads(args.guards.with_name('fields-manifest.json').read_bytes())
        if guard_manifest['collected'] < len(data['catalog']):
            raise ValueError('Reflection replay did not collect the full catalog')
        for key, value in guards['fields'].items():
            data['fields'][key] = format(int(data['fields'].get(key, '0'), 16) | int(value, 16), 'x')
        data['initialization'] = sorted(set(data['initialization']) | set(guards['initialization']))
        for name in ('untrusted', 'always_include'):
            data[name] = format(int(data[name], 16) | int(guards[name], 16), 'x')
        data['legacy_class_guards'] = data['class_guards']
        data['class_guards'] = guards['class_guards']
        data['global_guard_classes'] = guards.get('global_guard_classes', [])
        data['membership_consumers'] = guards.get('membership_consumers', {})
        data['reflection_replay'] = True
    protect(data, args.source, module.shared_producers(args.source))
    if args.foreign_coverage:
        from foreign_fields import augment
        coverage = json.loads(gzip.decompress(args.foreign_coverage.read_bytes()))
        if coverage['base'] != data['base'] or coverage['catalog'] != data['catalog']:
            raise ValueError('Foreign receiver coverage mismatch')
        augment(data, args.source, coverage)
    if args.worker_manifest:
        protect_workers(data, args.worker_manifest.read_bytes())
    if args.certify:
        if not data.get('certificate', {}).get('worker_contexts_indexed'):
            raise ValueError('Certification requires the original child-process context manifest')
        if not data.get('certificate', {}).get('foreign_receiver_guards'):
            raise ValueError('Certification requires foreign receiver guards')
        if not data.get('reflection_replay'):
            raise ValueError('Certification requires a complete precise reflection replay')
        manifest = json.loads(args.map.with_name('fields-manifest.json').read_bytes())
        classes = json.loads(gzip.decompress(args.classes.read_bytes()))
        if not classes.get('complete') or manifest['collected'] < len(data['catalog']) or classes['catalog'] != data['catalog'] or classes['base'] != data['base']:
            raise ValueError('Incomplete or incompatible baseline collection')
        test_modules = {node.partition('::')[0][:-3].replace('/', '.') for node in data['catalog']}
        missing = test_modules - set(data['modules'])
        if missing:
            raise ValueError('Uninstrumented test modules: ' + repr(sorted(missing)))
        data['complete'] = True
        data['certificate'] = {**data.get('certificate', {}), 'static_guards': True, 'worker_policy': 'linked-class-consumer fallback',
                               'test_modules': len(test_modules)}
    args.map.write_bytes(gzip.compress(json.dumps(data, separators=(',', ':'), sort_keys=True).encode(), mtime=0))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--map', type=Path, required=True)
    parser.add_argument('--classes', type=Path)
    parser.add_argument('--guards', type=Path)
    parser.add_argument('--foreign-coverage', type=Path)
    parser.add_argument('--certify', action='store_true')
    parser.add_argument('--worker-manifest', type=Path)
    parser.add_argument('--update-workers', action='store_true')
    main(parser.parse_args())
