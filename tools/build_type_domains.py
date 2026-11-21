"""Describe finite enum domains and guard reflective constructor mutations."""
import argparse
import ast
import enum
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path
from build_maps import BASE, compact, prepare_source
from refine_classes import import_package


def build(source, coverage, catalog):
    guard = 0
    by_file = {}
    for row in catalog:
        by_file[row['file']] = by_file.get(row['file'],0) | (1<<row['number'])
    bypass = []
    protected = {'__eq__','__ne__','__new__','__bases__','_member_map_','_value2member_map_','_new_member_'}
    for folder in ('sqlglot','tests'):
        for file in (source/folder).rglob('*.py'):
            path = file.relative_to(source).as_posix()
            entry = coverage['files'].get(path, {'lines':{},'global':[]})
            tree = ast.parse(file.read_bytes())
            def affected(node):
                if node.lineno in entry['global']:
                    return by_file.get(path, (1<<len(catalog))-1)
                return int(entry['lines'].get(str(node.lineno),'0'),16)
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='__new__':
                    receiver=node.func.value
                    if not (isinstance(receiver,ast.Call) and isinstance(receiver.func,ast.Name) and receiver.func.id=='super'):
                        bypass.append(f'{path}:{node.lineno}')
                if (isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='getattr'
                        and len(node.args)>1 and isinstance(node.args[1],ast.Constant)
                        and node.args[1].value in {'__new__','_new_member_'}):
                    bypass.append(f'{path}:{node.lineno}')
                if isinstance(node,ast.Attribute) and isinstance(node.ctx,(ast.Store,ast.Del)) and node.attr in protected:
                    guard |= affected(node)
                if isinstance(node,ast.Call):
                    args = None
                    if isinstance(node.func,ast.Name) and node.func.id in {'setattr','delattr'}:
                        args=node.args
                    elif isinstance(node.func,ast.Attribute) and node.func.attr=='object' and isinstance(node.func.value,ast.Attribute) and node.func.value.attr=='patch':
                        args=node.args
                    if args is not None:
                        name=args[1] if len(args)>1 else None
                        if not isinstance(name,ast.Constant) or not isinstance(name.value,str) or name.value in protected:
                            guard |= affected(node)
    domains = {}
    seen = set()
    for module in list(sys.modules.values()):
        if not getattr(module,'__name__','').startswith('sqlglot'):
            continue
        for cls in vars(module).values():
            if type(cls) is not enum.EnumType or not cls.__module__.startswith('sqlglot'):
                continue
            identity=object.__hash__(cls)
            if identity in seen:
                continue
            seen.add(identity)
            members = list({object.__hash__(member):member for member in cls.__members__.values()}.values())
            if not members or len(members)>16:
                continue
            if (cls.__new__ is not enum.Enum.__new__
                    or cls._missing_.__func__ is not enum.Enum._missing_.__func__
                    or cls.__init__ is not enum.Enum.__init__):
                continue
            if cls.__eq__ is str.__eq__ and cls.__ne__ is str.__ne__ and cls._new_member_ is str.__new__:
                values=[str.__str__(member) for member in members]
                kind='canonical-str-enum'
            elif cls.__eq__ is int.__eq__ and cls.__ne__ is int.__ne__ and cls._new_member_ is int.__new__:
                values=[int.__int__(member) for member in members]
                kind='canonical-int-enum'
            else:
                continue
            if len(set(values)) != len(members):
                continue
            domains[cls.__module__+'.'+cls.__qualname__]={'kind':kind,'members':values}
    if bypass:
        domains={}
    return {'base':BASE,'catalog':[row['id'] for row in catalog], 'enums':domains,
            'always_include':format(guard,'x'),'constructor_bypasses':bypass}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--coverage',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    coverage=json.loads(gzip.decompress(args.coverage.read_bytes()))
    catalog=json.loads(args.catalog.read_bytes())['tests']
    if coverage['base']!=BASE or coverage['catalog']!=[row['id'] for row in catalog]:
        raise ValueError('Baseline mismatch')
    source=prepare_source(args)
    os.chdir(source)
    import_package(source)
    data=build(source,coverage,catalog)
    encoded=gzip.compress(compact(data),mtime=0)
    args.out.mkdir(exist_ok=True,parents=True)
    (args.out/'type-domains.json.gz').write_bytes(encoded)
    (args.out/'type-domains-manifest.json').write_text(json.dumps({'base':BASE,'domains':len(data['enums']),
        'guarded':int(data['always_include'],16).bit_count(),'bypasses':data['constructor_bypasses'],
        'sha256':hashlib.sha256(encoded).hexdigest()},indent=2))
