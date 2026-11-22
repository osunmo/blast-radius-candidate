"""Conservative guards for foreign subclasses omitted by the old field profiler.

No patched source or labels are read. Merge before field-map certification.
"""
import argparse
import ast
from collections import defaultdict
import gzip
from importlib.util import resolve_name
import json
from pathlib import Path


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted(node.value)+'.'+node.attr
    return ''


def class_decorators_safe(node, modules):
    for decorator in node.decorator_list:
        function=decorator.func if isinstance(decorator,ast.Call) else decorator
        name=dotted(function)
        root,_,attribute=name.partition('.')
        if root not in modules or attribute not in {'skip','skipIf','skipUnless','expectedFailure'}:
            return False
    return True


def assigned_names(tree):
    return {node.id for statement in ast.walk(tree) if isinstance(statement,(ast.Assign,ast.AnnAssign,ast.AugAssign,ast.NamedExpr))
            for target in (statement.targets if isinstance(statement,ast.Assign) else [statement.target])
            for node in ast.walk(target) if isinstance(node,ast.Name)}


def dynamic_class_lines(source):
    tree=ast.parse(source)
    lines=set()
    parents={child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node,ast.Call):
            name=dotted(node.func)
            if name in {'type','builtins.type'}:
                if len(node.args)!=1 or node.keywords or any(isinstance(a,ast.Starred) for a in node.args):
                    lines.add(node.lineno)
            elif name.rsplit('.',1)[-1] in {'new_class','__build_class__'}:
                lines.add(node.lineno)
        elif isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load) and node.id in {'type','__build_class__'}:
            # Aliases of the constructor cannot be resolved safely. The direct
            # one-argument type inspection is the sole exception.
            parent=parents.get(node)
            if isinstance(parent,(ast.Assign,ast.NamedExpr)) and getattr(parent,'value',None) is node:
                lines.add(node.lineno)
        elif isinstance(node,ast.Attribute) and node.attr in {'new_class','__build_class__'}:
            parent=parents.get(node)
            if not isinstance(parent,ast.Call) or parent.func is not node:
                lines.add(node.lineno)
        elif isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__module__' for t in node.targets):
            lines.add(node.lineno)
    return lines


def risky_class_nodes(source, extra_safe=()):
    tree=ast.parse(source)
    modules=set()
    safe={'object',*extra_safe}
    blocked=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            modules.update(a.asname or a.name for a in node.names if a.name=='unittest')
        elif isinstance(node,ast.ImportFrom) and node.module=='unittest':
            safe.update(a.asname or a.name for a in node.names if a.name=='TestCase')
        elif isinstance(node,ast.ImportFrom):
            blocked.update(a.asname or a.name for a in node.names if (a.asname or a.name) not in extra_safe)
    safe.update(name+'.TestCase' for name in modules)
    modules -= assigned_names(tree)
    classes=[n for n in ast.walk(tree) if isinstance(n,ast.ClassDef)]
    changed=True
    while changed:
        changed=False
        for node in classes:
            if node.name not in safe and node.name not in blocked and not node.keywords and class_decorators_safe(node,modules) and all(dotted(b) in safe for b in node.bases):
                safe.add(node.name)
                changed=True
    return [node for node in classes if node.keywords or not class_decorators_safe(node,modules) or any(dotted(b) not in safe for b in node.bases)]


def risky_test_source(source):
    return bool(dynamic_class_lines(source) or risky_class_nodes(source))


