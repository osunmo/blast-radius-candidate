"""Verify dependencies of baseline isolated Python -c subprocess tests.

Produces supplemental evidence only; does not modify the shipped dependency map.
Each child must match an independent uninstrumented launch, finish successfully,
report its exact PID/nonce, and launch no descendants. Unsupported launches stay
opaque. Input targets are selected from baseline process uncertainty, not labels.
"""
import argparse
import ast
import atexit
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

PROCESS_EVENTS = {'subprocess.Popen', 'os.fork', 'os.forkpty', 'os.system',
                  'os.posix_spawn', 'os.exec', '_winapi.CreateProcess', '_posixsubprocess.fork_exec'}


def python_command(command):
    """Only same-interpreter, environment-respecting isolated -c commands."""
    if not isinstance(command, (list, tuple)) or len(command) != 3 or command[1] != '-c':
        return False
    executable = shutil.which(os.fsdecode(command[0]))
    return bool(executable and Path(executable).resolve() == Path(sys.executable).resolve()
                and isinstance(command[2], str))


def register_class(value, classes, seen_classes):
    if not isinstance(value, type):
        return
    if id(value) in seen_classes:
        return
    seen_classes.add(id(value))
    for base in type.__getattribute__(value,'__bases__'):
        register_class(base, classes, seen_classes)
    module = type.__getattribute__(value, '__module__')
    qualname = type.__getattribute__(value, '__qualname__')
    if module == 'sqlglot' or module.startswith('sqlglot.'):
        key = module + ':' + qualname
        classes.add(key)
        for nested in type.__getattribute__(value, '__dict__').values():
            if isinstance(nested, type):
                register_class(nested, classes, seen_classes)


def child_trace():
    from build_maps import BASE, Collector, compact
    target = Path(os.environ.pop('BLAST_LAZY_REPORT'))
    nonce = os.environ.pop('BLAST_LAZY_NONCE')
    source = Path(os.environ['BLAST_LAZY_SOURCE'])
    if any(name == 'sqlglot' or name.startswith('sqlglot.') for name in sys.modules):
        return
    class ChildCollector(Collector):
        def audit(self,event,args):
            if event == 'open' and isinstance(args[0], (str,bytes)) and os.fsdecode(args[0]).endswith('.py'):
                frame=sys._getframe(1)
                while frame and frame.f_globals.get('__name__','').split('.')[0] == 'pathlib':
                    frame=frame.f_back
                if frame and frame.f_code.co_name == 'exec_module' and frame.f_code.co_filename == str(Path(__file__).with_name('operand_trace.py')):
                    return  # Instrumented import source, not a user text read.
            return super().audit(event,args)
    tracker = ChildCollector(source, {'child':0}, explain_processes=True)
    from operand_trace import Observer, encoded_sites
    operand_observer=Observer(source,tracker)
    operand_observer.install()
    tracker.group = 1
    tracker.start()

    def finish():
        if not tracker.active:
            return
        tracker.stop()
        classes = set()
        seen_classes = set()
        modules = []
        for name, module in list(sys.modules.items()):
            if module and (name == 'sqlglot' or name.startswith('sqlglot.')):
                modules.append(name)
                filename=getattr(module,'__file__',None)
                if filename and filename.endswith('.py'):
                    def declared(node,prefix=()):
                        if isinstance(node,ast.ClassDef):
                            prefix=(*prefix,node.name)
                            classes.add(name+':'+'.'.join(prefix))
                        for child in ast.iter_child_nodes(node):
                            if isinstance(child,(ast.Module,ast.ClassDef)):
                                declared(child,prefix)
                    declared(ast.parse(Path(filename).read_bytes()))
                for value in vars(module).values():
                    register_class(value, classes, seen_classes)
        lines = {p:sorted(set(entries) | tracker.contextless.get(p,set()))
                 for p,entries in tracker.observed.items()}
        for p,entries in tracker.contextless.items():
            lines[p] = sorted(set(lines.get(p,[])) | entries)
        result = {'version':2,'operands_complete':True,'classes_complete':True,'pid':os.getpid(), 'nonce':nonce, 'base':BASE,
                  'nested_process':bool(tracker.opaque),
                  'lines':lines, 'reads':sorted(tracker.reads),
                  'classes':sorted(classes), 'modules':sorted(modules),
                  'operand_sites':encoded_sites(operand_observer)}
        target.write_bytes(gzip.compress(compact(result), mtime=0))
    atexit.register(finish)


