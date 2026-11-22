"""Import one reconstructed module in a fresh interpreter with an in-memory loader."""
import importlib
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path


def scalar_namespace(module, fields):
    try:
        for owner, name in fields:
            current = module.__dict__[owner.split('.')[0]]
            for part in owner.split('.')[1:]:
                current = type.__getattribute__(current, '__dict__')[part]
            value = type.__getattribute__(current, '__dict__')[name]
            if type(value) not in (type(None), bool, int, float, str, bytes):
                return False
    except (KeyError, AttributeError, TypeError):
        return False
    return True


def main():
    request = json.load(sys.stdin)
    root = Path(sys.argv[1])
    name = request['path'][:-3].replace('/', '.')
    if name.endswith('.__init__'):
        name = name[:-9]
    filename = str(root / request['path'])
    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return None
        def exec_module(self, module):
            code = compile(request['source'], filename, 'exec', dont_inherit=True)
            exec(code, module.__dict__)
    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == name:
                options = {'submodule_search_locations':[str(Path(filename).parent)]} if request['path'].endswith('/__init__.py') else {}
                return importlib.util.spec_from_file_location(fullname, filename, loader=Loader(), **options)
            return None
    sys.path.insert(0, str(root))
    sys.meta_path.insert(0, Finder())
    module = importlib.import_module(name)
    if request.get('scalar_fields'):
        print('blast-scalar-ok' if scalar_namespace(module, request['scalar_fields']) else 'blast-scalar-unsupported')
    print('blast-import-ok')


if __name__ == '__main__':
    main()
