"""Exact-Package-SID temporary WFP deny lease, no global firewall changes.

Native successful installation requires OS-granted WFP object permissions.
Current ordinary-user evidence is ACCESS_DENIED; no elevation is attempted.
The successful privileged branch is unvalidated until actual acceptance runs.
"""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
import os,re,uuid

# Authorize IP traffic at connect/receive, leaving local port assignment out of the deny lease.
LAYERS={
 'ALE_AUTH_CONNECT_V4':'c38d57d1-05a7-4c33-904f-7fbceee60e82',
 'ALE_AUTH_CONNECT_V6':'4a72393b-319f-44bc-84c3-ba54dcb3b6b4',
 'ALE_AUTH_RECV_ACCEPT_V4':'e1cd9fe7-f4b5-4273-96c0-592e487b8650',
 'ALE_AUTH_RECV_ACCEPT_V6':'a3b42c97-9f04-4672-b87e-cee9c483257f'}
PACKAGE_ID='71bc78fa-f17c-4997-a602-6abb261f351c'
class NetworkPolicyError(RuntimeError):
    def __init__(self,stage,code):super().__init__(stage+': Windows error '+str(code));self.stage=stage;self.code=int(code)
def scoped_plan(sid):
    if type(sid) is not str or re.fullmatch(r'S-1-15-2-(?:[0-9]+-){6}[0-9]+',sid) is None or any(int(n)>0xffffffff for n in sid.split('-')[4:]):raise ValueError('only a full unique AppContainer Package SID is allowed; no global/package-group filters')
    return [{'layer':name,'layer_guid':key,'action':'BLOCK','condition':'ALE_PACKAGE_ID_EQUALS','package_sid':sid,'condition_count':1,'persistent':False} for name,key in LAYERS.items()]
class GUID(C.Structure):_fields_=[('a',W.DWORD),('b',W.WORD),('c',W.WORD),('d',C.c_ubyte*8)]
def guid(value):return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)
def same_guid(a,b):return bytes(a)==bytes(b)
class DISPLAY(C.Structure):_fields_=[('name',W.LPWSTR),('description',W.LPWSTR)]
class BLOB(C.Structure):_fields_=[('size',W.DWORD),('data',C.c_void_p)]
class SESSION(C.Structure):_fields_=[('key',GUID),('display',DISPLAY),('flags',W.DWORD),('wait_ms',W.DWORD),('pid',W.DWORD),('sid',C.c_void_p),('user',W.LPWSTR),('kernel',W.BOOL)]
class SUBLAYER(C.Structure):_fields_=[('key',GUID),('display',DISPLAY),('flags',W.DWORD),('provider',C.c_void_p),('data',BLOB),('weight',W.WORD)]
class VALUE(C.Structure):_fields_=[('type',C.c_int),('value',C.c_uint64)]
class CONDITION(C.Structure):_fields_=[('key',GUID),('match',C.c_int),('value',VALUE)]
class ACTION(C.Structure):_fields_=[('type',W.DWORD),('key',GUID)]
class CONTEXT(C.Union):_fields_=[('raw',C.c_uint64),('key',GUID)]
class FILTER(C.Structure):_fields_=[('key',GUID),('display',DISPLAY),('flags',W.DWORD),('provider',C.c_void_p),('data',BLOB),('layer',GUID),('sublayer',GUID),('weight',VALUE),('count',W.DWORD),('conditions',C.POINTER(CONDITION)),('action',ACTION),('context',CONTEXT),('reserved',C.c_void_p),('id',C.c_uint64),('effective_weight',VALUE)]

