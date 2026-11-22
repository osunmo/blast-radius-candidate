"""Label-free per-patch fallback diagnostics; never reads answers.

python tools/analyze_generalization.py --data DATA --source PINNED_SOURCE
"""
import argparse
from collections import Counter
from contextlib import redirect_stderr
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.selector import Selector
from src.scheduler import Scheduler


def catalog_report(tests, baseline):
    ordered = sorted(tests,key=lambda row:row['number'])
    ids = [row['id'] for row in ordered]
    current = {row['id']:row['number'] for row in ordered}
    return {'exact_order':ids == baseline,
            'same_unique_ids':len(ids)==len(set(ids)) and len(baseline)==len(set(baseline)) and set(ids)==set(baseline),
            'unknown_ids':sorted(set(ids)-set(baseline)),
            'missing_ids':sorted(set(baseline)-set(ids)),
            'baseline_to_input':[current.get(node) for node in baseline]}


def diagnose(data,source,artifacts,mode='fine'):
    tests=json.loads((data/'tests.json').read_text(encoding='utf-8'))['tests']
    with gzip.open(artifacts/'coverage.json.gz','rt',encoding='utf-8') as stream:
        baseline=json.load(stream)
    report={'catalog':catalog_report(tests,baseline['catalog']),'patches':[]}
    selector=Selector(tests,source,artifacts,mode=mode,explain=True)
    durations={row['number']:round(row['seconds']*1000) for row in tests}
    scheduler=Scheduler(durations)
    reasons=Counter()
    for patch in sorted((data/'commits').glob('*.patch')):
        text=patch.read_text(encoding='utf-8')
        stream=io.StringIO()
        started=time.process_time()
        with redirect_stderr(stream):
            selected=selector.select(text)
        workers=scheduler.schedule(selected)
        trace=stream.getvalue().splitlines()
        fallback=[line for line in trace if 'full suite' in line]
        reasons.update(fallback)
        cost=max((sum(durations[n] for n in worker) for worker in workers),default=0)+2000*len(workers)
        report['patches'].append({'commit':patch.stem,'sha256':hashlib.sha256(text.encode()).hexdigest(),
            'selected':len(selected),'workers':len(workers),'cost_ms':cost,
            'cpu_seconds':time.process_time()-started,'fallback':fallback,'trace':trace})
    report['fallback_reasons']=dict(reasons)
    report['simulated_without_miss_penalties_ms']=sum(row['cost_ms'] for row in report['patches'])
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--artifacts',type=Path,default=Path(__file__).resolve().parents[1]/'artifacts')
    parser.add_argument('--mode',default='fine')
    args=parser.parse_args()
    print(json.dumps(diagnose(args.data,args.source,args.artifacts,args.mode),indent=2))
