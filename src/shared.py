"""Conservative baseline index of routines producing shared Python state."""

import ast
from collections import defaultdict
from importlib.util import resolve_name


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
MUTATORS = {"add", "append", "clear", "discard", "extend", "insert", "pop",
            "popitem", "remove", "setdefault", "update", "__setitem__"}


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + "." + node.attr
    if isinstance(node, ast.Subscript):
        return dotted(node.value)
    return ""


def local_nodes(function):
    pending = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (*FUNCTIONS, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))


def functions(tree, prefix=""):
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.expr):
            continue
        name = prefix
        if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
            name = f"{prefix}.{node.name}" if prefix else node.name
        if isinstance(node, FUNCTIONS):
            yield name, node
        yield from functions(node, name)


def bindings(tree, package):
    shared = set()
    aliases = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            target = resolve_name("." * node.level + (node.module or ""), package)
            aliases.update({a.asname or a.name: f"{target}.{a.name}" for a in node.names})
        elif isinstance(node, ast.Import):
            aliases.update({a.asname or a.name.split(".")[0]: a.name if a.asname else a.name.split(".")[0]
                            for a in node.names})
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            shared.update(t.id for target in targets for t in ast.walk(target) if isinstance(t, ast.Name))
        elif isinstance(node, ast.ClassDef):
            shared.add(node.name)
    return shared | aliases.keys(), aliases


def mutation_owner(node):
    if isinstance(node, (ast.Subscript, ast.Attribute)) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return dotted(node.value).split(".")[0]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS:
        return dotted(node.func.value).split(".")[0]
    return None


def class_receiver(fn):
    positional = fn.args.posonlyargs + fn.args.args
    first = positional[0].arg if positional else None
    if first in {"cls", "mcls"} or any(dotted(dec).split(".")[-1] == "classmethod"
                                       for dec in fn.decorator_list):
        return first
    return None


def shared_producers(root):
    definitions = {}
    calls = defaultdict(set)
    work = []
    for top in ("sqlglot", "tests"):
        for path in sorted((root / top).rglob("*.py")):
            relative = path.relative_to(root)
            module = ".".join(relative.with_suffix("").parts).removesuffix(".__init__")
            package = module if path.name == "__init__.py" else module.rpartition(".")[0]
            tree = ast.parse(path.read_bytes(), filename=str(path))
            shared, aliases = bindings(tree, package)
            for qual, fn in functions(tree):
                symbol = f"{module}.{qual}"
                definitions[symbol] = relative.as_posix(), qual
                marked = any(dotted(dec.func if isinstance(dec, ast.Call) else dec).split(".")[-1]
                             in {"cache", "lru_cache", "cached_property"} for dec in fn.decorator_list)
                positional = fn.args.posonlyargs + fn.args.args
                receiver = positional[0].arg if positional and "." in qual else None
                shared_receiver = class_receiver(fn)
                for node in local_nodes(fn):
                    if isinstance(node, ast.Global):
                        marked = True
                    if isinstance(node, ast.Call):
                        callee = dotted(node.func)
                        head, dot, tail = callee.partition(".")
                        if head in aliases:
                            calls[symbol].add(aliases[head] + dot + tail)
                        elif head == receiver:
                            calls[symbol].add(f"{symbol.rpartition('.')[0]}.{tail}")
                        elif head:
                            calls[symbol].add(f"{module}.{callee}")
                            calls[symbol].add(f"{symbol}.{callee}")
                    owner = mutation_owner(node)
                    if owner and (owner in shared or owner == shared_receiver):
                        marked = True
                if marked:
                    work.append(symbol)

    # Callers of an already-populated cache need not re-enter its producer
    # or its helpers. Include every statically resolved downstream helper.
    seen = set()
    result = defaultdict(set)
    while work:
        symbol = work.pop()
        if symbol in seen or symbol not in definitions:
            continue
        seen.add(symbol)
        path, qual = definitions[symbol]
        result[path].add(qual)
        work.extend(calls[symbol] - seen)
    return {path: sorted(names) for path, names in sorted(result.items())}
