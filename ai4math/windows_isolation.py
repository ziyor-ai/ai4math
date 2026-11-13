"""Per-request AppContainer file staging and fail-closed network admission.

This module is NOT study_isolated_v1. Native network leases require WFP rights;
the current ordinary account is denied and the privileged branch awaits actual
acceptance. Engineering file probes cannot qualify network/study admission.
Trusted host users/controllers remain outside the untrusted-worker threat model.
"""
from __future__ import annotations
import hashlib,json,os,re,shutil,stat,uuid
from dataclasses import dataclass
from pathlib import Path,PurePosixPath,PureWindowsPath

class IsolationError(ValueError):pass
class NetworkIsolationUnavailable(IsolationError):pass
def require(ok,message):
    if not ok:raise IsolationError(message)
def encoded(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
def digest(x):return hashlib.sha256(encoded(x)).hexdigest()
def file_hash(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def relative_name(value):
    require(type(value) is str and value and value not in ('.','..') and '\\' not in value,'relative path must use canonical forward slashes')
    p=PurePosixPath(value);w=PureWindowsPath(value)
    require(not p.is_absolute() and not w.is_absolute() and not w.drive and str(p)==value,'absolute/ambiguous path rejected')
    for part in p.parts:
        require(part not in ('.','..') and re.fullmatch(r'[A-Za-z0-9_.-]+',part) is not None,'unsafe path component')
        require(part.rstrip(' .')==part and part.split('.')[0].upper() not in {'CON','PRN','AUX','NUL','COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9','LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9'},'Windows device/alias rejected')
    return value
def no_reparse(path,hardlink_file=False):
    p=Path(path).absolute()
    for ancestor in [p]+list(p.parents):
        if ancestor.exists() or ancestor.is_symlink():
            st=ancestor.lstat();require(not stat.S_ISLNK(st.st_mode) and not getattr(st,'st_file_attributes',0)&0x400,'reparse ancestor rejected')
    if hardlink_file:
        st=p.stat();require(stat.S_ISREG(st.st_mode) and st.st_nlink==1,'nonregular/hardlinked source rejected')
    return p.resolve()
def _native():
    require(os.name=='nt','Windows native backend required; no fallback')
    from . import _windows_native
    return _windows_native
def current_user_sid():
    import ctypes as C
    from ctypes import wintypes as W
    n=_native();n.api(n.K,'GetCurrentProcess',[],W.HANDLE);token=W.HANDLE();n.must(n.A.OpenProcessToken(n.K.GetCurrentProcess(),8,C.byref(token)),'controller token')
    try:
        data=C.create_string_buffer(512);size=W.DWORD();n.must(n.A.GetTokenInformation(token,1,data,len(data),C.byref(size)),'controller SID');return n.sid_text(C.cast(data,C.POINTER(C.c_void_p))[0])
    finally:n.K.CloseHandle(token)
def _acl(path,root,host_sid,app_sid=None,writable=False,inherit=True):
    import ctypes as C
    from ctypes import wintypes as W
    path=no_reparse(path);require(path.is_relative_to(root.resolve()),'ACL target outside owned request root');n=_native();flags='OICI' if path.is_dir() and inherit else ''
    sddl='D:P(A;'+flags+';FA;;;SY)(A;'+flags+';FA;;;'+host_sid+')'
    if app_sid:sddl+='(A;'+flags+';'+('FA' if writable else 'FRFX')+';;;'+app_sid+')'
    if writable:sddl+='S:(ML;'+flags+';NW;;;LW)'
    n.api(n.A,'ConvertStringSecurityDescriptorToSecurityDescriptorW',[W.LPCWSTR,W.DWORD,C.POINTER(C.c_void_p),C.c_void_p],W.BOOL);n.api(n.A,'SetFileSecurityW',[W.LPCWSTR,W.DWORD,C.c_void_p],W.BOOL)
    sd=C.c_void_p();n.must(n.A.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,C.byref(sd),None),'parse request DACL')
    try:
        n.must(n.A.SetFileSecurityW(str(path),0x80000004,sd),'protect owned request DACL')
        if writable:n.must(n.A.SetFileSecurityW(str(path),0x10,sd),'label writable request directory low')
    finally:n.K.LocalFree(sd)
    return {'relative_path':path.relative_to(root).as_posix(),'grant':'write' if writable else 'read_execute' if app_sid else 'controller_only','inherit':inherit,'low_integrity':writable,'DACL_sha256':hashlib.sha256(sddl.encode()).hexdigest()}

@dataclass(frozen=True)
class FrozenFile:
    source:str
    target:str
    sha256:str

class FileRequest:
    """One-use request tree and profile, owned by a trusted controller.

    No source producer/validator is imported here. Files are copied by hash.
    A worker must be gone before collecting output, and all exported paths are
    checked again. This does not protect against a hostile current host user.
    """
    def __init__(self,workspace,request_id,files,approved_source_roots,protocol_sha256,method_sha256,max_stage_bytes=64*1024*1024):
        require(type(request_id) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',request_id) is not None,'invalid permanent request id')
        for value in [protocol_sha256,method_sha256]:require(type(value) is str and re.fullmatch(r'[a-f0-9]{64}',value) is not None,'invalid protocol/method hash')
        require(type(max_stage_bytes) is int and 0<max_stage_bytes<=256*1024*1024,'stage bound invalid')
        require(type(files) in (list,tuple) and 0<len(files)<=512 and all(isinstance(f,FrozenFile) for f in files),'frozen source list required')
        targets=[relative_name(f.target) for f in files];require(len(set(t.casefold() for t in targets))==len(targets),'case-insensitive target collision')
        require(all(t.split('/')[0] in ['runtime','input','program'] and len(PurePosixPath(t).parts)>1 for t in targets),'source destination must be frozen partition')
        workspace=no_reparse(workspace);require(workspace.is_dir(),'controller workspace must already exist')
        approved=[no_reparse(p) for p in approved_source_roots];require(bool(approved),'approved source roots missing');validated=[];total=0
        for f in files:
            require(type(f.sha256) is str and re.fullmatch(r'[a-f0-9]{64}',f.sha256) is not None,'source hash invalid')
            p=no_reparse(f.source,hardlink_file=True);require(any(p.is_relative_to(r) for r in approved),'source not in controller-approved root');total+=p.stat().st_size;require(total<=max_stage_bytes,'staged byte limit exceeded');validated.append((f,p))
        self.root=workspace/request_id;require(not self.root.exists(),'request id already consumed')
        self.root.mkdir();self.host_sid=current_user_sid();self.profile=None;self.acls=[];self.state='creating';self.launched=False;self.receipt=None;self._collected=False;self.network_lease=None
        try:
            self.acls.append(_acl(self.root,self.root,self.host_sid))
            for name in ['control','runtime','input','program','work']:
                p=self.root/name;p.mkdir();self.acls.append(_acl(p,self.root,self.host_sid))
            n=_native();self.profile=n.create_profile('ai4math.request.'+uuid.uuid4().hex)
            self.identity={'schema':'windows-file-request-v1','request_id':request_id,'protocol_sha256':protocol_sha256,'method_sha256':method_sha256,'profile_sid':self.profile['sid'],'profile_name':self.profile['name'],'network_verified':False,'strict_study_admitted':False}
            rows=[]
            for f,p in validated:
                target=self.root/f.target;target.parent.mkdir(parents=True,exist_ok=True)
                before=p.stat();h=hashlib.sha256();copied=0
                with p.open('rb') as src,target.open('xb') as dst:
                    for block in iter(lambda:src.read(1024*1024),b''):
                        copied+=len(block);require(copied<=before.st_size,'source grew during freeze');h.update(block);dst.write(block)
                after=p.stat();require((before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns) and after.st_nlink==1,'source changed during freeze');require(h.hexdigest()==f.sha256,'source bytes differ from frozen hash')
                rows.append({'target':f.target,'size':copied,'sha256':f.sha256})
            # Root traversal is allowed, but control never inherits package rights.
            self.acls.append(_acl(self.root,self.root,self.host_sid,self.profile['sid'],inherit=False))
            for name in ['runtime','input','program']:
                for p in [self.root/name]+sorted((self.root/name).rglob('*')):self.acls.append(_acl(p,self.root,self.host_sid,self.profile['sid']))
            self.acls.append(_acl(self.root/'work',self.root,self.host_sid,self.profile['sid'],writable=True))
            self.manifest={'files':rows,'total_bytes':sum(r['size'] for r in rows),'source_allowlist_controller_owned':True};self.identity['file_manifest_sha256']=digest(self.manifest)
            self._save('identity.json',self.identity);self._save('frozen-files.json',self.manifest);self._save('ACL-record.json',{'targets':self.acls});self.state='staged';self._save('stage-result.json',{'status':self.state,'network_verified':False})
        except Exception as exc:
            self.state='setup_failed'
            try:self._save('setup-failure.json',{'type':type(exc).__name__,'message':str(exc),'no_fallback':True})
            finally:self.close()
            raise
    def _save(self,name,value):
        with (self.root/'control'/name).open('xb') as f:f.write(encoded(value)+b'\n')
    def verify_frozen(self):
        require(self.state=='staged','request not staged')
        expected={r['target'] for r in self.manifest['files']}
        actual={p.relative_to(self.root).as_posix() for name in ['runtime','input','program'] for p in (self.root/name).rglob('*') if p.is_file()}
        require(actual==expected,'frozen file set changed')
        for row in self.manifest['files']:
            p=no_reparse(self.root/row['target'],hardlink_file=True);require(p.stat().st_size==row['size'] and file_hash(p)==row['sha256'],'frozen artifact drift')
    def dispatch(self,executable=None,args=None,**untrusted_claims):
        """Require installed/read-back exact-SID OS policy before starting a worker.

        This engineering entry still does not admit a study. Callers cannot supply
        booleans or metadata to impersonate a native policy lease.
        """
        require(self.state=='staged' and not self.launched,'request not eligible or already used')
        self.verify_frozen()
        try:
            if untrusted_claims:raise NetworkIsolationUnavailable('caller declarations cannot replace native WFP policy')
            require(type(executable) is str and type(args) is list and all(type(a) is str for a in args),'explicit frozen executable and argv required')
            from ._windows_network import PackageNetworkLease
            self.network_lease=PackageNetworkLease(self.profile);policy=self.network_lease.acquire()
            self._save('native-network-policy.json',policy)
        except Exception as exc:
            self._save('network-admission-refusal.json',{'error':str(exc),'Windows_error':getattr(exc,'code',None),'launched':False,'no_fallback':True,'strict_study_admitted':False})
            if self.network_lease is not None and self.network_lease.handle is not None:
                self.state='network_cleanup_unconfirmed';raise NetworkIsolationUnavailable('native policy cleanup unconfirmed; retain profile and lease for review') from exc
            self.network_lease=None;self.state='network_refused';raise NetworkIsolationUnavailable(str(exc)+'; request was not launched, no weaker fallback') from exc
        try:return self._launch(executable,args,'files_network_engineering',policy)
        finally:
            stopped=not self.launched or bool(self.receipt and (self.receipt.get('job_empty') is True or not self.receipt.get('pid')))
            if stopped:
                cleanup=self.network_lease.close(worker_stopped=True);self._save('network-policy-cleanup.json',cleanup);self.network_lease=None
    def run_engineering_file_probe(self,executable,args,wall_seconds=12):
        """Explicit benign fixture only; never a fallback from dispatch()."""
        return self._launch(executable,args,'engineering_file_probe_only',None,wall_seconds)
    def _launch(self,executable,args,scope,network_policy,wall_seconds=12):
        require(self.state=='staged' and not self.launched,'request cannot rerun or downgrade after refusal')
        self.verify_frozen();relative_name(executable);require(executable.startswith('runtime/') and executable in {r['target'] for r in self.manifest['files']},'executable not frozen runtime');require(type(args) is list and all(type(a) is str for a in args),'argv must be explicit strings')
        self._save('dispatch-intent.json',{'executable':executable,'args':args,'scope':scope,'network_verified':network_policy is not None,'strict_study_admitted':False})
        self.launched=True;self.state='running';n=_native()
        self.receipt=n.run(self.root/executable,args,self.root/'work',self.root/'control/process',profile=self.profile,wall_seconds=wall_seconds)
        self._save('process-receipt.json',self.receipt);self.state='terminal'
        require(self.receipt.get('job_empty') is True,'worker Job not confirmed empty; do not collect')
        return self.receipt
    def collect_outputs(self):
        require(self.state=='terminal' and self.receipt and self.receipt.get('job_empty') is True,'collect only after confirmed process-tree exit')
        require(not self._collected,'outputs already collected')
        rows = [{'target': path.relative_to(self.root/'work').as_posix(), 'size': size, 'sha256': file_hash(path)}
                for path, size in self._work_files()]
        self._save('collected-outputs.json',{'files':rows,'job_empty_before_collection':True,'network_verified':False});self._collected=True;return rows

    def _work_files(self):
        root = no_reparse(self.root/'work')

        def visit(folder):
            for p in sorted(folder.iterdir()):
                path=no_reparse(p);require(path.is_relative_to(root),'output escape');st=p.lstat()
                if stat.S_ISDIR(st.st_mode):
                    yield from visit(path)
                else:
                    require(stat.S_ISREG(st.st_mode) and st.st_nlink==1,'nonregular/hardlinked output rejected')
                    yield p, st.st_size

        return visit(root)

    def settle_engineering_resources(self, ledger):
        """Settle one pre-reserved diagnostic tool or verifier from controller evidence."""
        require(ledger.plan['mode'] == 'engineering_diagnostic', 'engineering resource ledger required')
        require(self.state == 'terminal' and self.receipt.get('job_empty') is True
                and self.receipt.get('all_owned_handles_closed') is True,
                'confirmed process-tree exit and handle cleanup required for resource settlement')
        request_id = self.identity['request_id']
        call = ledger.state()['calls'].get(request_id)
        require(call and call['status'] == 'pending', 'matching pending resource reservation required')
        control = self.root/'control'
        intent = json.loads((control/'dispatch-intent.json').read_bytes())
        argv = intent['args']
        require(intent['scope'] == 'engineering_file_probe_only' and intent['executable'] == 'runtime/python.exe'
                and argv[:3] == ['-I', '-S', '-B'] and len(argv) >= 4
                and self.receipt['argv'] == [str(self.root/intent['executable']), *argv],
                'fixed engineering Python entry required')
        programs = {str(self.root/row['target']): row['sha256'] for row in self.manifest['files']
                    if row['target'].startswith('program/')}
        inputs = sorted((row for row in self.manifest['files'] if row['target'].startswith('input/')),
                        key=lambda row: row['target'])
        require(programs.get(argv[3]) == call['tool_sha256'] and digest(inputs) == call['input_sha256']
                and self.identity['protocol_sha256'] == call['policy_sha256'],
                'resource reservation differs from the staged program, input or policy')
        process_path = control/'process-receipt.json'
        require(json.loads(process_path.read_bytes()) == self.receipt, 'native process receipt changed')
        if self.receipt['resumed']:
            token = self.receipt.get('OS_token_before_resume', {})
            require(token.get('is_appcontainer') is True and token.get('appcontainer_sid') == self.identity['profile_sid']
                    and self.receipt.get('OS_in_owned_job_before_resume') is True,
                    'metering requires the staged AppContainer and owned Job')

        os_usage = self.receipt.get('OS_accounting') or {}
        output = self.receipt.get('output_capture', {})
        work_bytes = None
        output_bytes = None
        output_note = 'capture incomplete or truncated'
        if set(output) == {'stdout', 'stderr'} and all(row['stream_fully_drained'] for row in output.values()):
            try:
                work_bytes = sum(size for _, size in self._work_files())
            except (OSError, IsolationError, RecursionError):
                output_note = 'work file sizes could not be verified'
            else:
                output_bytes = work_bytes + sum(row['captured_bytes'] for row in output.values())
                output_note = 'captured streams plus logical sizes of final regular work files'
        role_wall = 'tool_wall_microseconds' if call['kind'] == 'tool' else 'verification_wall_microseconds'
        usage = {'cpu_microseconds': os_usage.get('CPU_microseconds'),
                 'peak_memory_bytes': os_usage.get('peak_process_committed_memory_bytes'),
                 'input_bytes': sum(row['size'] for row in inputs) if self.receipt['resumed'] else None,
                 'output_bytes': output_bytes, role_wall: self.receipt.get('wall_microseconds')}
        status = self.receipt['status']
        if status not in {'timeout', 'output_limit', 'interrupted'}:
            status = 'completed' if status == 'completed' and self.receipt.get('returncode') == 0 else 'failed'
        accounting = {'schema':'windows-engineering-resource-v1','request_id':request_id,'kind':call['kind'],
                      'status':status,'native_status':self.receipt['status'],'usage':usage,
                      'output_scope':output_note,'work_output_bytes':work_bytes,'output_capture':output,
                      'inputs':inputs,'process_receipt_sha256':file_hash(process_path)}
        evidence_path = control/'resource-accounting.json'
        try:
            self._save(evidence_path.name,accounting)
        except FileExistsError:
            require(json.loads(evidence_path.read_bytes()) == accounting, 'resource accounting evidence changed')
        return ledger.settle(request_id,usage,status,file_hash(evidence_path))
    def close(self):
        if self.state=='running':raise IsolationError('cannot delete profile while a worker is running')
        if self.launched and self.receipt and self.receipt.get('job_empty') is not True:raise IsolationError('cannot discard identity while process-tree exit remains unconfirmed')
        if self.network_lease is not None:raise IsolationError('live network lease must be released only after confirmed worker stop')
        if self.profile:
            result=_native().delete_profile(self.profile);self.profile=None;self._save('profile-cleanup.json',result);require(result['deleted'],'profile cleanup failed')
        self.state='closed'
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
