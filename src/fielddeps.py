"""Identify isolated class setting edits and consume baseline field observations.

The producer must retain class-wide guards for reads during module/class
initialization, copied metadata, reflection, and unobserved workers.
"""
import ast
from .classdeps import assignments, changed_classes


def changed_fields(source, updated):
    """Return (qualified class, field) pairs for existing value-only edits."""
    if changed_classes(source, updated) is None:
        return None
    left = assignments(ast.parse(source))
    right = assignments(ast.parse(updated))
    return {('.'.join(scope), a.targets[0].id)
            for (scope, a), (_, b) in zip(left, right)
            if ast.dump(a) != ast.dump(b)}


def scalar_fields_only(source, updated):
    if changed_classes(source, updated) is None:
        return False
    allowed = (type(None), bool, int, float, str, bytes)
    for (_, a), (_, b) in zip(assignments(ast.parse(source)), assignments(ast.parse(updated))):
        if ast.dump(a) != ast.dump(b):
            try:
                if type(ast.literal_eval(a.value)) not in allowed or type(ast.literal_eval(b.value)) not in allowed:
                    return False
            except (ValueError, TypeError):
                return False
    return True


def field_consumers(module, changes, data, class_consumers):
    """Use a certified map only; missing observations require class fallback.

    Import-time reads use the linked class consumer map because a setting may
    have been copied into another class or a dispatch table before tests run.
    """
    if not data.get('complete'):
        return None
    selected = int(data.get('untrusted', data.get('always_include', '0')), 16)
    opaque = int(data.get('opaque', format(int(data.get('always_include', '0'), 16) & ~selected, 'x')), 16)
    fields = data.get('fields', {})
    observations = data.get('_class_observations')
    if observations is None:
        observations = {}
        for key, value in fields.items():
            cls = key.rpartition(':')[0]
            observations[cls] = observations.get(cls, 0) | int(value, 16)
        for collection in ('class_guards', 'membership_consumers'):
            for cls, value in data.get(collection, {}).items():
                observations[cls] = observations.get(cls, 0) | int(value, 16)
        data['_class_observations'] = observations
    guards = data.get('class_guards', {})
    imports = set(data.get('initialization', ()))
    for owner, name in changes:
        cls = module + ':' + owner
        key = cls + ':' + name
        if cls not in class_consumers:
            return None
        class_mask = class_consumers[cls] | observations.get(cls, 0)
        selected |= int(guards.get(cls, '0'), 16)
        selected |= opaque & class_mask
        if cls in data.get('global_guard_classes', ()) or key in imports or key not in fields:
            selected |= class_mask
        else:
            selected |= int(fields[key], 16)
    return selected
