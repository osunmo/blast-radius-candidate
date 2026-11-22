"""Baseline filesystem observations for non-code SQL fixture additions.

Run before package imports. Existing child-process contexts and native-library
consumers remain conservative guards; deletion is intentionally unsupported.
"""
import argparse
import ast
import functools
import gzip
import hashlib
import importlib.util
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path,PurePath,PosixPath,WindowsPath,PurePosixPath,PureWindowsPath


PROCESS_EVENTS={'subprocess.Popen','os.fork','os.forkpty','os.posix_spawn','os.system','os.exec','_winapi.CreateProcess','_posixsubprocess.fork_exec'}
MUTATION_EVENTS={'os.mkdir','os.rmdir','os.remove','os.rename','os.chdir','os.symlink','os.link'}
COLLECTION_HOOKS={'pytest_collect_file','pytest_collect_directory','pytest_ignore_collect'}


class Observations:
    def __init__(self,source,context):
        self.source=Path(source)
        self.root=os.path.normcase(str(self.source))+os.sep
        self.context=context
        self.directories={}
        self.probes={}
        self.opens={}
        self.ignored=defaultdict(int)
        self.busy=False
        self.originals=[]
        self.original_readlink=os.readlink
        self.framework_filter=True

    def relative(self,value,dir_fd=None):
        if isinstance(value,int):
            value=self.original_readlink('/proc/self/fd/'+str(value))
        elif isinstance(value,bytes):
            value=os.fsdecode(value)
        elif type(value) in (Path,PosixPath,WindowsPath,PurePath,PurePosixPath,PureWindowsPath):
            value=str(value)
        elif not isinstance(value,str):
            raise ValueError('unresolved path argument')
        if not os.path.isabs(value) and dir_fd is not None:
            value=os.path.join(self.original_readlink('/proc/self/fd/'+str(dir_fd)),value)
        value=os.path.normcase(os.path.abspath(value))
        if value==self.root[:-1]:
            return '.'
        return os.path.relpath(value,self.root).replace(os.sep,'/') if value.startswith(self.root) else None

    def framework_only(self):
        framework=False
        frame=sys._getframe(2)
        while frame:
            filename=frame.f_code.co_filename.replace('\\','/')
            if os.path.normcase(filename).startswith(self.root.replace('\\','/')):
                return False
            framework |= ('/_pytest/' in filename or filename.startswith('<frozen importlib')
                          or '/importlib/' in filename)
            frame=frame.f_back
        return framework

    def record(self,table,path,dir_fd=None,kind='probe'):
        if self.busy or not self.context.active:
            return
        self.busy=True
        try:
            if self.framework_filter and self.framework_only():
                self.ignored[kind]+=1
                return
            try:
                relative=self.relative(path,dir_fd)
            except (OSError,ValueError,TypeError):
                self.context.opaque |= self.context.group or self.context.all
                return
            if relative is None or relative.startswith(('.git/','.pytest_cache/')):
                return
            table[relative]=table.get(relative,0)|(self.context.group or self.context.all)
        finally:
            self.busy=False

    def audit(self,event,args):
        if not self.context.active:
            return
        if event in PROCESS_EVENTS:
            self.context.opaque |= self.context.group or self.context.all
        elif event in {'os.listdir','os.scandir'}:
            self.record(self.directories,args[0] if args[0] is not None else '.',kind='enumeration')
        elif event=='open' and args:
            # Failed opens and O_EXCL/write collisions are dependencies too.
            self.record(self.opens,args[0],kind='open')
        elif event in MUTATION_EVENTS and args:
            fd=lambda value: None if value in (None,-1,-100) else value
            first=fd(args[2]) if event in {'os.mkdir','os.rename','os.link'} and len(args)>2 else fd(args[1]) if event in {'os.remove','os.rmdir'} and len(args)>1 else None
            self.record(self.probes,args[0],first,kind='mutation')
            if event in {'os.rename','os.link','os.symlink'} and len(args)>1:
                second=fd(args[3]) if event in {'os.rename','os.link'} and len(args)>3 else fd(args[2]) if len(args)>2 else None
                self.record(self.probes,args[1],second,kind='mutation')
        elif event=='import' and len(args)>1 and isinstance(args[1],str) and args[1].endswith(('.so','.pyd')):
            self.ignored['native_import:'+args[0].split('.')[0]]+=1

    def wrap(self,function):
        @functools.wraps(function)
        def observed(*args,**kwargs):
            path=args[0] if args else kwargs.get('path',None)
            if path is not None:
                self.record(self.probes,path,kwargs.get('dir_fd'),kind='stat')
            return function(*args,**kwargs)
        return observed

    def install(self):
        # Save implementations before modifying either os or its native backend.
        backend=sys.modules.get(os.name)
        for name in ('stat','lstat','access','readlink'):
            original=getattr(os,name,None)
            if original is None:
                continue
            replacement=self.wrap(original)
            for module in (os,backend):
                if module is not None and getattr(module,name,None) is original:
                    self.originals.append((module,name,original))
                    setattr(module,name,replacement)
        sys.addaudithook(self.audit)

    def stop(self):
        self.context.active=False
        for module,name,original in reversed(self.originals):
            setattr(module,name,original)


