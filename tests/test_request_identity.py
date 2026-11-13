"""Local logical isolation and identity binding; no OS/provider attestation."""
import copy
import json
import os
import sys
from pathlib import Path

import pytest

from ai4math.execution import export_task, run_task
from ai4math.request_identity import PROFILE, build_identity, verify_identity
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def scenario(tmp_path):
    w = Workbench.create(tmp_path/'ws', 'identity-test', 'Synthetic identity tests')
    n = w.apply('node.add', {'id':'goal','statement':'Fixture holds at n=1','domain':'n=1','quantifier':'finite'})
    w.apply('goal.set', {'node':'goal'})
    w.apply('route.add', {'id':'route','node':'goal','rationale':'protocol fixture','next_question':'identity?',
                        'stop_condition':'bounded check','max_runs':8,'stagnation_limit':2})
    w.apply('route.status', {'id':'route','status':'active','reason':'fixture'})
    source = tmp_path/'program.py'
    source.write_text("import json,os,sys,tempfile\nfrom pathlib import Path\nc=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
                      "r=dict(c['result_contract']);r.update(method='formal_kernel',scope='finite',evidence_level='proved',passed=True,checks=['synthetic fixture'],limitations=['not a mathematical checker'])\n"
                      "Path('observed.json').write_text(json.dumps({'env':dict(os.environ),'pid':os.getpid(),'cwd':os.getcwd(),'temp':tempfile.gettempdir(),'isolated':sys.flags.isolated,'dont_write_bytecode':sys.dont_write_bytecode}),encoding='utf-8')\n"
                      "Path('result.json').write_text(json.dumps(r),encoding='utf-8')\n", encoding='utf-8')
    program = w.apply('artifact.import', {'path':str(source),'purpose':'synthetic standalone program'})['id']
    public = tmp_path/'public.txt';public.write_text('public fixture',encoding='utf-8')
    a = w.apply('artifact.import', {'path':str(public),'purpose':'public fixture'})['id']
    yield w, a, program, n
    w.store.close()


def add(w, a, p, key='job', arm='A', **changes):
    payload = {'id':key,'route':'route','role':'produce','argv':[sys.executable,'{program}','{input}'],
               'program_artifact':p,'inputs':[a],'timeout_seconds':3,'output_limit_bytes':100000,
               'execution_profile':PROFILE,'request_context':{'trial_id':'trial-1','arm':arm,'request_id':'request-'+key}}
    payload.update(changes)
    return w.apply('task.add', payload, 'fixture-owner')


def test_identity_binds_immutable_task_and_frozen_program(scenario):
    w,a,p,_ = scenario;t=add(w,a,p)
    identity = t['request_identity']
    assert identity['input_artifacts']==[a] and identity['program_artifact']==p
    assert identity['sha256']==verify_identity(w.store,t)['sha256']
    exported=export_task(w,'job')['contract']
    assert exported['request_identity']==identity and exported['result_contract']['arm']=='A'
    assert exported['argv'][1:3]==['-I','-B']
    assert not exported['study_isolation_ready']


@pytest.mark.parametrize('field,value', [('timeout_seconds',4),('output_limit_bytes',20),('role','review'),('inputs',[]),('route','different'),('method_review','other-review')])
def test_changed_binding_changes_identity(scenario,field,value):
    w,a,p,_=scenario;t=add(w,a,p);other=copy.deepcopy(t);other[field]=value
    ctx={k:t['request_identity'][k] for k in ('trial_id','arm','request_id')}
    assert build_identity(w.store,other,ctx)['sha256']!=t['request_identity']['sha256']


@pytest.mark.parametrize('change', [{'arm':'D'},{'arm':True},{'trial_id':'../escape'},{'request_id':''},{'session_id':'history'}])
def test_bad_context_rejected_without_budget_or_partial_registry(scenario,change):
    w,a,p,_=scenario;ctx={'trial_id':'t','arm':'A','request_id':'r'};ctx.update(change)
    with pytest.raises(ResearchError):add(w,a,p,request_context=ctx)
    assert not w.store.all('task') and not w.store.all('request_identity')
    assert w.store.get('route','route')['runs']==0


def test_duplicate_request_id_rejected_and_original_preserved(scenario):
    w,a,p,_=scenario;t=add(w,a,p)
    with pytest.raises(ResearchError,match='already exists'):
        add(w,a,p,'another',request_context={'trial_id':'trial-2','arm':'B','request_id':'request-job'})
    assert len(w.store.all('task'))==1 and w.store.get('request_identity','request-job')==t['request_identity']


@pytest.mark.parametrize('profile',['study_isolated_v1','misspelled-profile'])
def test_unsupported_isolation_never_falls_back(scenario,profile):
    w,a,p,_=scenario
    with pytest.raises(ResearchError,match='isolation unavailable|unknown execution profile'):add(w,a,p,execution_profile=profile)
    assert not w.store.all('task')


def test_context_not_silently_ignored_in_legacy_profile(scenario):
    w,a,p,_=scenario
    with pytest.raises(ResearchError,match='explicit fresh'):add(w,a,p,execution_profile='trusted_local')


@pytest.mark.parametrize('argv',[[sys.executable,'-c','print(1)'],[sys.executable,'other.py','{input}']])
def test_fresh_program_must_be_frozen_standalone(scenario,argv):
    w,a,p,_=scenario
    with pytest.raises(ResearchError,match='fresh local argv'):add(w,a,p,argv=argv)


