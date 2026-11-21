"""Import one reconstructed module in a fresh interpreter with an in-memory loader."""
import importlib
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path


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
                return importlib.util.spec_from_file_location(fullname, filename, loader=Loader())
            return None
    sys.path.insert(0, str(root))
    sys.meta_path.insert(0, Finder())
    importlib.import_module(name)
    print('blast-import-ok')


if __name__ == '__main__':
    main()