def boundaries(source,catalog):
    """Guard native-library users and disable discovery exclusions for hooks."""
    stdlib=set(sys.stdlib_module_names)
    trusted={'sqlglot','tests','pytest','_pytest','typing_extensions','__future__','mypy_extensions','pytz'}
    optional_absent={name for name in ('sqlglotrs',) if importlib.util.find_spec(name) is None}
    seeds=defaultdict(set)
    native_stdlib={'sqlite3','ctypes','dbm','readline'}
    reverse=defaultdict(set)
    files={}
    risky=set()
    collection_hooks=[]
    for file in [file for folder in ('sqlglot','tests') for file in (source/folder).rglob('*.py')]+([source/'conftest.py'] if (source/'conftest.py').is_file() else []):
        path=file.relative_to(source).as_posix()
        module=path[:-3].replace('/','.').removesuffix('.__init__')
        package=module if file.name=='__init__.py' else module.rpartition('.')[0]
        files[module]=path
        tree=ast.parse(file.read_bytes())
        def walk(node):
            if isinstance(node,ast.If) and ((isinstance(node.test,ast.Name) and node.test.id=='TYPE_CHECKING') or (isinstance(node.test,ast.Attribute) and node.test.attr=='TYPE_CHECKING')):
                return
            if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='pytest_plugins' for target in node.targets):
                collection_hooks.append(path+':pytest_plugins')
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in COLLECTION_HOOKS:
                collection_hooks.append(path+':'+node.name)
            targets=[]
            if isinstance(node,ast.Import):
                targets=[alias.name for alias in node.names]
            elif isinstance(node,ast.ImportFrom):
                target=importlib.util.resolve_name('.'*node.level+(node.module or ''),package) if node.level else node.module or ''
                targets=[target,*[target+'.'+alias.name for alias in node.names]]
            for target in targets:
                root=target.partition('.')[0]
                computational=target=='dateutil.relativedelta' or target.startswith('dateutil.relativedelta.')
                if (root in native_stdlib or root not in stdlib|trusted) and root not in optional_absent and not computational:
                    risky.add(module)
                    seeds[module].add(target)
                parts=target.split('.')
                for end in range(1,len(parts)+1):
                    reverse['.'.join(parts[:end])].add(module)
            for child in ast.iter_child_nodes(node):
                walk(child)
        walk(tree)
    queue=list(risky)
    while queue:
        for dependent in reverse[queue.pop()]-risky:
            risky.add(dependent);queue.append(dependent)
    mask=0
    for row in catalog:
        if 'conftest' in risky or row['file'] in {files[name] for name in risky}:
            mask |= 1<<row['number']
    return mask,sorted(files[name] for name in risky),collection_hooks,{'absent_optional':sorted(optional_absent),'native_seeds':{name:sorted(targets) for name,targets in seeds.items()},'computational_imports':['mypy_extensions.mypyc_attr','mypy_extensions.trait','mypy_extensions.i64','dateutil.relativedelta','pytz']}


