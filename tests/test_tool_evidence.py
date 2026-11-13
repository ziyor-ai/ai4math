"""Control-plane contract fixtures only: no real math tool/project imports."""
import hashlib,json
from copy import deepcopy
import pytest
import sys
from ai4math.store import ResearchError,digest
from ai4math.workbench import Workbench
from ai4math.report import markdown
from ai4math.cli import build_parser

@pytest.fixture
def setup(tmp_path):
    w=Workbench.create(tmp_path/'workspace','unit-tools','Unit provenance fixtures')
    n=w.apply('node.add',{'id':'target','statement':'Synthetic finite contract fixture','domain':'unit input only','quantifier':'finite'})
    w.apply('goal.set',{'node':'target'})
    count=[0]
    def artifact(value):
        count[0]+=1;p=tmp_path/f'a{count[0]}.json';p.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')),encoding='utf-8')
        return w.apply('artifact.import',{'path':str(p),'purpose':'synthetic contract fixture'})['id']
    public={'fixture':1};public_id=artifact(public);raw=w.store.verify_blob(public_id).read_bytes()
    receipt={'kernel_status':'completed','captured_input_sha256':hashlib.sha256(raw).hexdigest(),'logical_input_sha256':digest(public),'output':{'unit_value':2},'tool_sha256':'a'*64,'policy_sha256':'b'*64}
    receipt['output_sha256']=digest(receipt['output'])
    binding={'schema':'math-audit-binding-v1','audit_id':'unit-audit','task_id':'unit-public','tool_id':'unit-tool','input_bytes_sha256':hashlib.sha256(raw).hexdigest(),'logical_input_sha256':digest(public),'kernel_receipt_sha256':digest(receipt)}
    candidate={'schema':'math-audit-candidate-v1','audit_id':binding['audit_id'],'task_id':binding['task_id'],'tool_id':binding['tool_id'],'input_utf8':raw.decode(),'kernel_receipt':receipt}
    checks={k:True for k in ['whole_receipt_host_binding','independent_math_verified','host_identity_binding','host_raw_input_binding','host_logical_input_binding','output_hash','pinned_validator_loaded']}
    verdict={'schema':'finite-math-audit-v1','verification_status':'passed','accepted':True,'scope':'provided explicit finite input only','external_human_review':False,
      'shared_service_admitted':False,'claim_registration_performed':False,'checks':checks,'mathematical_verification':{'passed':True,'field_checks':{'unit_fixture':True}},'verifier_sha256':'c'*64,
      'audit_id':binding['audit_id'],'task_id':binding['task_id'],'tool_id':binding['tool_id'],'input_bytes_sha256':binding['input_bytes_sha256'],'logical_input_sha256':binding['logical_input_sha256'],
      'kernel_receipt_sha256':binding['kernel_receipt_sha256'],'binding_sha256':digest(binding),'candidate_sha256':digest(candidate),'output_sha256':receipt['output_sha256'],'producer_sha256':receipt['tool_sha256'],'policy_sha256':receipt['policy_sha256']}
    bid=artifact(binding);cid=artifact(candidate);aid=artifact(verdict);checker=artifact({'checker':'declared synthetic test only'})
    task={'id':'audit','role':'validate','node':'target','status':'completed','exit_code':0,'validator_artifact':checker,'inputs':[public_id,bid,cid],'output_artifacts':[aid]}
    with w.store.transaction('unit.fixture','fixture'):w.store.put('task','audit',task,new=True)
    p={'id':'evidence','node':'target','statement_digest':n['statement_digest'],'scope':'finite','public_input_artifact':public_id,'binding_artifact':bid,'candidate_artifact':cid,
       'audit_artifact':aid,'audit_task':'audit','checker_artifact':checker,'trust_basis':'Synthetic test data; no mathematical acceptance claimed','source_kind':'historical_reverification','propose_claim':True,'summary':'Unit finite evidence'}
    yield w,p,artifact,task,verdict,binding,candidate
    w.store.close()

def change_task(w,task,**extra):
    task={**task,**extra}
    with w.store.transaction('unit.fixture.change','fixture'):w.store.put('task','audit',task)
    return task

def change_audit(w,p,artifact,task,verdict,fn):
    value=deepcopy(verdict);fn(value);aid=artifact(value);p={**p,'audit_artifact':aid};task=change_task(w,task,output_artifacts=[aid]);return p,task

def test_proposed_finite_only_and_idempotent(setup):
    w,p,*_=setup;r=w.apply('tool.evidence.add',p,'same-author');c=w.store.get('claim',r['claim'])
    assert c['evidence_level']=='finite_support' and c['scope']=='finite' and c['status']=='proposed'
    assert not r['claim_automatically_accepted'] and w.node_status('target')=='open'
    before=w.audit();assert w.apply('tool.evidence.add',p,'same-author')==r;assert w.audit()['events']==before['events']
    assert r['id'] in markdown(w) and w.board()['tool_evidence']==[r] and w.audit()['passed']

