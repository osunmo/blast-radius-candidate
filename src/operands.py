"""Use baseline operand domains to prove individual comparison edits equivalent."""
import ast
import json


def identity_consumers(source, updated, path, sites, type_domains=None):
    before, after = ast.parse(source), ast.parse(updated)
    old = [node for node in ast.walk(before) if isinstance(node, ast.Compare)]
    new = [node for node in ast.walk(after) if isinstance(node, ast.Compare)]
    if len(old) != len(new):
        return None
    changes = []
    for a, b in zip(old, new):
        if ast.dump(a) == ast.dump(b):
            continue
        if len(a.ops) != 1 or len(b.ops) != 1:
            return None
        positive = isinstance(a.ops[0], ast.Is) and isinstance(b.ops[0], ast.Eq)
        negative = isinstance(a.ops[0], ast.IsNot) and isinstance(b.ops[0], ast.NotEq)
        if not positive and not negative:
            return None
        site = f'{path}:{a.lineno}:{a.col_offset}:{a.end_lineno}:{a.end_col_offset}'
        if site not in sites:
            return None
        a.ops = b.ops
        if ast.dump(a) != ast.dump(b):
            return None
        changes.append((sites[site], negative))
    if not changes or ast.dump(before) != ast.dump(after):
        return None
    unsafe = 0
    enum_proof = False
    domains = type_domains or {'enums':{},'always_include':'0'}
    for pairs, negative in changes:
        for signature, bits in pairs.items():
            domain = json.loads(signature)
            # NoneType has dedicated C slots with the same identity fallback
            # semantics as object. Their descriptor objects are distinct.
            left_eq = domain[2] is True or domain[0] == 'builtins.NoneType'
            right_eq = domain[3] is True or domain[1] == 'builtins.NoneType'
            pure = left_eq and right_eq
            if negative:
                left_ne = domain[4] is True or domain[0] == 'builtins.NoneType'
                right_ne = domain[5] is True or domain[1] == 'builtins.NoneType'
                pure = pure and left_ne and right_ne
            if domain[0] == domain[1] and domain[0] in domains['enums']:
                pure = True
                enum_proof = True
            if not pure:
                unsafe |= int(bits, 16)
    if enum_proof:
        unsafe |= int(domains['always_include'],16)
    return unsafe