class PackageNetworkLease:
    def __init__(self,profile):
        self.plan=scoped_plan(profile.get('sid'));self.profile=profile;self.handle=None;self.ids=[];self.installed=False;self.cleanup=None;self.attempted=False
        if not profile.get('newly_created') or not str(profile.get('name','')).startswith('ai4math.request.'):raise ValueError('only freshly created owned request profiles allowed')
        if os.name!='nt':raise NetworkPolicyError('Windows WFP required',50)
        if C.sizeof(C.c_void_p)!=8 or C.sizeof(FILTER)!=200 or FILTER.count.offset!=112 or FILTER.conditions.offset!=120 or C.sizeof(CONDITION)!=40:raise NetworkPolicyError('unsupported Windows ABI layout; no policy added',50)
        from . import _windows_native as n
        self.n=n;pointer=profile.get('sid_pointer');n.api(n.A,'IsValidSid',[C.c_void_p],W.BOOL)
        if not pointer or not n.A.IsValidSid(pointer) or n.sid_text(pointer)!=profile['sid']:raise ValueError('native SID pointer does not match unique owned profile')
        self.f=C.WinDLL('fwpuclnt',use_last_error=True)
        for name,args in [
         ('FwpmEngineOpen0',[W.LPCWSTR,W.UINT,C.c_void_p,C.POINTER(SESSION),C.POINTER(W.HANDLE)]),('FwpmEngineClose0',[W.HANDLE]),
         ('FwpmTransactionBegin0',[W.HANDLE,W.DWORD]),('FwpmTransactionCommit0',[W.HANDLE]),('FwpmTransactionAbort0',[W.HANDLE]),
         ('FwpmSubLayerAdd0',[W.HANDLE,C.POINTER(SUBLAYER),C.c_void_p]),('FwpmFilterAdd0',[W.HANDLE,C.POINTER(FILTER),C.c_void_p,C.POINTER(C.c_uint64)]),
         ('FwpmFilterGetById0',[W.HANDLE,C.c_uint64,C.POINTER(C.POINTER(FILTER))])]:
            fn=getattr(self.f,name);fn.argtypes=args;fn.restype=W.DWORD
        self.f.FwpmFreeMemory0.argtypes=[C.c_void_p];self.f.FwpmFreeMemory0.restype=None
    def _ok(self,code,stage):
        if code:raise NetworkPolicyError(stage,code)
    def acquire(self):
        if self.attempted:raise ValueError('network lease cannot be reused')
        self.attempted=True
        session=SESSION();session.key=guid(str(uuid.uuid4()));session.display=DISPLAY('AI4MATH-request-dynamic-network','exact new package SID; dynamic auto-cleanup');session.flags=1;session.wait_ms=1000;handle=W.HANDLE();txn=False
        self._ok(self.f.FwpmEngineOpen0(None,10,None,C.byref(session),C.byref(handle)),'FwpmEngineOpen0');self.handle=handle
        try:
            self._ok(self.f.FwpmTransactionBegin0(handle,0),'begin dynamic transaction');txn=True
            sub=SUBLAYER();sub.key=guid(str(uuid.uuid4()));sub.display=DISPLAY('AI4MATH-new-request-only','no global scope, no persistent rule');sub.weight=0
            self._ok(self.f.FwpmSubLayerAdd0(handle,C.byref(sub),None),'add owned dynamic sublayer')
            for row in self.plan:
                condition=CONDITION();condition.key=guid(PACKAGE_ID);condition.match=0;condition.value.type=13;condition.value.value=self.profile['sid_pointer'].value
                f=FILTER();f.key=guid(str(uuid.uuid4()));f.display=DISPLAY('AI4MATH-'+row['layer'],'block only exact new AppContainer SID');f.layer=guid(row['layer_guid']);f.sublayer=sub.key;f.weight.type=0;f.count=1;f.conditions=C.pointer(condition);f.action.type=0x1001
                if f.count!=1 or not f.conditions or condition.value.value==0:raise ValueError('unqualified traffic filter forbidden')
                result=C.c_uint64();self._ok(self.f.FwpmFilterAdd0(handle,C.byref(f),None,C.byref(result)),'add exact-SID '+row['layer']);self.ids.append(result.value)
            self._ok(self.f.FwpmTransactionCommit0(handle),'commit dynamic transaction');txn=False
            for filter_id,row in zip(self.ids,self.plan):
                pointer=C.POINTER(FILTER)();self._ok(self.f.FwpmFilterGetById0(handle,filter_id,C.byref(pointer)),'read installed exact-SID filter')
                try:
                    f=pointer.contents;c=f.conditions[0] if f.count==1 and f.conditions else None
                    valid=f.flags&3==0 and same_guid(f.layer,guid(row['layer_guid'])) and same_guid(f.sublayer,sub.key) and f.action.type==0x1001 and c is not None and same_guid(c.key,guid(PACKAGE_ID)) and c.match==0 and c.value.type==13 and self.n.sid_text(C.c_void_p(c.value.value))==self.profile['sid']
                    if not valid:raise ValueError('installed filter differs from exact package-only plan')
                finally:self.f.FwpmFreeMemory0(C.byref(pointer))
            self.installed=True;return {'schema':'WFP-package-deny-lease-v1','package_sid':self.profile['sid'],'dynamic':True,'filter_ids':self.ids.copy(),'filter_count':len(self.ids),'scope':self.plan,'installed_and_readback_verified':True,'global_options_changed':False,'strict_study_admitted':False}
        except Exception:
            if txn:self.f.FwpmTransactionAbort0(handle)
            self.close(worker_stopped=True);raise
    def close(self,worker_stopped=False):
        if self.installed and not worker_stopped:raise ValueError('must confirm worker stopped before removing its network policy')
        if not self.handle:return self.cleanup
        self._ok(self.f.FwpmEngineClose0(self.handle),'close dynamic network session');self.handle=None;self.installed=False
        # In a committed session all lease IDs must disappear. In an aborted
        # transaction IDs were never active but must likewise be absent.
        check=W.HANDLE();fn=self.f.FwpmEngineOpen0;fn.argtypes=[W.LPCWSTR,W.UINT,C.c_void_p,C.c_void_p,C.POINTER(W.HANDLE)]
        self._ok(fn(None,10,None,None,C.byref(check)),'open cleanup read session');codes=[]
        try:
            for i in self.ids:
                pointer=C.POINTER(FILTER)();code=self.f.FwpmFilterGetById0(check,i,C.byref(pointer));codes.append(int(code))
                if code==0:self.f.FwpmFreeMemory0(C.byref(pointer))
        finally:self._ok(self.f.FwpmEngineClose0(check),'close cleanup read session')
        self.cleanup={'dynamic_session_closed':True,'filter_ids':self.ids.copy(),'filter_lookup_codes':codes,'all_owned_filters_absent':all(c==0x80320003 for c in codes)}
        if not self.cleanup['all_owned_filters_absent']:raise NetworkPolicyError('owned filter cleanup not confirmed',5)
        return self.cleanup
