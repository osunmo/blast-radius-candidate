"""Merge certified isolated-Python child evidence into a separate artifact copy."""
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


def compact(data):
    return json.dumps(data,sort_keys=True,separators=(',',':')).encode()


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def safe_path(path):
    pure=PurePosixPath(path)
    return bool(path and not pure.is_absolute() and '..' not in pure.parts and '\\' not in path and ':' not in path)


def apply_report(coverage,classes,operands,report,protected_coverage=0):
    """Return independent updated maps; retain every unrelated/protected bit."""
    if (report.get('version')!=2 or report.get('producer')!='refine_lazy-v2'
            or report.get('status')!='passed' or report.get('complete') is not True
            or report.get('rejected')!=[] or not report.get('groups')):
        raise ValueError('unverified producer report')
    maps=[copy.deepcopy(data) for data in (coverage,classes,operands)]
    coverage,classes,operands=maps
    catalog=coverage['catalog'];base=coverage['base'];allmask=(1<<len(catalog))-1
    if any(data['base']!=base or data['catalog']!=catalog for data in (classes,operands,report)):
        raise ValueError('baseline/catalog mismatch')
    if classes.get('complete') is not True or operands.get('complete') is not True:
        raise ValueError('incomplete input consumer maps')
    def bits(value):
        mask=int(value,16)
        if mask<0 or mask&~allmask:raise ValueError('out of catalog bits')
        return mask
    verified=0
    for encoded,group in report['groups'].items():
        mask=bits(encoded)
        if not mask or verified&mask:raise ValueError('empty or overlapping groups')
        if (type(group.get('children')) is not int or group['children']<1
                or group.get('operands_complete') is not True or group.get('classes_complete') is not True
                or not isinstance(group.get('operand_sites'),dict)):
            raise ValueError('missing child capability certification')
        verified|=mask
        for path,lines in group['lines'].items():
            if not safe_path(path) or not isinstance(lines,list):raise ValueError('unsafe source path')
            target=coverage['files'].setdefault(path,{'lines':{},'global':[]})['lines']
            for line in lines:
                if type(line) is not int or line<=0:raise ValueError('invalid source line')
                target[str(line)]=format(bits(target.get(str(line),'0'))|mask,'x')
        for path in group['reads']:
            if not safe_path(path):raise ValueError('unsafe reader path')
            coverage['fixtures'][path]=format(bits(coverage['fixtures'].get(path,'0'))|mask,'x')
        for name in group['classes']:
            if not isinstance(name,str) or ':' not in name:raise ValueError('invalid class key')
            classes['classes'][name]=format(bits(classes['classes'].get(name,'0'))|mask,'x')
        for site,pairs in group['operand_sites'].items():
            if not isinstance(site,str) or not isinstance(pairs,dict):raise ValueError('invalid operand site')
            target=operands['sites'].setdefault(site,{})
            for signature,encoded_consumers in pairs.items():
                domain=json.loads(signature)
                if (not isinstance(domain,list) or len(domain)!=6
                    or not all(isinstance(value,str) for value in domain[:2])
                    or not all(type(value) is bool for value in domain[2:])
                    or bits(encoded_consumers)!=mask):raise ValueError('invalid child operand domain')
                target[signature]=format(bits(target.get(signature,'0'))|mask,'x')
    coverage['always_include']=format((bits(coverage['always_include'])&~verified)|protected_coverage,'x')
    for data in (classes,operands):
        data['always_include']=format((bits(data['always_include'])&~verified)|bits(data.get('untrusted','0')),'x')
    return coverage,classes,operands,verified


def merge(source,report_path,destination):
    source=source.resolve();destination=destination.resolve()
    if source==destination or destination.is_relative_to(source):
        raise ValueError('destination must be a separate artifact copy')
    report=json.loads(report_path.read_bytes())
    manifest=json.loads((source/'manifest.json').read_bytes())
    original=(source/'coverage.json.gz').read_bytes()
    if hashlib.sha256(original).hexdigest()!=manifest['map_sha256']:
        raise ValueError('coverage provenance digest mismatch')
    coverage=json.loads(gzip.decompress(original));classes=read(source/'classes.json.gz');operands=read(source/'operands.json.gz')
    consumers=json.loads((source/'consumers-manifest.json').read_bytes())
    for name in ('classes','operands'):
        expected=consumers['facts'][name]['sha256']
        if hashlib.sha256((source/(name+'.json.gz')).read_bytes()).hexdigest()!=expected:
            raise ValueError('consumer provenance digest mismatch')
    ids={name:i for i,name in enumerate(coverage['catalog'])}
    protected=int(coverage.get('unmapped','0'),16)
    for name in manifest.get('nonpassing_or_missing_phase',[]):
        if name in ids:protected|=1<<ids[name]
    coverage,classes,operands,verified=apply_report(coverage,classes,operands,report,protected)
    shutil.copytree(source,destination)
    names=[name for name,number in ids.items() if verified&(1<<number)]
    for name,data in (('coverage',coverage),('classes',classes),('operands',operands)):
        raw=compact(data);encoded=gzip.compress(raw,mtime=0)
        (destination/(name+'.json.gz')).write_bytes(encoded)
        sha=hashlib.sha256(encoded).hexdigest()
        if name=='coverage':
            manifest.update(map_sha256=sha,json_bytes=len(raw),gzip_bytes=len(encoded),
                always_include_count=int(data['always_include'],16).bit_count(),
                files_with_coverage=len(data['files']),recorded_reads=len(data['fixtures']),
                global_lines=sum(len(value['global']) for value in data['files'].values()),
                attributed_lines=sum(len(value['lines']) for value in data['files'].values()))
        else:
            field='classes' if name=='classes' else 'sites'
            consumers['facts'][name]={'bytes':len(encoded),'sha256':sha,
                'always_include':int(data['always_include'],16).bit_count(),'entries':len(data[field])}
    links_path=destination/'classes-links.json'
    if links_path.is_file():
        links=json.loads(links_path.read_bytes())
        links['sha256']=consumers['facts']['classes']['sha256']
        links_path.write_text(json.dumps(links,sort_keys=True,indent=2)+'\n')
    evidence={'producer':'refine_lazy-v2','report_sha256':hashlib.sha256(report_path.read_bytes()).hexdigest(),
              'verified_tests':names,'verified_groups':len(report['groups'])}
    manifest['isolated_subprocess_refinement']=evidence
    consumers['isolated_subprocess_refinement']=evidence
    (destination/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    (destination/'consumers-manifest.json').write_text(json.dumps(consumers,sort_keys=True,indent=2)+'\n')
    (destination/'lazy-manifest.json').write_text(json.dumps(dict(evidence,base=coverage['base'],
        catalog=len(coverage['catalog']),parent_maps_sha256={name:hashlib.sha256((source/name).read_bytes()).hexdigest()
        for name in ('coverage.json.gz','classes.json.gz','operands.json.gz')}),sort_keys=True,indent=2)+'\n')
    return len(names)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifacts',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    print('Merged verified tests:',merge(args.artifacts,args.report,args.out))
