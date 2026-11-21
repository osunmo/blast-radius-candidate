"""Add source-derived class-reference consumers after dynamic profiling."""
import argparse
import gzip
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

from build_maps import BASE, compact, prepare_source
from build_classes import Classes
from refine_classes import import_package


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--coverage', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    coverage = json.loads(gzip.decompress(args.coverage.read_bytes()))
    data = json.loads(gzip.decompress((out/'classes.json.gz').read_bytes()))
    catalog = json.loads(args.catalog.read_bytes())['tests']
    if data['catalog'] != [row['id'] for row in catalog] or data['base'] != BASE or coverage['base'] != BASE:
        raise ValueError('Baseline map mismatch')
    source = prepare_source(args)
    os.chdir(source)
    os.environ['SKIP_INTEGRATION'] = '0'
    import_package(source)
    for path in sorted({row['file'] for row in catalog}):
        importlib.import_module(path[:-3].replace('/', '.'))
    tracker = Classes(source, {row['id']: row['number'] for row in catalog})
    tracker.test_references(source, catalog, coverage['files'])
    for key, mask in tracker.classes.items():
        data['classes'][key] = format(int(data['classes'].get(key, '0'), 16) | mask, 'x')
    encoded = gzip.compress(compact(data), mtime=0)
    (out/'classes.json.gz').write_bytes(encoded)
    (out/'classes-refs.json').write_text(json.dumps({'base':BASE,'classes':len(tracker.classes),
        'sha256':hashlib.sha256(encoded).hexdigest()},indent=2))
