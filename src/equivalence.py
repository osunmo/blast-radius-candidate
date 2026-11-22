"""Compare compiled computation while leaving positional observers to the caller."""
import ast
import marshal
from functools import lru_cache
from types import CodeType


def canonical(code):
    constants = tuple(canonical(value) if isinstance(value, CodeType) else value
                      for value in code.co_consts)
    return code.replace(co_consts=constants, co_filename='<baseline>',
                        co_firstlineno=1, co_linetable=b'')


@lru_cache(maxsize=16)
def fingerprint(source):
    return marshal.dumps(canonical(compile(source, '<baseline>', 'exec', dont_inherit=True)))


def computationally_equal(before, after):
    # This deliberately retains docstrings, names, closure/argument layout,
    # flags, constants (including their types), and exception regions.
    return fingerprint(before) == fingerprint(after)


@lru_cache(maxsize=32)
def binding_fingerprint(source):
    tree = ast.parse(source)
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    nodes = [node for node in ast.walk(tree) if isinstance(node, functions)]
    decorated = tuple(ast.dump(node) for node in nodes if node.decorator_list)
    docs = tuple(ast.get_docstring(node) for node in nodes)
    for node in nodes:
        node.body = [ast.Pass()]
    return ast.dump(tree), tuple(ast.dump(node) for node in nodes), docs, decorated


def local_bodies_only(before, after):
    """Keep initialization/binding observers broad; narrow undecorated bodies."""
    return binding_fingerprint(before) == binding_fingerprint(after)
