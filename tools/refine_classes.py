"""Merge verified worker-lifetime class consumers into a baseline class map."""
import argparse
import atexit
import concurrent.futures
import gzip
import hashlib
import importlib
import json
import multiprocessing
import multiprocessing.process
import multiprocessing.util
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from build_maps import BASE, compact, prepare_source
from build_classes import Classes
from refine_children import Replay
from build_operands import Contexts
from operand_trace import Observer, encoded_sites


def import_package(source):
    sys.path.insert(0, str(source))
    for file in sorted((source/'sqlglot').rglob('*.py')):
        parts = file.relative_to(source).with_suffix('').parts
        if parts[-1] == '__main__':
            continue
        importlib.import_module('.'.join(parts[:-1] if parts[-1] == '__init__' else parts))


def trace_child():
    target = Path(os.environ.pop('BLAST_CHILD_REPORT'))
    source = Path(os.environ['BLAST_CHILD_SOURCE'])
    tracker = Classes(source, {'child':0})
    tracker.group = 1
    observer = Observer(source, tracker)
    observer.install()
    import_package(source)
    tracker.start()
    original_start = multiprocessing.process.BaseProcess.start
    def nested_start(process):
        tracker.opaque |= tracker.all
        return original_start(process)
    multiprocessing.process.BaseProcess.start = nested_start
    def finish():
        if not tracker.active:
            return
        tracker.stop()
        target.write_bytes(gzip.compress(compact({'base':BASE,'pid':os.getpid(),
            'opaque':bool(tracker.opaque),'classes':list(tracker.classes),
            'sites':encoded_sites(observer)}),mtime=0))
    atexit.register(finish)
    multiprocessing.util.Finalize(None,finish,exitpriority=-999)


class ClassReplay(Replay, Classes):
    start = Classes.start
    stop = Classes.stop
    switch = Classes.switch

    def __init__(self, source, ids, directory):
        super().__init__(source, ids, directory)
        (self.hook/'sitecustomize.py').write_text(
            "import os\nif 'BLAST_CHILD_REPORT' in os.environ:\n"
            "    from refine_classes import trace_child\n    trace_child()\n",encoding='utf-8')


def main(args):
    out = args.out.resolve()
    payload = json.loads(gzip.decompress((out/'classes.json.gz').read_bytes()))
    catalog = json.loads(args.catalog.read_bytes())['tests']
    ids = {row['id']:row['number'] for row in catalog}
    if payload['base'] != BASE or payload['catalog'] != list(ids):
        raise ValueError('Class map/catalog mismatch')
    manifest = json.loads(args.coverage_manifest.read_bytes())
    targets = manifest['opaque_child_process']
    source = prepare_source(args)
    os.chdir(source)
    os.environ.update(SKIP_INTEGRATION='0',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    initial_context = Contexts(source, ids)
    observer = Observer(source, initial_context)
    observer.install()
    import_package(source)
    original = multiprocessing.process.BaseProcess.start
    init_pool = concurrent.futures.ProcessPoolExecutor.__init__
    submit_pool = concurrent.futures.ProcessPoolExecutor.submit
    spawn_pool = concurrent.futures.ProcessPoolExecutor._spawn_process
    bad = int(payload['always_include'],16)
    protected = int(payload.get('untrusted', payload['always_include']), 16)
    verified = []
    worker_sites = {}
    with tempfile.TemporaryDirectory(prefix='blast-classes-') as directory:
        replay = ClassReplay(source,ids,Path(directory))
        observer.context = replay
        def start(process):
            return replay.launch(original,process)
        def initialize(pool,*args,**kwargs):
            replay.pool_owner[pool] = replay.group
            return init_pool(pool,*args,**kwargs)
        def submit(pool,*args,**kwargs):
            if pool not in replay.pool_owner or replay.group.bit_count() != 1:
                replay.foreign |= replay.group or replay.all
                node = ''
            else:
                node = payload['catalog'][replay.group.bit_length()-1]
            return replay.jobs.submit(submit_pool,pool,node,args,kwargs)
        def spawn(pool):
            replay.spawning.pool=pool
            try:
                return spawn_pool(pool)
            finally:
                replay.spawning.pool=None
        replay.start()
        try:
            with (patch.object(multiprocessing.process.BaseProcess,'start',start),
                  patch.object(concurrent.futures.ProcessPoolExecutor,'__init__',initialize),
                  patch.object(concurrent.futures.ProcessPoolExecutor,'submit',submit),
                  patch.object(concurrent.futures.ProcessPoolExecutor,'_spawn_process',spawn)):
                status=pytest.main([*targets,'-q','--tb=line','-p','no:cacheprovider'],plugins=[replay])
        finally:
            replay.stop()
        if status not in (0,1) or replay.collection_errors:
            raise RuntimeError('Worker class replay incomplete')
        for key,bits in replay.classes.items():
            payload['classes'][key]=format(int(payload['classes'].get(key,'0'),16)|bits,'x')
        groups=[]
        for pool,jobs in replay.jobs.tasks.items():
            names=list(jobs)
            if not replay.jobs.signature(pool) or '' in names:
                replay.foreign |= sum(1<<ids[name] for name in names if name)
                continue
            groups.append((names,replay.pool_children[pool]))
        for bit,children in replay.started.items():
            groups.append(([payload['catalog'][bit.bit_length()-1]],children))
        done=set()
        for names,children in groups:
            mask=sum(1<<ids[name] for name in names)
            if not children or mask & replay.foreign or any(name in replay.nonpassing for name in names):
                bad |= mask
                continue
            records=[]
            for process,pid,file in children:
                try:
                    successful=process.exitcode==0
                except ValueError:
                    successful=False
                if not successful or not file.is_file():
                    break
                record=json.loads(gzip.decompress(file.read_bytes()))
                if record['base']!=BASE or record['pid']!=pid or record['opaque'] or not record['classes']:
                    break
                records.append(record)
            if len(records)!=len(children):
                bad |= mask
                continue
            for record in records:
                for key in record['classes']:
                    payload['classes'][key]=format(int(payload['classes'].get(key,'0'),16)|mask,'x')
                for site, pairs in record['sites'].items():
                    target = worker_sites.setdefault(site, {})
                    for pair in pairs:
                        target[pair] = target.get(pair, 0) | mask
            done.update(names)
            bad &= ~(mask & ~protected)
            verified.extend(names)
        for name in targets:
            if name not in done:
                bad |= 1<<ids[name]
    payload['always_include']=format(bad,'x')
    encoded=gzip.compress(compact(payload),mtime=0)
    (out/'classes.json.gz').write_bytes(encoded)
    (out/'classes-workers.json').write_text(json.dumps({'base':BASE,'verified':sorted(set(verified)),
        'always_include':[name for name,i in ids.items() if bad&(1<<i)],'sha256':hashlib.sha256(encoded).hexdigest()},indent=2))
    for site, pairs in observer.sites.items():
        target = worker_sites.setdefault(site, {})
        for pair, mask in pairs.items():
            target[pair] = target.get(pair, 0) | mask
    extra = {'base':BASE,'catalog':payload['catalog'],'verified':sorted(set(verified)),
             'always_include':format(bad,'x'),
             'sites':{site:{pair:format(mask,'x') for pair,mask in pairs.items()}
                      for site,pairs in worker_sites.items()}}
    (out/'operands-workers.json.gz').write_bytes(gzip.compress(compact(extra),mtime=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--coverage-manifest',type=Path,required=True)
    main(parser.parse_args())