def verify_report(record, pid, nonce, base):
    return (isinstance(record,dict) and record.get('version') == 2
            and record.get('operands_complete') is True and record.get('classes_complete') is True
            and isinstance(record.get('operand_sites'),dict) and record.get('pid') == pid
            and record.get('nonce') == nonce and record.get('base') == base
            and record.get('nested_process') is False
            and isinstance(record.get('lines'),dict)
            and any(path.startswith('sqlglot/') for path in record['lines'])
            and isinstance(record.get('reads'),list)
            and isinstance(record.get('classes'),list))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--coverage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    source=a.source.resolve(); output=a.output.resolve()
    catalog=json.loads(a.catalog.read_text())['tests']
    payload=json.loads(gzip.decompress(a.coverage.read_bytes()))
    if payload['catalog'] != [row['id'] for row in catalog]:
        raise ValueError('catalog mismatch')
    from build_maps import BASE, Collector
    if payload['base'] != BASE:
        raise ValueError('revision mismatch')
    opaque=int(payload['always_include'],16)
    ids={row['id']:row['number'] for row in catalog}
    targets=[row['id'] for row in catalog if opaque & (1 << row['number'])]
    if not targets:
        raise ValueError('no opaque baseline test contexts')
    os.chdir(source); sys.path.insert(0,str(source))
    os.environ.update(SKIP_INTEGRATION='0',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    import pytest
    original=subprocess.Popen.__init__

    class Replay(Collector):
        def __init__(self,directory):
            super().__init__(source,ids)
            self.expected=False; self.children=[]; self.reject=[]; self.directory=directory
            self.hook=directory/'hook';self.hook.mkdir()
            (self.hook/'sitecustomize.py').write_text(
                "import os\nif 'BLAST_LAZY_REPORT' in os.environ:\n"
                "    from refine_lazy import child_trace\n    child_trace()\n")

        def audit(self,event,args):
            if event in PROCESS_EVENTS and self.expected:
                return
            return super().audit(event,args)

        def launch(self,process,command,*args,**kwargs):
            bit=self.group
            if not python_command(command) or bit.bit_count()!=1 or kwargs.get('shell') or kwargs.get('executable'):
                self.reject.append('unsupported launch'); self.opaque |= bit or self.all
                return original(process,command,*args,**kwargs)
            nonce=uuid.uuid4().hex; target=self.directory/(nonce+'.json.gz')
            # Independently execute exactly the original command before the
            # instrumented launch, with the caller's original environment.
            reference=subprocess.Popen.__new__(subprocess.Popen)
            self.expected=True
            try:
                original(reference,command,*args,**kwargs)
                reference_out,reference_err=reference.communicate(timeout=30)
                env=dict(kwargs.get('env') or os.environ)
                env.update(BLAST_LAZY_REPORT=str(target),BLAST_LAZY_NONCE=nonce,
                           BLAST_LAZY_SOURCE=str(source))
                search=[str(self.hook),str(Path(__file__).resolve().parent)]
                if env.get('PYTHONPATH'):search.append(env['PYTHONPATH'])
                env['PYTHONPATH']=os.pathsep.join(search)
                instrumented=dict(kwargs,env=env)
                result=original(process,command,*args,**instrumented)
            finally:
                self.expected=False
            self.children.append((bit,process,target,nonce,reference.returncode,reference_out,reference_err))
            return result

    with tempfile.TemporaryDirectory(prefix='blast-lazy-') as directory:
        replay=Replay(Path(directory)); replay.start()
        try:
            def launch(process,*args,**kwargs):
                return replay.launch(process,*args,**kwargs)
            with patch.object(subprocess.Popen,'__init__',launch):
                status=pytest.main([*targets,'-q','--tb=line','-p','no:cacheprovider'],plugins=[replay])
        finally:
            replay.stop()
        resolved={}; rejected=list(replay.reject)
        for bit,process,target,nonce,exitcode,out,err in replay.children:
            # communicate() was called by the test; compare retained pipe data
            # via a wrapper capture stored below rather than reading again.
            actual=getattr(process,'_blast_lazy_output',None)
            try:record=json.loads(gzip.decompress(target.read_bytes()))
            except (OSError,EOFError,ValueError):record=None
            if (exitcode!=0 or process.returncode!=0 or actual!=(out,err)
                    or not verify_report(record,process.pid,nonce,BASE)):
                rejected.append('child exit/output/identity/report mismatch');replay.opaque |= bit
                continue
            resolved.setdefault(str(bit),[]).append(record)
        # Preserve every opaque context if collection/test/lifecycle validation
        # was incomplete. Caller may merge only explicitly verified groups.
        complete=status==0 and not replay.collection_errors and not rejected
        successful={int(bit):records for bit,records in resolved.items()
                    if not int(bit)&replay.opaque and complete}
        results={'version':2,'producer':'refine_lazy-v2','status':'passed' if complete else 'unverified','base':BASE,'catalog':payload['catalog'],
                 'complete':complete,'rejected':rejected,'groups':{}}
        for bit,records in successful.items():
            lines={};reads=set();classes=set();modules=set();operand_sites={}
            for path,entries in replay.observed.items():
                hit=[line for line,bits in entries.items() if bits&bit]
                if hit:lines[path]=set(hit)
            for path,bits in replay.reads.items():
                if bits&bit:reads.add(path)
            for record in records:
                for path,entries in record['lines'].items():lines.setdefault(path,set()).update(entries)
                reads.update(record['reads']);classes.update(record['classes']);modules.update(record['modules'])
                for site,pairs in record.get('operand_sites',{}).items():
                    domain=operand_sites.setdefault(site,{})
                    for signature in pairs:
                        domain[signature]=format(bit,'x')
            results['groups'][format(bit,'x')]={'lines':{p:sorted(v) for p,v in lines.items()},
                'reads':sorted(reads),'classes':sorted(classes),'modules':sorted(modules),
                'children':len(records),'operand_sites':operand_sites,
                'operands_complete':True,'classes_complete':True}
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(results,sort_keys=True),encoding='utf-8')
        print('Verified subprocess groups:',len(results['groups']))
        return 0 if results['groups'] else 1


if __name__=='__main__':
    # Store outputs without changing what the caller observes.
    communicate=subprocess.Popen.communicate
    def capture(self,*args,**kwargs):
        result=communicate(self,*args,**kwargs)
        self._blast_lazy_output=result
        return result
    with patch.object(subprocess.Popen,'communicate',capture):
        raise SystemExit(main())