def main(args):
    import pytest
    from build_maps import BASE,Collector,compact,prepare_source
    class Contexts(Collector):
        def switch(self,group):
            self.group=group
    source=prepare_source(args)
    catalog=json.loads(args.catalog.read_bytes())['tests']
    ids={row['id']:row['number'] for row in catalog}
    known_directories={'.'}
    known_files=set()
    symlinks=set()
    for folder,dirs,files in os.walk(source):
        relative=Path(folder).relative_to(source)
        known_directories.add(relative.as_posix())
        for name in dirs+files:
            path=Path(folder)/name
            key=path.relative_to(source).as_posix()
            if path.is_symlink():
                symlinks.add(key)
        known_files.update((relative/name).as_posix() for name in files)
    native,native_files,hooks,boundary_proof=boundaries(source,catalog)
    manifest_bytes=args.worker_manifest.read_bytes()
    manifest=json.loads(manifest_bytes)
    if manifest['base']!=BASE or not set(manifest['opaque_child_process'])<=ids.keys():
        raise ValueError('Worker baseline mismatch')
    workers=sum(1<<ids[node] for node in set(manifest['opaque_child_process']))
    context=Contexts(source,ids)
    observer=Observations(source,context)
    observer.framework_filter=not hooks
    os.chdir(source)
    sys.path.insert(0,str(source))
    os.environ.update(SKIP_INTEGRATION='0',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    started=time.monotonic()
    observer.install()
    try:
        options=[args.smoke or 'tests','-q','--tb=short','-p','no:cacheprovider']
        status=pytest.main(options,plugins=[context])
    finally:
        observer.stop()
    if status not in (0,1) or context.collection_errors:
        raise RuntimeError('Directory profile collection incomplete')
    untrusted={node for node in ids if context.passed_phases[node]!={'setup','call','teardown'}}|context.nonpassing
    untrusted_mask=sum(1<<ids[node] for node in untrusted if node in ids)
    opaque=context.opaque|workers
    payload={'base':BASE,'version':1,'catalog':list(ids),'complete':not args.smoke and len(context.collected)>=len(ids),
        'directories':{key:format(mask,'x') for key,mask in sorted(observer.directories.items())},
        'probes':{key:format(mask,'x') for key,mask in sorted(observer.probes.items())},
        'opens':{key:format(mask,'x') for key,mask in sorted(observer.opens.items())},
        'known_directories':sorted(known_directories),'known_files':sorted(known_files),'symlinks':sorted(symlinks),
        'untrusted':format(untrusted_mask,'x'),'opaque':format(opaque,'x'),'native_guard':format(native,'x'),
        'always_include':format(untrusted_mask|opaque|native,'x'),
        'certificate':{'operations':['add_regular_sql_fixture'],'deletions':False,'worker_contexts_indexed':True,
            'worker_count':len(set(manifest['opaque_child_process'])),'worker_manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),
            'native_boundary':'third-party filesystem library consumer fallback','native_files':native_files,'native_import_proof':boundary_proof,
            'framework_python_discovery_exclusion':not hooks,'custom_collection_hooks':hooks}}
    encoded=gzip.compress(compact(payload),mtime=0)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'directories.json.gz').write_bytes(encoded)
    summary={'base':BASE,'elapsed':time.monotonic()-started,'collected':len(context.collected),'untrusted':sorted(untrusted),
             'directories':len(observer.directories),'probes':len(observer.probes),'opens':len(observer.opens),
             'native_tests':native.bit_count(),'opaque_tests':opaque.bit_count(),'ignored':dict(observer.ignored),
             'sha256':hashlib.sha256(encoded).hexdigest()}
    (args.out/'directories-manifest.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({**summary,'untrusted_count':len(untrusted),'untrusted':sorted(untrusted) if len(untrusted)<10 else 'see manifest','native_import_proof':boundary_proof}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--worker-manifest',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--smoke')
    main(parser.parse_args())
