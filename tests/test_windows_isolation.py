"""Meaningful controller-boundary negatives, no provider/native study simulation."""
import hashlib,os,sys
from pathlib import Path
import pytest
from ai4math.windows_isolation import FrozenFile,FileRequest,IsolationError,NetworkIsolationUnavailable,relative_name,no_reparse,file_hash

@pytest.mark.parametrize('value',['.','..','../escape','/absolute','C:/drive','C:drive','\\\\server\\share','runtime\\backslash','input/a:stream','input/a.','input/a ','input/CON.txt','input/nul','input//double','input/./dot','input/../up','input/LPT9.txt'])
def test_ambiguous_or_escaping_path_rejected(value):
    with pytest.raises(IsolationError):relative_name(value)
def test_safe_canonical_nested_name():assert relative_name('input/task-01.json')=='input/task-01.json'
def test_source_hardlink_rejected(tmp_path):
    a=tmp_path/'a';a.write_bytes(b'public');b=tmp_path/'b';os.link(a,b)
    with pytest.raises(IsolationError,match='hardlinked'):no_reparse(a,hardlink_file=True)
@pytest.mark.parametrize('target',['../escape','control/auth.json','work/not-frozen','runtime/CON','input/a:stream'])
def test_unsafe_destination_refused_before_request_creation(tmp_path,target):
    source=tmp_path/'src';source.write_bytes(b'x');workspace=tmp_path/'workspace';workspace.mkdir()
    with pytest.raises(IsolationError):FileRequest(workspace,'req1',[FrozenFile(str(source),target,file_hash(source))],[tmp_path],'a'*64,'b'*64)
    assert not (workspace/'req1').exists()
def test_case_folded_collision_rejected(tmp_path):
    source=tmp_path/'src';source.write_bytes(b'x');workspace=tmp_path/'workspace';workspace.mkdir();sha=file_hash(source)
    with pytest.raises(IsolationError,match='collision'):FileRequest(workspace,'req',[FrozenFile(str(source),'input/X',sha),FrozenFile(str(source),'input/x',sha)],[tmp_path],'a'*64,'b'*64)
def test_unapproved_source_rejected(tmp_path):
    source=tmp_path/'src';source.write_bytes(b'x');workspace=tmp_path/'workspace';workspace.mkdir()
    with pytest.raises(IsolationError,match='approved'):FileRequest(workspace,'req',[FrozenFile(str(source),'input/x',file_hash(source))],[workspace],'a'*64,'b'*64)
def test_stage_size_bound_before_creating_root(tmp_path):
    source=tmp_path/'src';source.write_bytes(b'xx');workspace=tmp_path/'workspace';workspace.mkdir()
    with pytest.raises(IsolationError,match='byte limit'):FileRequest(workspace,'req',[FrozenFile(str(source),'input/x',file_hash(source))],[tmp_path],'a'*64,'b'*64,max_stage_bytes=1)
    assert not (workspace/'req').exists()
def test_reparse_ancestor_rejected(tmp_path):
    target=tmp_path/'target';target.mkdir();(target/'file').write_bytes(b'x');link=tmp_path/'link'
    try:link.symlink_to(target,target_is_directory=True)
    except OSError:pytest.skip('host does not permit creating a test symlink')
    with pytest.raises(IsolationError,match='reparse'):no_reparse(link/'file',hardlink_file=True)
def test_collector_refuses_hardlinked_output(tmp_path):
    (tmp_path/'work').mkdir();(tmp_path/'control').mkdir();a=tmp_path/'work/a';a.write_bytes(b'x');os.link(a,tmp_path/'work/b')
    req=object.__new__(FileRequest);req.root=tmp_path;req.state='terminal';req.receipt={'job_empty':True};req._collected=False
    with pytest.raises(IsolationError,match='hardlinked'):req.collect_outputs()
    assert not (tmp_path/'control/collected-outputs.json').exists()
def test_collector_requires_whole_job_empty(tmp_path):
    req=object.__new__(FileRequest);req.root=tmp_path;req.state='terminal';req.receipt={'job_empty':False};req._collected=False
    with pytest.raises(IsolationError,match='process-tree'):req.collect_outputs()
def test_frozen_byte_drift_rejected(tmp_path):
    for name in ['runtime','input','program']:(tmp_path/name).mkdir()
    p=tmp_path/'input/x';p.write_bytes(b'x');sha=file_hash(p);p.write_bytes(b'y')
    req=object.__new__(FileRequest);req.root=tmp_path;req.state='staged';req.manifest={'files':[{'target':'input/x','size':1,'sha256':sha}]}
    with pytest.raises(IsolationError,match='drift'):req.verify_frozen()
def test_network_dispatch_refuses_boolean_unlock_and_no_downgrade(tmp_path,monkeypatch):
    from ai4math import windows_isolation as m
    for name in ['runtime','input','program','control']:(tmp_path/name).mkdir()
    req=object.__new__(FileRequest);req.root=tmp_path;req.state='staged';req.manifest={'files':[]};req.launched=False;req.network_lease=None
    monkeypatch.setattr(m,'_native',lambda:pytest.fail('must not launch native process on missing network policy'))
    with pytest.raises(NetworkIsolationUnavailable):req.dispatch(network_guard=True)
    assert req.state=='network_refused' and not req.launched
    with pytest.raises(IsolationError):req.run_engineering_file_probe('runtime/python.exe',[])
