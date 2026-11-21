"""Select an executed expression whose surrounding function is unchanged."""
import ast


def without(root, target):
    marker = ast.Name(id='_blast_changed_expression', ctx=ast.Load())
    if root is target:
        return ast.dump(marker)
    for parent in ast.walk(root):
        for field, value in ast.iter_fields(parent):
            if value is target:
                setattr(parent, field, marker)
                try:
                    return ast.dump(root)
                finally:
                    setattr(parent, field, target)
            if isinstance(value, list):
                for index, child in enumerate(value):
                    if child is target:
                        value[index] = marker
                        try:
                            return ast.dump(root)
                        finally:
                            value[index] = target
    raise ValueError('Expression does not belong to its function')


def candidates(root, start, end):
    forbidden = (ast.NamedExpr, ast.Lambda, ast.comprehension, ast.Await, ast.Yield, ast.YieldFrom)
    found = []
    for node in ast.walk(root):
        if not isinstance(node, ast.expr) or node.lineno > start + 1 or node.end_lineno < end:
            continue
        if any(isinstance(getattr(child, 'ctx', None), (ast.Store, ast.Del)) for child in ast.walk(node)):
            continue
        if any(isinstance(child, forbidden) for child in ast.walk(node)):
            continue
        found.append(node)
    return sorted(found, key=lambda node:(node.end_lineno-node.lineno,node.end_col_offset-node.col_offset))


def expression_anchor(before, after, edit):
    if before is None or after is None or edit.old_start == edit.old_end or edit.new_start == edit.new_end:
        return None
    old = candidates(before, edit.old_start, edit.old_end)
    new = candidates(after, edit.new_start, edit.new_end)
    fingerprints = {without(after, node):node for node in reversed(new)}
    for node in old:
        if without(before, node) in fingerprints:
            return node.lineno
    return None
