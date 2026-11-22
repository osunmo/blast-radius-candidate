"""Certify exact primitive own-namespace values in a fresh pinned baseline process.

No patches or labels are read. Import errors deny all fields in that module.
"""
import argparse
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types


def build(data, importer=importlib.import_module):
    allowed = (type(None), bool, int, float, str, bytes)
    modules = {}
    errors = {}
    certified = []
    for key in sorted(data['fields']):
        module_name, owner, name = key.split(':')
        if module_name not in modules:
            try:
                modules[module_name] = importer(module_name)
            except Exception as error:
                modules[module_name] = None
                errors[module_name] = type(error).__name__ + ': ' + str(error)
        module = modules[module_name]
        if module is None:
            continue
        try:
            value = types.ModuleType.__getattribute__(module, '__dict__')[owner.split('.')[0]]
            for part in owner.split('.')[1:]:
                value = type.__getattribute__(value, '__dict__')[part]
            value = type.__getattribute__(value, '__dict__')[name]
            if type(value) in allowed:
                certified.append(key)
        except (KeyError, AttributeError, TypeError):
            pass
    return {'version':1, 'base':data['base'], 'catalog':data['catalog'],
            'complete':True, 'fields':certified, 'errors':errors,
            'policy':'exact primitive stored in baseline own class namespace; missing/error denies refinement'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--fields', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    raw = args.fields.read_bytes()
    data = json.loads(gzip.decompress(raw) if args.fields.suffix == '.gz' else raw)
    result = build(data)
    result['field_map_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(',',':'))+'\n', encoding='utf-8')
    print(json.dumps({'certified':len(result['fields']), 'modules_with_errors':len(result['errors']), 'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
