"""Managed workflow v2 uses isolated fixtures; no Codex/model or project workspace."""
import copy
import json
import sys
import time
from pathlib import Path

import pytest

from ai4math import external_deadline as deadline
from ai4math.execution import _start_task, export_task, run_task, start_external
from ai4math.external_supervisor import supervise_external, _codex_stop_mailbox
from ai4math.store import ResearchError, file_hash
from ai4math.workbench import Workbench
from test_strategy import node, route, decide, coordinator_literature, worker_receipt


def setup(tmp_path, timeout=None):
    w = Workbench.create(tmp_path / 'workspace', 'codex-v2', 'Monitored fixture')
    node(w, 'a'); w.apply('goal.set', {'node': 'a'})
    route(w, 'a', 'a'); decide(w, 'direction', 'switch', 'a'); coordinator_literature(w, 'direction', 'a')
    path = tmp_path / 'skill.json'; path.write_text('{"skill":"fixture-only"}')
    skill = w.apply('artifact.import', {'path': str(path), 'purpose': 'test skill'})['id']
    p = {'id':'managed', 'route':'a', 'role':'explore', 'argv':[sys.executable,'-c','pass'],
         'timeout_seconds':timeout, 'output_limit_bytes':2000, 'inputs':[skill], 'external_supervision':'process',
         'codex_workflow':{'skill_manifest_artifact':skill,'worker_actor':'worker'}}
    return w, p


def dispatch(w):
    exported, task, _ = _start_task(w, 'managed', 'human', external=True, supervised=True, managed_codex=True)
    return {**exported, 'contract_sha256':task['contract_artifact']}


def release(w, handoff):
    ready = w.apply('task.literature_ready', worker_receipt(w, 'managed', handoff), 'worker')
    p={'task':'managed','worker_receipt_sha256':ready['worker_receipt_sha256'],'assessment':'fixture reviewed'}
    return w.apply('task.release_exploration', p)


def test_v2_unbounded_stage_times_and_pinned_export(tmp_path, monkeypatch):
    w,p=setup(tmp_path)
    try:
        task=w.apply('task.add',p)
        assert task['external_deadline_policy']['version']==2
        e=export_task(w,'managed');assert e['contract']['codex_workflow']==task['codex_workflow']
        assert 'no stage deadline' in (Path(e['directory'])/'PROMPT.md').read_text(encoding='utf-8')
        d=dispatch(w)
        monkeypatch.setattr(deadline,'now',lambda:'2099-01-01T00:00:00+00:00')
        assert not deadline.expire(w,'managed')
        release(w,d)
        t=w.store.get('task','managed')
        assert t['external_deadline_at'] is None
        assert all(set(x)=={'started_at'} for x in t['phase_timing'].values())
        with pytest.raises(ResearchError):deadline.enter(t,'literature')
    finally:w.store.close()


@pytest.mark.parametrize('change',[
    {'external_supervision':'host'}, {'phase_budgets':dict.fromkeys(deadline.PHASES,60)},
    {'role':'review'}, {'timeout_seconds':0}, {'timeout_seconds':True},
    {'codex_workflow':{'skill_manifest_artifact':'0'*64,'worker_actor':'worker'}},
    {'codex_workflow':{'skill_manifest_artifact':'0'*64,'worker_actor':'human'}},
])
def test_v2_rejects_unsupported_contracts(tmp_path,change):
    w,p=setup(tmp_path)
    try:
        with pytest.raises(ResearchError):w.apply('task.add',{**p,**change})
        assert not w.store.exists('task','managed')
    finally:w.store.close()


def test_legacy_null_rejected_and_generic_bypass_does_not_consume(tmp_path):
    w,p=setup(tmp_path)
    try:
        old=copy.deepcopy(p);old.pop('codex_workflow');old['phase_budgets']=dict.fromkeys(deadline.PHASES,60)
        with pytest.raises(ResearchError):w.apply('task.add',old)
        w.apply('task.add',p)
        for call in [lambda:run_task(w,'managed'),lambda:start_external(w,'managed'),
                     lambda:supervise_external(w,'managed',[sys.executable,'-c','pass'])]:
            with pytest.raises(ResearchError,match='owned Codex runtime'):call()
        assert w.store.get('route','a')['runs']==0
        assert not (w.store.root/'tasks/managed').exists()
    finally:w.store.close()


def test_dispatch_hold_and_pinned_worker(tmp_path):
    w,p=setup(tmp_path)
    try:
        w.apply('task.add',p)
        w.apply('codex.dispatch_hold',{'held':True,'reason':'review pending'})
        with pytest.raises(ResearchError,match='dispatch is held'):dispatch(w)
        assert w.store.get('route','a')['runs']==0
        w.apply('codex.dispatch_hold',{'held':False,'reason':'review done'})
        d=dispatch(w)
        with pytest.raises(ResearchError,match='pinned worker_actor'):
            w.apply('task.literature_ready',{'task':'managed'},'foreign')
        release(w,d)
    finally:w.store.close()