def augment(data, source, coverage):
    """Mutate field map with an all-fields per-context conservative guard."""
    source=Path(source)
    catalog=data['catalog']
    all_tests=(1<<len(catalog))-1
    by_file=defaultdict(int)
    for number,node in enumerate(catalog):
        by_file[node.partition('::')[0]] |= 1<<number
    reverse=defaultdict(set)
    risks=set()
    module_files={}
    local_mask=0
    test_sources={}
    safe_qualified={'unittest.TestCase','object'}
    bindings={}
    decorator_modules_by_module={}
    for file in (source/'tests').rglob('*.py'):
        path=file.relative_to(source).as_posix()
        module=path[:-3].replace('/','.').removesuffix('.__init__')
        package=module if file.name=='__init__.py' else module.rpartition('.')[0]
        text=file.read_text(encoding='utf-8')
        tree=ast.parse(text)
        names={}
        for node in tree.body:
            if isinstance(node,ast.Import):
                names.update({a.asname or a.name.split('.')[0]:a.name if a.asname else a.name.split('.')[0] for a in node.names})
            elif isinstance(node,ast.ImportFrom):
                target=resolve_name('.'*node.level+(node.module or ''),package) if node.level else node.module or ''
                names.update({a.asname or a.name:target+'.'+a.name for a in node.names})
        test_sources[module]=(path,text,tree)
        bindings[module]=names
        decorator_modules_by_module[module]={name for name,target in names.items() if target=='unittest'}-assigned_names(tree)
    changed=True
    while changed:
        changed=False
        for module,(_,_,tree) in test_sources.items():
            for node in tree.body:
                decorator_modules=decorator_modules_by_module[module]
                if not isinstance(node,ast.ClassDef) or node.keywords or not class_decorators_safe(node,decorator_modules):
                    continue
                resolved=[]
                for base in node.bases:
                    name=dotted(base)
                    first,_,rest=name.partition('.')
                    resolved.append(bindings[module].get(first,module+'.'+first)+('.'+rest if rest else '') if name!='object' else name)
                key=module+'.'+node.name
                if key not in safe_qualified and all(base in safe_qualified for base in resolved):
                    safe_qualified.add(key)
                    changed=True
    for file in (source/'tests').rglob('*.py'):
        path=file.relative_to(source).as_posix()
        module=path[:-3].replace('/','.').removesuffix('.__init__')
        package=module if file.name=='__init__.py' else module.rpartition('.')[0]
        text=file.read_text(encoding='utf-8')
        module_files[module]=path
        tree=ast.parse(text)
        extra_safe={name for name,target in bindings[module].items() if target in safe_qualified}
        parents={child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        risky_lines=dynamic_class_lines(text)|{node.lineno for node in risky_class_nodes(text,extra_safe)}
        for line in risky_lines:
            node=next((n for n in ast.walk(tree) if getattr(n,'lineno',None)==line),None)
            test_function=None
            in_function=False
            while node is not None:
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    in_function=True
                    if node.name.startswith('test'):
                        test_function=node.name
                node=parents.get(node)
            if test_function:
                scoped=sum(1<<n for n,name in enumerate(catalog) if name.partition('::')[0]==path and name.rsplit('::',1)[-1]==test_function)
                if not scoped:
                    scoped=by_file[path]
                local_mask |= scoped
            elif in_function:
                local_mask |= by_file[path]
                risks.add(module)
            else:
                risks.add(module)
        for node in ast.walk(tree):
            dependencies=[]
            if isinstance(node,ast.Import):
                dependencies=[a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom):
                target=resolve_name('.'*node.level+(node.module or ''),package) if node.level else node.module or ''
                dependencies=[target,*[target+'.'+a.name for a in node.names]]
            for dependency in dependencies:
                parts=dependency.split('.')
                for end in range(1,len(parts)+1):
                    reverse['.'.join(parts[:end])].add(module)
    queue=list(risks)
    while queue:
        for dependent in reverse[queue.pop()]-risks:
            risks.add(dependent)
            queue.append(dependent)
    mask=local_mask
    reasons=[]
    for module in sorted(risks):
        path=module_files[module]
        mask |= by_file[path]
        reasons.append({'path':path,'reason':'foreign subclass/builder or importing helper','mask':format(by_file[path],'x')})
    for file in (source/'sqlglot').rglob('*.py'):
        path=file.relative_to(source).as_posix()
        text=file.read_text(encoding='utf-8')
        lines=dynamic_class_lines(text)
        # A type constructor executed in a SQLGlot module with an explicit
        # plain namespace lacking __module__ receives that SQLGlot module.
        # Such receivers were already observed by the old profiler.
        for node in ast.walk(ast.parse(text)):
            if (isinstance(node,ast.Call) and dotted(node.func)=='type' and len(node.args)==3
                    and isinstance(node.args[2],ast.Dict)
                    and all(isinstance(key,ast.Constant) and isinstance(key.value,str)
                            and key.value!='__module__' for key in node.args[2].keys)):
                lines.discard(node.lineno)
        if not lines:
            continue
        entry=coverage.get('files',{}).get(path)
        bits=0
        if entry is None or lines & set(entry['global']):
            bits=all_tests
        else:
            for line in lines:
                bits |= int(entry['lines'].get(str(line),'0'),16)
        mask |= bits
        reasons.append({'path':path,'reason':'source class builder/module rebinding','lines':sorted(lines),'mask':format(bits,'x')})
    data['untrusted']=format(int(data['untrusted'],16)|mask,'x')
    data['always_include']=format(int(data['always_include'],16)|mask,'x')
    data['foreign_receiver_guard']={'version':1,'mask':format(mask,'x'),'reasons':reasons}
    data.setdefault('certificate',{})['foreign_receiver_guards']=True
    data['complete']=False
    return data


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--map',type=Path,required=True)
    parser.add_argument('--coverage',type=Path,required=True)
    args=parser.parse_args()
    data=json.loads(gzip.decompress(args.map.read_bytes()))
    coverage=json.loads(gzip.decompress(args.coverage.read_bytes()))
    if coverage['base']!=data['base'] or coverage['catalog']!=data['catalog']:
        raise ValueError('Foreign receiver guard baseline mismatch')
    augment(data,args.source,coverage)
    args.map.write_bytes(gzip.compress(json.dumps(data,sort_keys=True,separators=(',',':')).encode(),mtime=0))
    print(json.dumps({'guarded_tests':int(data['foreign_receiver_guard']['mask'],16).bit_count(),'sources':len(data['foreign_receiver_guard']['reasons'])}))
