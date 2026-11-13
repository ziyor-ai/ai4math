"""Windows restricted-process primitive; no native study admission by itself.

Every child is created suspended and assigned to a configured non-breakaway
Job before resume. AppContainer capability list is empty. No unbounded fallback.
"""
import sys
sys.dont_write_bytecode=True
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack
from pathlib import Path
import os,subprocess,time
K=C.WinDLL('kernel32',use_last_error=True);A=C.WinDLL('advapi32',use_last_error=True);U=C.WinDLL('userenv',use_last_error=True)
SIZE=C.c_size_t;PTR=C.c_void_p;I64=C.c_longlong;U64=C.c_ulonglong
class SA(C.Structure):_fields_=[('nLength',W.DWORD),('lpSecurityDescriptor',PTR),('bInheritHandle',W.BOOL)]
class SI(C.Structure):_fields_=[('cb',W.DWORD),('lpReserved',W.LPWSTR),('lpDesktop',W.LPWSTR),('lpTitle',W.LPWSTR),('dwX',W.DWORD),('dwY',W.DWORD),('dwXSize',W.DWORD),('dwYSize',W.DWORD),('dwXCountChars',W.DWORD),('dwYCountChars',W.DWORD),('dwFillAttribute',W.DWORD),('dwFlags',W.DWORD),('wShowWindow',W.WORD),('cbReserved2',W.WORD),('lpReserved2',PTR),('hStdInput',W.HANDLE),('hStdOutput',W.HANDLE),('hStdError',W.HANDLE)]
class SIX(C.Structure):_fields_=[('StartupInfo',SI),('lpAttributeList',PTR)]
class PI(C.Structure):_fields_=[('hProcess',W.HANDLE),('hThread',W.HANDLE),('dwProcessId',W.DWORD),('dwThreadId',W.DWORD)]
class CAPS(C.Structure):_fields_=[('AppContainerSid',PTR),('Capabilities',PTR),('CapabilityCount',W.DWORD),('Reserved',W.DWORD)]
class BASIC(C.Structure):_fields_=[('PerProcessUserTimeLimit',I64),('PerJobUserTimeLimit',I64),('LimitFlags',W.DWORD),('MinimumWorkingSetSize',SIZE),('MaximumWorkingSetSize',SIZE),('ActiveProcessLimit',W.DWORD),('Affinity',SIZE),('PriorityClass',W.DWORD),('SchedulingClass',W.DWORD)]
class IO(C.Structure):_fields_=[(n,U64) for n in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
class EXT(C.Structure):_fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',SIZE),('JobMemoryLimit',SIZE),('PeakProcessMemoryUsed',SIZE),('PeakJobMemoryUsed',SIZE)]
class CPU(C.Structure):_fields_=[('ControlFlags',W.DWORD),('CpuRate',W.DWORD)]
class ACCOUNT(C.Structure):_fields_=[('TotalUserTime',I64),('TotalKernelTime',I64),('ThisPeriodTotalUserTime',I64),('ThisPeriodTotalKernelTime',I64),('TotalPageFaultCount',W.DWORD),('TotalProcesses',W.DWORD),('ActiveProcesses',W.DWORD),('TotalTerminatedProcesses',W.DWORD)]
def api(dll,name,args,result):f=getattr(dll,name);f.argtypes=args;f.restype=result;return f
api(K,'CreateJobObjectW',[PTR,W.LPCWSTR],W.HANDLE);api(K,'SetInformationJobObject',[W.HANDLE,C.c_int,PTR,W.DWORD],W.BOOL);api(K,'QueryInformationJobObject',[W.HANDLE,C.c_int,PTR,W.DWORD,PTR],W.BOOL)
api(K,'AssignProcessToJobObject',[W.HANDLE,W.HANDLE],W.BOOL);api(K,'TerminateJobObject',[W.HANDLE,W.UINT],W.BOOL);api(K,'IsProcessInJob',[W.HANDLE,W.HANDLE,C.POINTER(W.BOOL)],W.BOOL)
api(K,'CreateProcessW',[W.LPCWSTR,W.LPWSTR,PTR,PTR,W.BOOL,W.DWORD,PTR,W.LPCWSTR,C.POINTER(SIX),C.POINTER(PI)],W.BOOL)
api(K,'InitializeProcThreadAttributeList',[PTR,W.DWORD,W.DWORD,C.POINTER(SIZE)],W.BOOL);api(K,'UpdateProcThreadAttribute',[PTR,W.DWORD,SIZE,PTR,SIZE,PTR,PTR],W.BOOL);api(K,'DeleteProcThreadAttributeList',[PTR],None)
api(K,'CreateFileW',[W.LPCWSTR,W.DWORD,W.DWORD,C.POINTER(SA),W.DWORD,W.DWORD,W.HANDLE],W.HANDLE);api(K,'CloseHandle',[W.HANDLE],W.BOOL)
api(K,'CreatePipe',[C.POINTER(W.HANDLE),C.POINTER(W.HANDLE),C.POINTER(SA),W.DWORD],W.BOOL)
api(K,'SetHandleInformation',[W.HANDLE,W.DWORD,W.DWORD],W.BOOL)
api(K,'PeekNamedPipe',[W.HANDLE,PTR,W.DWORD,PTR,C.POINTER(W.DWORD),PTR],W.BOOL)
api(K,'ReadFile',[W.HANDLE,PTR,W.DWORD,C.POINTER(W.DWORD),PTR],W.BOOL)
api(K,'ResumeThread',[W.HANDLE],W.DWORD);api(K,'WaitForSingleObject',[W.HANDLE,W.DWORD],W.DWORD);api(K,'GetExitCodeProcess',[W.HANDLE,C.POINTER(W.DWORD)],W.BOOL);api(K,'TerminateProcess',[W.HANDLE,W.UINT],W.BOOL);api(K,'LocalFree',[PTR],PTR)
api(A,'OpenProcessToken',[W.HANDLE,W.DWORD,C.POINTER(W.HANDLE)],W.BOOL);api(A,'GetTokenInformation',[W.HANDLE,C.c_int,PTR,W.DWORD,C.POINTER(W.DWORD)],W.BOOL)
api(A,'ConvertSidToStringSidW',[PTR,C.POINTER(PTR)],W.BOOL);api(A,'FreeSid',[PTR],PTR)
api(U,'CreateAppContainerProfile',[W.LPCWSTR,W.LPCWSTR,W.LPCWSTR,PTR,W.DWORD,C.POINTER(PTR)],C.c_long);api(U,'DeleteAppContainerProfile',[W.LPCWSTR],C.c_long)
def must(ok,label):
    if not ok:raise OSError(C.get_last_error(),label)
def sid_text(ptr):
    out=PTR();must(A.ConvertSidToStringSidW(ptr,C.byref(out)),'ConvertSidToStringSidW')
    try:return C.wstring_at(out)
    finally:K.LocalFree(out)
def token_info(process):
    token=W.HANDLE();must(A.OpenProcessToken(process,8,C.byref(token)),'OpenProcessToken')
    try:
        flag=W.DWORD();size=W.DWORD();must(A.GetTokenInformation(token,29,C.byref(flag),C.sizeof(flag),C.byref(size)),'TokenIsAppContainer')
        result={'is_appcontainer':bool(flag.value)}
        if flag.value:
            C.set_last_error(0)
            A.GetTokenInformation(token,31,None,0,C.byref(size))
            must(size.value>=C.sizeof(PTR),'TokenAppContainerSid required size')
            buffer=C.create_string_buffer(size.value);must(A.GetTokenInformation(token,31,buffer,len(buffer),C.byref(size)),'TokenAppContainerSid');ptr=C.cast(buffer,C.POINTER(PTR))[0];result['appcontainer_sid']=sid_text(ptr)
            A.GetTokenInformation(token,30,None,0,C.byref(size));must(size.value>=4,'TokenCapabilities required size');caps_buffer=C.create_string_buffer(size.value);must(A.GetTokenInformation(token,30,caps_buffer,len(caps_buffer),C.byref(size)),'TokenCapabilities');result['capability_count']=C.cast(caps_buffer,C.POINTER(W.DWORD))[0]
        return result
    finally:K.CloseHandle(token)
def create_profile(name):
    if not name.startswith('ai4math.request.'):raise ValueError('profile outside task prefix')
    ptr=PTR();hr=U.CreateAppContainerProfile(name,name,'AI math AM-T055 disposable capability probe',None,0,C.byref(ptr))
    if hr!=0:raise OSError(hr & 0xffffffff,'CreateAppContainerProfile; no existing-profile reuse')
    return {'name':name,'sid_pointer':ptr,'sid':sid_text(ptr),'newly_created':True}
def delete_profile(profile):
    if not profile['newly_created'] or not profile['name'].startswith('ai4math.request.'):raise ValueError('not a newly owned profile')
    hr=U.DeleteAppContainerProfile(profile['name']);A.FreeSid(profile['sid_pointer']);return {'name':profile['name'],'delete_hresult':hr & 0xffffffff,'deleted':hr==0}
def query(job,kind,struct):
    result=struct();must(K.QueryInformationJobObject(job,kind,C.byref(result),C.sizeof(result),None),'QueryInformationJobObject '+str(kind));return result


def job_accounting(job, account):
    memory = query(job, 9, EXT)
    return {
        'CPU_microseconds': (account.TotalUserTime + account.TotalKernelTime) // 10,
        'user_CPU_100ns': account.TotalUserTime,
        'kernel_CPU_100ns': account.TotalKernelTime,
        'total_processes': account.TotalProcesses,
        'active_processes': account.ActiveProcesses,
        'terminated_processes': account.TotalTerminatedProcesses,
        'peak_job_committed_memory_bytes': memory.PeakJobMemoryUsed,
        'peak_process_committed_memory_bytes': memory.PeakProcessMemoryUsed,
    }


class _OutputStream:
    """Capture one byte stream in the controller, including a one-byte overflow probe."""

    def __init__(self, destination, limit, handles):
        reader, writer = W.HANDLE(), W.HANDLE()
        must(K.CreatePipe(C.byref(reader), C.byref(writer), None, 4096), 'Create output pipe')
        self.reader, self.writer = reader.value, writer.value
        handles.extend((self.reader, self.writer))
        must(K.SetHandleInformation(self.writer, 1, 1), 'Make only the pipe writer inheritable')
        self.destination = destination
        self.limit = limit
        self.saved = 0
        self.overflow = False
        self.eof = False
        self.buffer = C.create_string_buffer(8192)

    def pump(self):
        if self.eof or self.overflow:
            return False
        available = W.DWORD()
        # This thread is the sole owner of the read end. No competing read can consume
        # the bytes between PeekNamedPipe and ReadFile.
        if not K.PeekNamedPipe(self.reader, None, 0, None, C.byref(available), None):
            if C.get_last_error() != 109:  # ERROR_BROKEN_PIPE
                must(False, 'Peek output pipe')
            self.eof = True
            return False
        if not available.value:
            return False
        remaining = self.limit - self.saved
        count = W.DWORD()
        size = min(available.value, len(self.buffer), remaining + 1)
        must(K.ReadFile(self.reader, self.buffer, size, C.byref(count), None), 'Read output pipe')
        must(count.value > 0, 'Unexpected empty output pipe read')
        saved = min(remaining, count.value)
        if saved:
            written = self.destination.write(self.buffer[:saved])
            self.saved += written
            if written != saved:
                raise OSError('Short output capture write')
        self.overflow = count.value > remaining
        return True

    def evidence(self):
        return {
            'captured_bytes': self.saved,
            'observed_bytes_lower_bound': self.saved + int(self.overflow),
            'overflow_detected': self.overflow,
            'stream_fully_drained': self.eof,
        }


def run(executable,args,cwd,record_dir,profile=None,wall_seconds=12,memory_bytes=512*1024*1024,process_limit=2,cpu_seconds=3):
    """Fixed benign probes only. Returns failed-closed setup/terminal evidence."""
    record_dir=Path(record_dir);record_dir.mkdir(exist_ok=False);steps=[];job=None;pi=PI();handles=[];attrs=None;assigned=False;start=time.perf_counter_ns()
    output_limit = 65536
    job_user_limit = cpu_seconds * 10_000_000
    files = ExitStack()
    streams = {}
    receipt={'argv':[str(executable)]+list(args),'cwd':str(cwd),'profile_sid':profile['sid'] if profile else None,'limits':{'wall_seconds':wall_seconds,'job_memory_bytes':memory_bytes,'active_processes':process_limit,'job_user_CPU_seconds':cpu_seconds,'CPU_rate_hard_cap_percent':10,'output_bytes_per_stream':output_limit},'steps':steps,'fallback_used':False,'resumed':False,'OS_accounting':None}
    try:
        job=K.CreateJobObjectW(None,None);must(job,'CreateJobObjectW')
        limit=EXT();limit.BasicLimitInformation.LimitFlags=0x2000|0x200|0x8|0x4;limit.BasicLimitInformation.ActiveProcessLimit=process_limit;limit.BasicLimitInformation.PerJobUserTimeLimit=job_user_limit;limit.JobMemoryLimit=memory_bytes
        must(K.SetInformationJobObject(job,9,C.byref(limit),C.sizeof(limit)),'Set job memory/process/time/kill-on-close');cpu=CPU(5,1000);must(K.SetInformationJobObject(job,15,C.byref(cpu),C.sizeof(cpu)),'Set job CPU hard cap');steps.append('job_limits_set_before_create')
        observed = query(job, 9, EXT)
        observed_cpu = query(job, 15, CPU)
        receipt['OS_limits_before_create'] = {
            'limit_flags': observed.BasicLimitInformation.LimitFlags,
            'active_processes': observed.BasicLimitInformation.ActiveProcessLimit,
            'job_memory_bytes': observed.JobMemoryLimit,
            'job_user_CPU_100ns': observed.BasicLimitInformation.PerJobUserTimeLimit,
            'cpu_rate_flags': observed_cpu.ControlFlags,
            'cpu_rate_per_10000': observed_cpu.CpuRate,
        }
        must(
            observed.BasicLimitInformation.LimitFlags & limit.BasicLimitInformation.LimitFlags
            == limit.BasicLimitInformation.LimitFlags
            and observed.BasicLimitInformation.ActiveProcessLimit == process_limit
            and observed.BasicLimitInformation.PerJobUserTimeLimit == job_user_limit
            and observed.JobMemoryLimit == memory_bytes
            and observed_cpu.ControlFlags & 5 == 5
            and observed_cpu.CpuRate == 1000,
            'Job limits did not match readback',
        )
        security=SA(C.sizeof(SA),None,True)
        for name in ('stdout', 'stderr'):
            destination = files.enter_context((record_dir / (name + '.bin')).open('xb', buffering=0))
            streams[name] = _OutputStream(destination, output_limit, handles)
        stdin = K.CreateFileW('NUL', 0x80000000, 3, C.byref(security), 3, 0x80, None)
        must(stdin not in (None,PTR(-1).value),'Create stdin handle');handles.append(stdin)
        count=2 if profile else 1;size=SIZE();K.InitializeProcThreadAttributeList(None,count,0,C.byref(size));attrs=C.create_string_buffer(size.value);must(K.InitializeProcThreadAttributeList(attrs,count,0,C.byref(size)),'Initialize attributes')
        inherited=(W.HANDLE*3)(streams['stdout'].writer, streams['stderr'].writer, stdin);must(K.UpdateProcThreadAttribute(attrs,0,0x20002,C.byref(inherited),C.sizeof(inherited),None,None),'Explicit inherited handle list')
        if profile:
            caps=CAPS(profile['sid_pointer'],None,0,0);must(K.UpdateProcThreadAttribute(attrs,0,0x20009,C.byref(caps),C.sizeof(caps),None,None),'AppContainer empty capabilities')
        si=SIX();si.StartupInfo.cb=C.sizeof(SIX);si.StartupInfo.dwFlags=0x100;si.StartupInfo.hStdOutput=inherited[0];si.StartupInfo.hStdError=inherited[1];si.StartupInfo.hStdInput=stdin;si.lpAttributeList=C.cast(attrs,PTR)
        env={'SystemRoot':os.environ.get('SystemRoot','C:\\Windows'),'WINDIR':os.environ.get('WINDIR','C:\\Windows'),'TEMP':str(cwd),'TMP':str(cwd),'USERPROFILE':str(cwd),'APPDATA':str(cwd),'LOCALAPPDATA':str(cwd)}
        block=C.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(env.items(),key=lambda kv:kv[0].upper()))+'\0\0');command=C.create_unicode_buffer(subprocess.list2cmdline([str(executable)]+list(args)))
        must(K.CreateProcessW(str(executable),command,None,None,True,0x4|0x400|0x80000|0x8000000,block,str(cwd),C.byref(si),C.byref(pi)),'CreateProcess suspended')
        steps.append('created_suspended');receipt['pid']=pi.dwProcessId
        for stream in streams.values():
            must(K.CloseHandle(stream.writer), 'Close controller copy of output writer')
            handles.remove(stream.writer)
        must(K.AssignProcessToJobObject(job,pi.hProcess),'AssignProcessToJobObject before resume');assigned=True;steps.append('assigned_before_resume')
        injob=W.BOOL();must(K.IsProcessInJob(pi.hProcess,job,C.byref(injob)),'IsProcessInJob');must(injob.value,'process not in owned Job')
        receipt['OS_token_before_resume']=token_info(pi.hProcess);receipt['OS_in_owned_job_before_resume']=bool(injob.value)
        if profile:must(receipt['OS_token_before_resume'].get('is_appcontainer') and receipt['OS_token_before_resume'].get('appcontainer_sid')==profile['sid'],'AppContainer SID mismatch')
        must(K.ResumeThread(pi.hThread)!=0xffffffff,'ResumeThread');receipt['resumed']=True;steps.append('resumed_after_assignment')
        terminal='completed';deadline=time.monotonic()+wall_seconds
        while True:
            account = query(job, 1, ACCOUNT)
            progressed = [stream.pump() for stream in streams.values()]
            overflow = any(stream.overflow for stream in streams.values())
            if overflow:
                terminal = 'output_limit'
            elif account.TotalUserTime >= job_user_limit:
                terminal = 'cpu_limit'
            elif time.monotonic() > deadline:
                terminal = 'timeout'
            if terminal != 'completed':
                if account.ActiveProcesses:
                    must(K.TerminateJobObject(job,91),'TerminateJobObject')
                    steps.append('terminated_entire_job')
                break
            if account.ActiveProcesses == 0:
                break
            if not any(progressed):
                time.sleep(.02)
        cleanup_deadline=time.monotonic()+5
        while query(job,1,ACCOUNT).ActiveProcesses and time.monotonic()<cleanup_deadline:time.sleep(.02)
        account=query(job,1,ACCOUNT);require_empty=account.ActiveProcesses==0;must(require_empty,'Job not empty after stop');steps.append('job_empty_confirmed')
        receipt['job_empty'] = True
        # Job accounting can reach zero before the kernel closes the writers.
        while not all(stream.eof or stream.overflow for stream in streams.values()):
            progressed = [stream.pump() for stream in streams.values()]
            if not any(progressed):
                if all(stream.eof or stream.overflow for stream in streams.values()):
                    break
                if time.monotonic() >= cleanup_deadline:
                    raise TimeoutError('Output writer still open after Job exit')
                time.sleep(.005)
        if terminal == 'completed' and any(stream.overflow for stream in streams.values()):
            terminal = 'output_limit'
        if terminal == 'completed' and account.TotalUserTime >= job_user_limit:
            terminal = 'cpu_limit'
        code=W.DWORD();must(K.GetExitCodeProcess(pi.hProcess,C.byref(code)),'GetExitCodeProcess')
        receipt.update(status=terminal,returncode=code.value,OS_accounting=job_accounting(job, account))
    except (Exception, KeyboardInterrupt) as exc:
        status = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'setup_or_monitor_failed'
        receipt.update(status=status,error_type=type(exc).__name__,error_code=getattr(exc,'winerror',None) or getattr(exc,'errno',None),error=str(exc))
    finally:
        if not receipt.get('job_empty') and pi.hProcess:
            if assigned and job:K.TerminateJobObject(job,92)
            else:K.TerminateProcess(pi.hProcess,92)
            process_stopped = K.WaitForSingleObject(pi.hProcess,5000) == 0
            if not assigned:
                receipt['job_empty'] = process_stopped
        elif not pi.hProcess:
            receipt['job_empty'] = True
        if assigned and job and receipt['OS_accounting'] is None:
            try:
                until = time.monotonic()+3
                account = query(job,1,ACCOUNT)
                while account.ActiveProcesses and time.monotonic()<until:
                    time.sleep(.02)
                    account = query(job,1,ACCOUNT)
                receipt['job_empty'] = account.ActiveProcesses == 0
                if receipt['job_empty']:
                    receipt['OS_accounting'] = job_accounting(job, account)
                    receipt['OS_accounting']['after_failed_setup'] = True
            except OSError:
                receipt['OS_accounting'] = None
        if attrs:K.DeleteProcThreadAttributeList(attrs)
        closed=[]
        for h in [pi.hThread,pi.hProcess]+handles+([job] if job else []):
            if h:closed.append(bool(K.CloseHandle(h)))
        try:
            files.close()
        except OSError as exc:
            closed.append(False)
            receipt.update(status='setup_or_monitor_failed', error_type=type(exc).__name__, error=str(exc))
    receipt['output_capture'] = {name: stream.evidence() for name, stream in streams.items()}
    receipt['wall_microseconds'] = (time.perf_counter_ns()-start)//1000
    receipt['all_owned_handles_closed']=all(closed);return receipt