@pytest.mark.parametrize('field,value',[('scope','universal'),('statement_digest','0'*64),('checker_artifact','f'*64),('audit_task','absent'),('source_kind','unknown'),('propose_claim',1)])
def test_invalid_contract_rejected(setup,field,value):
    w,p,*_=setup;p={**p,field:value}
    with pytest.raises(ResearchError):w.apply('tool.evidence.add',p)
    assert not w.store.all('tool_evidence') and not w.store.all('claim')

@pytest.mark.parametrize('status',['pending','running'])
def test_pending_task_not_evidence(setup,status):
    w,p,_,task,*_=setup;change_task(w,task,status=status)
    with pytest.raises(ResearchError,match='terminal'):w.apply('tool.evidence.add',p)

def test_result_must_be_real_task_output(setup):
    w,p,artifact,task,verdict,*_=setup;other=artifact({'not':'task output'});p={**p,'audit_artifact':other}
    with pytest.raises(ResearchError,match='produced'):w.apply('tool.evidence.add',p)

def test_task_must_bind_inputs(setup):
    w,p,_,task,*_=setup;change_task(w,task,inputs=[])
    with pytest.raises(ResearchError,match='bind'):w.apply('tool.evidence.add',p)

@pytest.mark.parametrize('mutation',[lambda v:v.update(candidate_sha256='0'*64),lambda v:v.update(output_sha256='0'*64),lambda v:v.update(scope='universal'),
    lambda v:v.update(external_human_review=True),lambda v:v.update(shared_service_admitted=True),lambda v:v.update(checks={'fake':True}),
    lambda v:v.update(mathematical_verification={'passed':True}),lambda v:v.update(verifier_sha256=None)])
def test_green_boolean_and_scope_spoofing_rejected(setup,mutation):
    w,p,artifact,task,verdict,*_=setup;p,task=change_audit(w,p,artifact,task,verdict,mutation)
    with pytest.raises(ResearchError):w.apply('tool.evidence.add',p)
    assert not w.store.all('claim') and not w.store.all('tool_evidence')

@pytest.mark.parametrize('status',['failed','unknown'])
def test_failed_unknown_archive_without_supporting_claim(setup,status):
    w,p,artifact,task,verdict,*_=setup
    p,task=change_audit(w,p,artifact,task,verdict,lambda v:v.update(verification_status=status,accepted=False))
    change_task(w,task,status='failed',exit_code=2 if status=='failed' else 3)
    p.update(propose_claim=False,source_kind='synthetic_fixture');r=w.apply('tool.evidence.add',p)
    assert r['verification_status']==status and r['evidence_level']=='unknown' and r['claim'] is None and not w.store.all('claim')

def test_failed_cannot_propose(setup):
    w,p,artifact,task,verdict,*_=setup;p,task=change_audit(w,p,artifact,task,verdict,lambda v:v.update(verification_status='failed',accepted=False))
    with pytest.raises(ResearchError,match='cannot propose'):w.apply('tool.evidence.add',p)

def test_completed_exit_is_not_passed_math(setup):
    w,p,artifact,task,verdict,*_=setup;p,task=change_audit(w,p,artifact,task,verdict,lambda v:v.update(verification_status='failed',accepted=False));p['propose_claim']=False
    assert w.apply('tool.evidence.add',p)['evidence_status']=='rejected_result'

def test_fresh_needs_actual_captured_receipt(setup):
    w,p,artifact,task,verdict,binding,candidate=setup;p.update(source_kind='fresh_execution',origin_task='producer')
    origin={'id':'producer','role':'produce','node':'target','status':'completed','exit_code':0,'inputs':[p['public_input_artifact']],'output_artifacts':[]}
    with w.store.transaction('unit.fixture','fixture'):w.store.put('task','producer',origin,new=True)
    with pytest.raises(ResearchError,match='not captured'):w.apply('tool.evidence.add',p)
    receipt=artifact(candidate['kernel_receipt']);origin['output_artifacts']=[receipt]
    with w.store.transaction('unit.fixture','fixture'):w.store.put('task','producer',origin)
    assert w.apply('tool.evidence.add',p)['origin_task']=='producer'

def test_synthetic_fixture_never_proposes_claim(setup):
    w,p,*_=setup;p['source_kind']='synthetic_fixture'
    with pytest.raises(ResearchError,match='synthetic'):w.apply('tool.evidence.add',p)

