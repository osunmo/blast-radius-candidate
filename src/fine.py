"""Baseline expression dependencies for immutable dispatch definitions."""
import ast
from functools import lru_cache

def changed_setting(source, updated):
    before, after = ast.parse(source), ast.parse(updated)
    old = [statement for node in ast.walk(before) if isinstance(node, ast.ClassDef)
           for statement in node.body if isinstance(statement, ast.Assign)]
    new = [statement for node in ast.walk(after) if isinstance(node, ast.ClassDef)
           for statement in node.body if isinstance(statement, ast.Assign)]
    if len(old) != len(new):
        return None
    changed = []
    for a, b in zip(old, new):
        if ast.dump(a) != ast.dump(b):
            if len(a.targets) != 1 or not isinstance(a.targets[0], ast.Name):
                return None
            if ast.dump(a.targets[0]) != ast.dump(b.targets[0]):
                return None
            if a.targets[0].id.startswith('__'):
                return None
            try:
                ast.literal_eval(a.value)
                ast.literal_eval(b.value)
            except (ValueError, TypeError):
                return None
            changed.append(a.targets[0].id)
            a.value = b.value = ast.Constant(None)
    return changed if changed and ast.dump(before) == ast.dump(after) else None

@lru_cache(maxsize=4)
def attribute_reads(root):
    result = []
    files = [file for folder in ('sqlglot', 'tests')
             for file in (root / folder).rglob('*.py')]
    for file in sorted(files):
        path = file.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(file.read_bytes())):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                result.append((path, node.lineno, node.end_lineno, node.attr))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {'getattr', 'hasattr', 'vars'}:
                name = None
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    value = node.args[1].value
                    if isinstance(value, str):
                        name = value
                result.append((path, node.lineno, node.end_lineno, name))
    return result

def setting_consumers(root, coverage, names, reads=None):
    selected = 0
    locations = attribute_reads(root) if reads is None else reads
    for path, start, end, name in locations:
        if name is not None and name not in names:
            continue
        entry = coverage.get(path)
        if entry is None:
            return None
        for line in range(start, end + 1):
            if line in entry['global']:
                return None
            selected |= int(entry['lines'].get(str(line), '0'), 16)
    return selected or None

def lambda_selection(source, updated, coverage):
    before, after = ast.parse(source), ast.parse(updated)
    left = [n for n in ast.walk(before) if isinstance(n, ast.Lambda)]
    right = [n for n in ast.walk(after) if isinstance(n, ast.Lambda)]
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Lambda)
           for tree in (before, after) for n in ast.walk(tree)):
        return None
    if not left or len(left) != len(right):
        return None
    selected = 0
    changed = False
    for a, b in zip(left, right):
        if ast.dump(a.args) != ast.dump(b.args):
            return None
        if ast.dump(a.body) != ast.dump(b.body):
            changed = True
            for line in range(a.lineno, a.end_lineno + 1):
                selected |= coverage.get(line, 0)
        a.body = ast.Constant(value=None)
        b.body = ast.Constant(value=None)
    if changed and ast.dump(before) == ast.dump(after):
        return selected
    return None