def test_hold_blocks_progress_without_reset_and_stop_is_final(tmp_path):
    w,p=setup(tmp_path,timeout=60)
    try:
        w.apply('task.add',p);d=dispatch(w)
        original=w.store.get('task','managed')['external_deadline_at']
        w.apply('task.codex_hold',{'task':'managed','held':True,'reason':'pause tools'})
        with pytest.raises(ResearchError,match='held'):
            w.apply('task.literature_ready',worker_receipt(w,'managed',d),'worker')
        with pytest.raises(ResearchError):w.apply('task.codex_hold',{'task':'managed','held':False,'reason':'not coordinator'},'worker')
        w.apply('task.codex_hold',{'task':'managed','held':False,'reason':'continue'})
        assert w.store.get('task','managed')['external_deadline_at']==original
        release(w,d)
        assert w.store.get('task','managed')['external_deadline_at']==original
        stopped=w.apply('task.codex_stop',{'task':'managed','reason':'explicit cancellation'})
        assert stopped['status']=='interrupted' and stopped['stop_state']=='requested'
        with pytest.raises(ResearchError):w.apply('task.begin_seal',{'task':'managed'})
        assert w.store.get('route','a')['runs']==1
        assert w.audit()['passed']
    finally:w.store.close()


@pytest.mark.parametrize('timeout',[None,60])
def test_v2_batch_seal_freshness_stop_and_acceptance(tmp_path,monkeypatch,timeout):
    w,p=setup(tmp_path,timeout=timeout)
    try:
        w.apply('task.add',p);d=dispatch(w);release(w,d)
        root=w.store.root/'tasks/managed/return';root.mkdir(exist_ok=True)
        old=p['inputs'][0];(root/'old.json').write_bytes(w.store.verify_blob(old).read_bytes())
        fresh=root/'fresh.txt';fresh.write_text('new actual output for v2')
        batch={'task':'managed','contract_sha256':d['contract_sha256'],'artifacts':[{'path':'old.json','sha256':old}]}
        assert not w.apply('task.preflight_seal_batch',batch,'worker')['passed']
        batch['artifacts']=[{'path':fresh.name,'sha256':file_hash(fresh)}]
        w.apply('task.begin_seal',{'task':'managed'},'worker')
        out=w.apply('task.submit_seal_batch',batch,'worker')
        assert out['external_stage']=='sealed'
        monkeypatch.setattr(deadline,'now',lambda:'2099-01-01T00:00:00+00:00')
        receipt=out['receipt'];finish={'task':'managed','status':'completed','summary':'fixture only','artifact_ids':receipt['output_artifacts'],'seal_manifest_artifact':receipt['artifact_id']}
        with pytest.raises(ResearchError):w.apply('task.finish_external',finish)
        w.apply('task.confirm_stop',{'task':'managed','confirmed':True,'evidence':'fixture had no process'})
        assert w.apply('task.finish_external',finish)['evidence_level']=='unknown'
        assert w.audit()['passed']
    finally:w.store.close()


def test_total_deadline_while_held_persists(tmp_path,monkeypatch):
    w,p=setup(tmp_path,timeout=2)
    try:
        w.apply('task.add',p);dispatch(w)
        w.apply('task.codex_hold',{'task':'managed','held':True,'reason':'wait'})
        cutoff=w.store.get('task','managed')['external_deadline_at'];monkeypatch.setattr(deadline,'now',lambda:cutoff)
        with pytest.raises(ResearchError):w.apply('task.codex_hold',{'task':'managed','held':False,'reason':'too late'})
        assert w.store.get('task','managed')['status']=='unknown_timeout'
    finally:w.store.close()


def test_optional_toolchain_and_directory_scope_are_frozen(tmp_path):
    w,p=setup(tmp_path)
    try:
        path=tmp_path/'toolchain.json';path.write_text('{"python":"fixture"}',encoding='utf-8')
        tool=w.apply('artifact.import',{'path':str(path),'purpose':'tools fixture'})['id']
        p['inputs'].append(tool)
        p['codex_workflow'].update(toolchain_artifact=tool,source_roots=[str(tmp_path.resolve())])
        w.apply('task.add',p)
        exported=export_task(w,'managed')['contract']
        assert exported['codex_workflow']['toolchain_artifact']==tool
        assert exported['codex_workflow']['source_roots']==[str(tmp_path.resolve())]
        for bad in ({'toolchain_artifact':'f'*64}, {'source_roots':['relative']}, {'source_roots':[str(path)]}):
            with pytest.raises(ResearchError):
                w.apply('task.add',{**p,'id':'bad','codex_workflow':{**p['codex_workflow'],**bad}})
    finally:w.store.close()


