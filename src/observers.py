"""Find test modules that can inspect source/code objects without calling them."""

import ast
from collections import defaultdict
from importlib.util import resolve_name


METADATA = {
    "__code__", "__doc__", "__annotations__", "co_consts", "co_firstlineno",
    "_getframe", "f_code", "f_lineno", "tb_lineno", "gi_code", "cr_code",
    "co_positions", "co_lines", "co_linetable", "co_filename", "__traceback__",
    "get_type_hints", "get_annotations",
}


def reflective_modules(root):
    files = {}
    observers = set()
    reverse = defaultdict(set)
    for path in sorted((root / "tests").rglob("*.py")):
        relative = path.relative_to(root)
        module = ".".join(relative.with_suffix("").parts).removesuffix(".__init__")
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        files[module] = relative.as_posix()
        tree = ast.parse(path.read_bytes(), filename=str(path))
        dependencies = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in METADATA:
                observers.add(module)
            if isinstance(node, ast.Import):
                dependencies.update(a.name for a in node.names)
            if isinstance(node, ast.ImportFrom):
                target = resolve_name("." * node.level + (node.module or ""), package)
                dependencies.add(target)
                dependencies.update(f"{target}.{a.name}" for a in node.names)
        if any(dep.split(".")[0] in {"inspect", "doctest", "traceback", "linecache", "dis"} for dep in dependencies):
            observers.add(module)
        if any(dep.endswith(('.get_type_hints','.get_annotations')) for dep in dependencies):
            observers.add(module)
        for dependency in dependencies:
            parts = dependency.split(".")
            for i in range(1, len(parts) + 1):
                reverse[".".join(parts[:i])].add(module)
    # A helper may be re-exported or invoked through a common base class.
    queue = list(observers)
    while queue:
        for dependent in reverse[queue.pop()] - observers:
            observers.add(dependent)
            queue.append(dependent)
    return sorted(files[name] for name in observers)
