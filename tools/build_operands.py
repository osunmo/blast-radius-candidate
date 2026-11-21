"""Profile baseline comparison operand types with the fixed catalog."""
import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from pathlib import Path
import pytest
from build_maps import BASE, Collector, compact, prepare_source
from operand_trace import Observer, encoded_sites


class Contexts(Collector):
    def switch(self, group):
        self.group = group
    def start(self):
        sys.addaudithook(self.audit)
    def stop(self):
        self.active = False


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    source=prepare_source(args)
    catalog=json.loads(args.catalog.read_bytes())['tests']
    ids={row['id']:row['number'] for row in catalog}
    os.chdir(source)
    sys.path.insert(0,str(source))
    os.environ.update(SKIP_INTEGRATION='0',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    tracker=Contexts(source,ids)
    observer=Observer(source,tracker)
    observer.install()
    start=time.monotonic()
    tracker.start()
    try:
        status=pytest.main(['tests','-q','--tb=line','-p','no:cacheprovider'],plugins=[tracker])
    finally:
        tracker.stop()
    if status not in (0,1) or tracker.collection_errors:
        raise RuntimeError('Operand collection incomplete')
    untrusted={name for name in ids if tracker.passed_phases[name]!={'setup','call','teardown'}}|tracker.nonpassing
    mask=sum(1<<ids[name] for name in untrusted if name in ids)
    data={'base':BASE,'complete':False,'catalog':[row['id'] for row in catalog],'sites':encoded_sites(observer),
          'untrusted':format(mask,'x'),'always_include':format(mask|tracker.opaque,'x')}
    encoded=gzip.compress(compact(data),mtime=0)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'operands.json.gz').write_bytes(encoded)
    (args.out/'operands-manifest.json').write_text(json.dumps({'base':BASE,'python':sys.version,
        'elapsed':time.monotonic()-start,'untrusted':sorted(untrusted),'sites':len(data['sites']),
        'sha256':hashlib.sha256(encoded).hexdigest()},indent=2))