def test_v2_release_commit_crossing_total_deadline_rolls_back(tmp_path,monkeypatch):
    w,p=setup(tmp_path,timeout=60)
    try:
        w.apply('task.add',p);d=dispatch(w)
        ready=w.apply('task.literature_ready',worker_receipt(w,'managed',d),'worker')
        original=w.store.get('task','managed')
        commit=w.store._commit
        def delayed(operation,actor,before_commit=None,metrics=None):
            if operation=='task.release_exploration':
                monkeypatch.setattr(deadline,'now',lambda:original['external_deadline_at'])
            return commit(operation,actor,before_commit,metrics)
        monkeypatch.setattr(w.store,'_commit',delayed)
        with pytest.raises(ResearchError,match='before commit'):
            w.apply('task.release_exploration',{'task':'managed','worker_receipt_sha256':ready['worker_receipt_sha256'],'assessment':'fixture'})
        task=w.store.get('task','managed')
        assert task['status']=='unknown_timeout'
        assert task['external_stage']=='awaiting_coordinator_review'
        assert task['phase_timing']==original['phase_timing']
        assert not w.store.exists('exploration_release','managed')
        assert w.audit()['passed']
    finally:w.store.close()


@pytest.mark.parametrize('mode',['timeout','overflow','stop','exited_owner_with_locked_child','mailbox_with_sqlite_lock'])
def test_native_process_control_v2(tmp_path,mode):
    w,p=setup(tmp_path,timeout=1 if mode=='timeout' else None)
    try:
        w.apply('task.add',p)
        marker=tmp_path/'escaped'
        child="import time,pathlib;time.sleep(2);pathlib.Path("+repr(str(marker))+").write_text('escaped')"
        if mode=='timeout':
            code="import sqlite3,subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);c=sqlite3.connect("+repr(str(w.store.db_path))+");c.execute('BEGIN IMMEDIATE');time.sleep(20)"
        elif mode=='exited_owner_with_locked_child':
            ready=tmp_path/'lock-ready'
            locked_child=("import sqlite3,time,pathlib;c=sqlite3.connect("+repr(str(w.store.db_path))+");"
                          "c.execute('BEGIN IMMEDIATE');pathlib.Path("+repr(str(ready))+").write_text('ready');"
                          "time.sleep(20);pathlib.Path("+repr(str(marker))+").write_text('escaped')")
            code=("import subprocess,sys,time,pathlib\nsubprocess.Popen([sys.executable,'-c',"+repr(locked_child)+"])\n"
                  "end=time.monotonic()+3\nwhile not pathlib.Path("+repr(str(ready))+").exists():\n"
                  " if time.monotonic()>end:raise RuntimeError('child never locked')\n time.sleep(.02)\n"
                  "raise SystemExit(23)\n")
        elif mode=='overflow':code="import sys,time;sys.stdout.write('x'*10000);sys.stdout.flush();time.sleep(20)"
        elif mode=='mailbox_with_sqlite_lock':
            code=("import sqlite3,pathlib,json,hashlib,time;c=sqlite3.connect("+repr(str(w.store.db_path))+");"
                  "c.execute('BEGIN IMMEDIATE');p=pathlib.Path('live/requests');p.mkdir(parents=True);"
                  "row={'id':'fixture','action':'interrupt','task':'managed','actor':'human','reason':'stop without a deadline',"
                  "'contract_sha256':hashlib.sha256(pathlib.Path('input.json').read_bytes()).hexdigest()};"
                  "(p/'fixture.json').write_text(json.dumps(row),encoding='utf-8');time.sleep(20)")
        else:
            code="import sys,time;sys.path.insert(0,"+repr(str(Path(__file__).resolve().parents[1]))+");from ai4math.workbench import Workbench;time.sleep(.2);w=Workbench("+repr(str(w.store.root))+");w.apply('task.codex_stop',{'task':'managed','reason':'fixture stop'});time.sleep(20)"
        started=time.monotonic();out=supervise_external(w,'managed',[sys.executable,'-B','-c',code],managed_codex=True)
        assert time.monotonic()-started<8 and out['stop_state']=='confirmed'
        assert out['status']=={'timeout':'unknown_timeout','overflow':'failed','stop':'interrupted','exited_owner_with_locked_child':'failed','mailbox_with_sqlite_lock':'interrupted'}[mode]
        if mode=='mailbox_with_sqlite_lock':
            receipt=json.loads((w.store.root/'tasks/managed/live/native-stop.json').read_text(encoding='utf-8'))
            assert receipt['local_tree_stopped'] and receipt['request_id']=='fixture' and receipt['grace_seconds']==3
        assert not marker.exists()
        assert w.store.get('route','a')['runs']==1
    finally:w.store.close()


def test_native_stop_mailbox_binding(tmp_path):
    task={'id':'task','contract_artifact':'a'*64,'dispatched_by':'coordinator'}
    row={'id':'request','action':'interrupt','task':'task','contract_sha256':'a'*64,'actor':'coordinator','reason':'stop'}
    path=tmp_path/'request.json'
    path.write_text(json.dumps(row),encoding='utf-8')
    assert _codex_stop_mailbox(path,task)['request_id']=='request'
    for field,value in [('id','other'),('action','steer'),('task','foreign'),('contract_sha256','b'*64),('actor','worker'),('reason','')]:
        path.write_text(json.dumps({**row,field:value}),encoding='utf-8')
        assert _codex_stop_mailbox(path,task) is None
    path.write_text('['+' '*65536+']',encoding='utf-8')
    assert _codex_stop_mailbox(path,task) is None