def test_immutable_id_and_frozen_bytes(setup):
    w,p,*_=setup;r=w.apply('tool.evidence.add',p)
    with pytest.raises(ResearchError,match='immutable'):w.apply('tool.evidence.add',{**p,'summary':'changed'})
    w.store.blob(r['candidate_artifact']).write_bytes(b'tampered')
    assert not w.audit()['passed']
    with pytest.raises(ResearchError):w.apply('tool.evidence.add',p)

def test_no_automatic_proved_field(setup):
    w,p,*_=setup
    with pytest.raises(ResearchError,match='unsupported'):w.apply('tool.evidence.add',{**p,'evidence_level':'proved'})

def test_cli_operation_registered():
    args=build_parser().parse_args(['--workspace','unused','apply','tool.evidence.add','--json','{}'])
    assert args.operation=='tool.evidence.add'


def fresh_fixture(w,task,request,arm='A',trial='trial',role='validate'):
    """Install explicitly synthetic control records; never a real checker."""
    from ai4math.request_identity import build_identity
    t={**task,'role':role,'route':'unit-route','node_snapshot':w.store.get('node','target'),
       'timeout_seconds':1,'output_limit_bytes':10000,'execution_profile':'fresh_local_v1'}
    if role=='produce':t.update(program_artifact=task['validator_artifact'],validator_artifact=None,argv=[sys.executable,'{program}','{input}'])
    else:t.update(argv=[sys.executable,'{validator}','{input}'])
    identity=build_identity(w.store,t,{'trial_id':trial,'arm':arm,'request_id':request})
    t.update(request_identity=identity,executed_request_identity_sha256=identity['sha256'],contract_unchanged=True)
    with w.store.transaction('unit.synthetic_fresh_identity','fixture'):
        w.store.put('request_identity',request,identity,new=True);w.store.put('task',t['id'],t)
    return t


def fresh_pair(setup,origin_arm='A',origin_trial='trial',audit_fresh=True,origin_fresh=True):
    w,p,artifact,task,verdict,binding,candidate=setup
    p.update(source_kind='fresh_execution',origin_task='producer')
    if audit_fresh:task=fresh_fixture(w,task,'audit-request')
    receipt=artifact(candidate['kernel_receipt'])
    origin={'id':'producer','role':'produce','node':'target','status':'completed','exit_code':0,
            'inputs':[p['public_input_artifact']],'output_artifacts':[receipt],'validator_artifact':p['checker_artifact']}
    with w.store.transaction('unit.synthetic_producer','fixture'):w.store.put('task','producer',origin,new=True)
    if origin_fresh:origin=fresh_fixture(w,origin,'producer-request',origin_arm,origin_trial,'produce')
    return w,p,task,origin


def test_fresh_evidence_retains_both_request_identities(setup):
    w,p,a,o=fresh_pair(setup);r=w.apply('tool.evidence.add',p)
    assert r['trial_id']=='trial' and r['arm']=='A'
    assert r['audit_request_identity_sha256']==a['request_identity']['sha256']
    assert r['origin_request_identity_sha256']==o['request_identity']['sha256']
    assert r['audit_request_identity_sha256']!=r['origin_request_identity_sha256']
    assert w.store.get('claim',r['claim'])['status']=='proposed'


@pytest.mark.parametrize('arm,trial',[('B','trial'),('A','other-trial')])
def test_fresh_cross_arm_or_trial_receipt_rejected(setup,arm,trial):
    w,p,*_=fresh_pair(setup,arm,trial)
    with pytest.raises(ResearchError,match='trial or arm differs'):w.apply('tool.evidence.add',p)
    assert not w.store.all('tool_evidence') and not w.store.all('claim')


@pytest.mark.parametrize('audit_fresh,origin_fresh',[(True,False),(False,True)])
def test_fresh_cannot_mix_new_identity_and_legacy_execution(setup,audit_fresh,origin_fresh):
    w,p,*_=fresh_pair(setup,audit_fresh=audit_fresh,origin_fresh=origin_fresh)
    with pytest.raises(ResearchError,match='profiles differ'):w.apply('tool.evidence.add',p)


@pytest.mark.parametrize('field,value',[('executed_request_identity_sha256','0'*64),('contract_unchanged',False)])
def test_fresh_task_execution_binding_must_be_intact(setup,field,value):
    w,p,a,_=fresh_pair(setup);a[field]=value
    with w.store.transaction('unit.synthetic_bad_execution','fixture'):w.store.put('task',a['id'],a)
    with pytest.raises(ResearchError,match='executed unchanged'):w.apply('tool.evidence.add',p)


def test_synthetic_fresh_audit_has_identity_but_no_claim(setup):
    w,p,_,task,*_=setup;fresh_fixture(w,task,'audit-request');p.update(source_kind='synthetic_fixture',propose_claim=False)
    r=w.apply('tool.evidence.add',p);assert r['arm']=='A' and r['origin_request_identity_sha256'] is None and r['claim'] is None
