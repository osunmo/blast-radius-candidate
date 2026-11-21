"""Finalize complete parent and verified worker dependency maps."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from build_maps import BASE, compact


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def finish(data, workers, field, catalog, manifest, verified):
    if data['base'] != BASE or workers['base'] != BASE or data['catalog'] != catalog or workers['catalog'] != catalog:
        raise ValueError('Baseline/catalog mismatch')
    ids = {name:i for i,name in enumerate(catalog)}
    declared = sum(1<<ids[name] for name in manifest['opaque_child_process'])
    verified_mask = sum(1<<ids[name] for name in set(verified))
    protected = int(data['untrusted'],16)
    always = protected | ((int(data['always_include'],16)|declared)&~verified_mask) | int(workers['always_include'],16)
    for pool in manifest.get('pool_checks',[]):
        members = sum(1<<ids[name] for name in pool['items'])
        if members & protected:
            always |= members
    if field == 'classes':
        for key,value in workers[field].items():
            data[field][key] = format(int(data[field].get(key,'0'),16)|int(value,16),'x')
    else:
        for site,pairs in workers[field].items():
            target = data[field].setdefault(site,{})
            for pair,value in pairs.items():
                target[pair] = format(int(target.get(pair,'0'),16)|int(value,16),'x')
    data['always_include'] = format(always,'x')
    data['complete'] = True
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--classes',type=Path)
    parser.add_argument('--operands',type=Path,required=True)
    parser.add_argument('--workers',type=Path,required=True)
    parser.add_argument('--coverage-manifest',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    catalog = [row['id'] for row in json.loads(args.catalog.read_bytes())['tests']]
    manifest = json.loads(args.coverage_manifest.read_bytes())
    worker_manifest = json.loads((args.workers/'classes-workers.json').read_bytes())
    args.out.mkdir(exist_ok=True,parents=True)
    facts = {}
    for name,path,field,worker_name in (
        ('classes',args.classes,'classes','classes.json.gz'),
        ('operands',args.operands,'sites','operands-workers.json.gz')):
        if path is None:
            continue
        data = finish(read(path),read(args.workers/worker_name),field,catalog,manifest,worker_manifest['verified'])
        encoded = gzip.compress(compact(data),mtime=0)
        (args.out/(name+'.json.gz')).write_bytes(encoded)
        facts[name]={'bytes':len(encoded),'sha256':hashlib.sha256(encoded).hexdigest(),
                     'always_include':int(data['always_include'],16).bit_count(),'entries':len(data[field])}
    (args.out/'consumers-manifest.json').write_text(json.dumps({'base':BASE,'catalog':len(catalog),
        'verified_workers':worker_manifest['verified'],'facts':facts},indent=2))
