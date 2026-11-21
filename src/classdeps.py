"""Attribute changes whose unchanged owning classes have baseline consumers."""
import ast


def assignments(tree):
    result = []
    def visit(node, parents):
        if isinstance(node, ast.ClassDef):
            parents = (*parents, node.name)
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    result.append((parents, statement))
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Module, ast.ClassDef)):
                visit(child, parents)
    visit(tree, ())
    return result


def value_change(before, after):
    if ast.dump(before) == ast.dump(after):
        return True
    try:
        ast.literal_eval(before)
        ast.literal_eval(after)
        return True
    except (ValueError, TypeError):
        pass
    if isinstance(before, ast.Dict) and isinstance(after, ast.Dict):
        if len(before.keys) != len(after.keys):
            return False
        for a, b, left, right in zip(before.keys, after.keys, before.values, after.values):
            if a is None or b is None:
                if a is not b or ast.dump(left) != ast.dump(right):
                    return False
            elif ast.dump(a) != ast.dump(b) or not value_change(left, right):
                return False
        return True
    if isinstance(before, ast.Call) and isinstance(after, ast.Call):
        if ast.dump(before.func) != ast.dump(after.func) or len(before.args) != len(after.args):
            return False
        if len(before.keywords) != len(after.keywords):
            return False
        values = list(zip(before.args, after.args))
        for a, b in zip(before.keywords, after.keywords):
            if a.arg != b.arg or a.arg is None:
                return False
            values.append((a.value, b.value))
        for a, b in values:
            if ast.dump(a) != ast.dump(b):
                try:
                    ast.literal_eval(a)
                    ast.literal_eval(b)
                except (ValueError, TypeError):
                    return False
        return True
    return False


def changed_classes(source, updated):
    before, after = ast.parse(source), ast.parse(updated)
    left, right = assignments(before), assignments(after)
    if len(left) != len(right):
        return None
    owners = set()
    for (scope, a), (other, b) in zip(left, right):
        if scope != other:
            return None
        if ast.dump(a) == ast.dump(b):
            continue
        if (len(a.targets) != 1 or not isinstance(a.targets[0], ast.Name)
                or ast.dump(a.targets[0]) != ast.dump(b.targets[0])
                or a.targets[0].id.startswith('__') or not value_change(a.value, b.value)):
            return None
        owners.add('.'.join(scope))
        a.value = b.value = ast.Constant(None)
    if owners and ast.dump(before) == ast.dump(after):
        return owners
    return None