def test_fresh_runtime_and_environment_no_host_history(scenario,monkeypatch):
    w,a,p,_=scenario
    monkeypatch.setenv('AI4MATH_HOST_SECRET_CANARY','synthetic-not-a-secret')
    monkeypatch.setenv('PYTHONPATH','synthetic-host-history')
    add(w,a,p);out=run_task(w,'job','fixture-owner');assert out['status']=='completed' and out['contract_unchanged']
    observed=json.loads(w.store.inside('tasks/job/observed.json').read_text(encoding='utf-8'))
    assert 'AI4MATH_HOST_SECRET_CANARY' not in observed['env'] and 'PYTHONPATH' not in observed['env']
    assert observed['isolated']==1 and observed['dont_write_bytecode']
    assert Path(observed['temp'])==w.store.inside('tasks/job/runtime/tmp')
    assert Path(observed['env']['HOME'])==w.store.inside('tasks/job/runtime/home')
    receipt=json.loads(w.store.verify_blob(out['environment_artifact']).read_text(encoding='utf-8'))
    assert not receipt['study_isolation_ready'] and receipt['policy']['os_access_control'] is False
    assert out['executed_request_identity_sha256']==out['request_identity']['sha256']
    with pytest.raises(ResearchError,match='not pending'):run_task(w,'job')


def test_preexisting_runtime_not_adopted_or_silently_cleared(scenario):
    w,a,p,_=scenario;add(w,a,p);export_task(w,'job')
    marker=w.store.inside('tasks/job/runtime/history.txt');marker.parent.mkdir();marker.write_text('old context')
    with pytest.raises(ResearchError,match='runtime directory already exists'):run_task(w,'job')
    assert marker.read_text()=='old context' and w.store.get('route','route')['runs']==0


@pytest.mark.parametrize('action',["Path(sys.argv[1]).write_text('{}')","Path(sys.argv[1]).unlink()"])
def test_mutated_or_deleted_input_contract_not_success(scenario,tmp_path,action):
    w,a,_,_=scenario;source=tmp_path/'tamper.py';source.write_text('import sys\nfrom pathlib import Path\n'+action+'\n',encoding='utf-8')
    p=w.apply('artifact.import',{'path':str(source),'purpose':'tamper fixture'})['id'];add(w,a,p)
    out=run_task(w,'job');assert out['status']=='failed' and not out['contract_unchanged']
    assert w.store.get('route','route')['runs']==1


def test_workspace_binding_differs_with_identical_project_and_context(scenario,tmp_path):
    w,a,p,_=scenario;t=add(w,a,p)
    other=Workbench.create(tmp_path/'other','identity-test','Synthetic identity tests')
    try:
        # Same public artifact bytes imported into another workspace do not
        # authorize replay of the first workspace's result identity.
        other.apply('artifact.import',{'path':str(w.store.verify_blob(p)),'purpose':'same program'})
        ctx={k:t['request_identity'][k] for k in ('trial_id','arm','request_id')}
        assert build_identity(other.store,t,ctx)['sha256']!=t['request_identity']['sha256']
    finally:other.store.close()


@pytest.mark.parametrize('field,value',[('arm','B'),('trial_id','another-trial'),('request_id','another-request'),('request_identity_sha256','0'*64)])
def test_foreign_result_context_rejected_by_validation(scenario,tmp_path,field,value):
    w,a,_,n=scenario
    source=tmp_path/'checker.py';source.write_text("import json,sys\nfrom pathlib import Path\nc=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\nr=dict(c['result_contract']);r.update(method='formal_kernel',scope='finite',evidence_level='proved',passed=True,checks=['synthetic'],limitations=['fixture'])\nr["+repr(field)+"]="+repr(value)+"\nPath('result.json').write_text(json.dumps(r),encoding='utf-8')\n",encoding='utf-8')
    p=w.apply('artifact.import',{'path':str(source),'purpose':'foreign identity fixture'})['id']
    w.apply('claim.add',{'id':'claim','node':'goal','statement_digest':n['statement_digest'],'evidence_level':'proved','scope':'finite','artifact_ids':[a],'summary':'synthetic protocol claim'},'author')
    add(w,a,p,role='validate',argv=[sys.executable,'{validator}','{input}'],validator_artifact=p)
    assert run_task(w,'job','checker')['status']=='completed'
    with pytest.raises(ResearchError,match='does not match exact task'):
        w.apply('validation.add',{'id':'v','claim':'claim','method':'formal_kernel','task':'job','trust_basis':'synthetic only'},'checker')
    assert w.store.get('claim','claim')['status']=='proposed' and not w.store.all('validation')


def test_correct_fresh_validation_identity_is_usable(scenario):
    w,a,p,n=scenario
    w.apply('claim.add',{'id':'claim','node':'goal','statement_digest':n['statement_digest'],'evidence_level':'proved','scope':'finite','artifact_ids':[a],'summary':'synthetic protocol claim'},'author')
    add(w,a,p,role='validate',argv=[sys.executable,'{validator}','{input}'],validator_artifact=p)
    assert run_task(w,'job','checker')['status']=='completed'
    result=w.apply('validation.add',{'id':'v','claim':'claim','method':'formal_kernel','task':'job','trust_basis':'synthetic identity binding only; not real proof'},'checker')
    assert result['passed'] and w.store.get('claim','claim')['status']=='proposed'