def test_setup_hash_failure_consumes_id_and_cleans_identity(tmp_path,monkeypatch):
    from ai4math import windows_isolation as m
    source=tmp_path/'source';source.write_bytes(b'x');workspace=tmp_path/'workspace';workspace.mkdir();deleted=[]
    class Native:
        def create_profile(self,name):return {'name':name,'sid':'synthetic-unit-SID'}
        def delete_profile(self,p):deleted.append(p['name']);return {'deleted':True}
    monkeypatch.setattr(m,'_native',lambda:Native());monkeypatch.setattr(m,'current_user_sid',lambda:'synthetic-controller-SID');monkeypatch.setattr(m,'_acl',lambda *a,**k:{'synthetic':True})
    with pytest.raises(IsolationError,match='frozen hash'):FileRequest(workspace,'used',[FrozenFile(str(source),'input/x','0'*64)],[tmp_path],'a'*64,'b'*64)
    assert len(deleted)==1 and (workspace/'used/control/setup-failure.json').exists()
    with pytest.raises(IsolationError,match='consumed'):FileRequest(workspace,'used',[FrozenFile(str(source),'input/x',file_hash(source))],[tmp_path],'a'*64,'b'*64)
@pytest.mark.parametrize('sid',['S-1-15-2-1','S-1-15-2-2','S-1-1-0','',None,'S-1-15-2-1-2-3-4-5-6-4294967296'])
def test_network_plan_refuses_global_or_invalid_sid(sid):
    from ai4math._windows_network import scoped_plan
    with pytest.raises(ValueError):scoped_plan(sid)
def test_network_plan_is_exact_sid_dynamic_block_on_four_authorization_layers():
    from ai4math._windows_network import scoped_plan
    sid='S-1-15-2-1-2-3-4-5-6-7';plan=scoped_plan(sid)
    assert {p['layer'] for p in plan} == {
        'ALE_AUTH_CONNECT_V4', 'ALE_AUTH_CONNECT_V6',
        'ALE_AUTH_RECV_ACCEPT_V4', 'ALE_AUTH_RECV_ACCEPT_V6',
    }
    assert len(plan)==4 and len({p['layer_guid'] for p in plan})==4
    assert all(p['package_sid']==sid and p['condition_count']==1 and p['condition']=='ALE_PACKAGE_ID_EQUALS' and p['action']=='BLOCK' and p['persistent'] is False for p in plan)


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job and native output pipes')
@pytest.mark.parametrize('length,status', [(65536, 'completed'), (65537, 'output_limit')])
def test_native_capture_persists_at_most_one_stream_limit(tmp_path, length, status):
    from ai4math._windows_native import run

    program = "import os; os.write(2,b'e'*65536); os.write(1,b'o'*" + str(length) + ")"
    record = tmp_path / 'control'
    receipt = run(sys.executable, ['-I', '-S', '-B', '-c', program], tmp_path, record)
    assert receipt['status'] == status, receipt
    assert receipt['job_empty'] and receipt['all_owned_handles_closed']
    assert (record / 'stdout.bin').read_bytes() == b'o' * 65536
    assert (record / 'stderr.bin').read_bytes() == b'e' * 65536
    assert receipt['output_capture']['stdout']['observed_bytes_lower_bound'] == length
    assert receipt['output_capture']['stdout']['overflow_detected'] == (length > 65536)
    assert receipt['output_capture']['stderr']['stream_fully_drained']


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job interruption')
def test_interrupted_native_capture_retains_job_accounting(tmp_path, monkeypatch):
    from ai4math import _windows_native as native

    def interrupt(_stream):
        raise KeyboardInterrupt

    monkeypatch.setattr(native._OutputStream, 'pump', interrupt)
    record = tmp_path / 'control'
    receipt = native.run(sys.executable, ['-I', '-S', '-B', '-c', 'import time; time.sleep(2)'], tmp_path, record)
    assert receipt['status'] == 'interrupted' and receipt['resumed']
    assert receipt['job_empty'] and receipt['all_owned_handles_closed']
    assert receipt['OS_accounting']['active_processes'] == 0
    assert not receipt['output_capture']['stdout']['stream_fully_drained']


@pytest.mark.skipif(os.name!='nt',reason='Windows directory junction')
def test_real_windows_junction_ancestor_rejected(tmp_path):
    import subprocess
    target=tmp_path/'target';target.mkdir();(target/'file').write_bytes(b'x');link=tmp_path/'junction'
    r=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),str(target)],capture_output=True,timeout=5)
    if r.returncode:pytest.skip('host cannot create owned test junction')
    assert link.lstat().st_file_attributes&0x400
    try:
        with pytest.raises(IsolationError,match='reparse'):no_reparse(link/'file',hardlink_file=True)
    finally:
        # Remove just the verified junction itself, never recurse into its target.
        assert link.parent==tmp_path and link.lstat().st_file_attributes&0x400
        link.rmdir()
    assert (target/'file').read_bytes()==b'x'
