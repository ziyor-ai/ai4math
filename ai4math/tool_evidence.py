"""Control-plane intake of externally checked finite tool evidence.

This module checks provenance and declared scope only. It never imports a
mathematical producer/validator, accepts a claim, or resets route budgets.
"""
from __future__ import annotations
import json
from .store import ResearchError,digest,identifier,now

def require(condition,message):
    if not condition:raise ResearchError(message)
def sha_string(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
def load(wb,artifact):
    path=wb.store.verify_blob(artifact)
    require(path.stat().st_size<=1048576,'tool evidence JSON exceeds byte limit')
    def pairs(rows):
        value={}
        for k,v in rows:
            require(k not in value,'duplicate JSON key in tool evidence');value[k]=v
        return value
    value=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=pairs)
    digest(value)  # Reject NaN/infinity and non-UTF-8 logical values.
    require(isinstance(value,dict),'tool evidence artifact must contain an object')
    return value

def attach(wb,p,actor):
    required={'id','node','statement_digest','scope','public_input_artifact','binding_artifact','candidate_artifact','audit_artifact','audit_task','checker_artifact','trust_basis','source_kind','propose_claim','summary'}
    require(required<=set(p) and set(p)<=required|{'origin_task'},'tool evidence fields missing or unsupported')
    identifier(p['id']);require(p['scope']=='finite','tool evidence scope must be finite')
    require(type(p['propose_claim']) is bool,'propose_claim must be boolean')
    require(p['source_kind'] in {'fresh_execution','historical_reverification','synthetic_fixture'},'unsupported tool evidence source kind')
    require(all(isinstance(p[k],str) and p[k].strip() for k in ['trust_basis','summary']),'tool evidence needs summary and trust_basis')
    request_digest=digest(p)
    try:existing=wb.store.get('tool_evidence',p['id'])
    except ResearchError:existing=None
    if existing:
        require(existing['request_digest']==request_digest,'immutable tool evidence ID conflict')
        for a in existing['artifact_ids']:wb.store.verify_blob(a)
        return existing
    node=wb.store.get('node',p['node']);require(p['statement_digest']==node['statement_digest'],'tool evidence statement/domain drift')
    task=wb.store.get('task',p['audit_task'])
    from .request_identity import PROFILE, verify_identity
    def executed_identity(t):
        if t.get('execution_profile')!=PROFILE:return None
        identity=verify_identity(wb.store,t)
        require(t.get('executed_request_identity_sha256')==identity['sha256'] and t.get('contract_unchanged') is True,'tool task frozen request was not executed unchanged')
        return identity
    require(task['role']=='validate' and task['node']==node['id'],'tool audit must be a validation task for this node')
    require(task['status'] not in {'pending','running'},'tool audit task is not terminal')
    require(task.get('validator_artifact')==p['checker_artifact'],'tool audit checker differs from executed task')
    require({p['public_input_artifact'],p['binding_artifact'],p['candidate_artifact']}<=set(task['inputs']),'tool audit did not bind all input artifacts')
    require(p['audit_artifact'] in set(task.get('output_artifacts',[]))|{task.get('result_artifact')},'tool audit result was not produced by the task')
    audit_identity=executed_identity(task);origin_identity=None
    binding=load(wb,p['binding_artifact']);candidate=load(wb,p['candidate_artifact']);audit=load(wb,p['audit_artifact'])
    public=wb.store.verify_blob(p['public_input_artifact']);raw=public.read_bytes();require(len(raw)<=65536,'public tool input exceeds byte limit')
    public_value=load(wb,p['public_input_artifact'])
    import hashlib
    require(binding.get('schema')=='math-audit-binding-v1' and candidate.get('schema')=='math-audit-candidate-v1','tool binding/candidate schema mismatch')
    require(all(candidate.get(k)==binding.get(k) and isinstance(binding.get(k),str) and binding[k] for k in ['audit_id','task_id','tool_id']),'tool public identity mismatch')
    require(candidate.get('input_utf8','').encode('utf-8')==raw and binding.get('input_bytes_sha256')==hashlib.sha256(raw).hexdigest(),'tool public input byte mismatch')
    require(binding.get('logical_input_sha256')==digest(public_value),'tool public logical input mismatch')
    receipt=candidate.get('kernel_receipt');require(isinstance(receipt,dict) and digest(receipt)==binding.get('kernel_receipt_sha256'),'whole tool receipt differs from bound original')
    require(receipt.get('captured_input_sha256')==binding['input_bytes_sha256'] and receipt.get('logical_input_sha256')==binding['logical_input_sha256'],'receipt input mismatch')
    require(audit.get('schema')=='finite-math-audit-v1' and audit.get('verification_status') in {'passed','failed','unknown'} and type(audit.get('accepted')) is bool,'invalid tool audit verdict')
    require(audit.get('shared_service_admitted') is False and audit.get('claim_registration_performed') is False,'tool audit overclaims service or claim acceptance')
    passed=audit['verification_status']=='passed'
    require(audit['accepted'] is passed,'tool audit accepted/status inconsistent')
    if passed:
        require(task['status']=='completed' and task.get('exit_code')==0,'passed tool audit needs completed zero-exit task')
        require(audit.get('scope')=='provided explicit finite input only' and audit.get('external_human_review') is False,'tool audit scope or human-review overclaim')
        expected={'audit_id':binding['audit_id'],'task_id':binding['task_id'],'tool_id':binding['tool_id'],'input_bytes_sha256':binding['input_bytes_sha256'],
                  'logical_input_sha256':binding['logical_input_sha256'],'kernel_receipt_sha256':binding['kernel_receipt_sha256'],'binding_sha256':digest(binding),'candidate_sha256':digest(candidate),
                  'output_sha256':receipt.get('output_sha256'),'producer_sha256':receipt.get('tool_sha256'),'policy_sha256':receipt.get('policy_sha256')}
        require(all(audit.get(k)==v for k,v in expected.items()),'tool audit not bound to exact inputs/receipt/version')
        require(receipt.get('kernel_status')=='completed' and receipt.get('output') is not None and receipt.get('output_sha256')==digest(receipt['output']),'completed tool output missing or changed')
        math_result=audit.get('mathematical_verification');require(isinstance(math_result,dict) and math_result.get('passed') is True,'tool audit lacks passed mathematical checker result')
        math_checks=math_result.get('field_checks');require(isinstance(math_checks,dict) and math_checks and all(v is True for v in math_checks.values()),'tool audit lacks complete mathematical field checks')
        expected_checks={'whole_receipt_host_binding','independent_math_verified','host_identity_binding','host_raw_input_binding','host_logical_input_binding','output_hash','pinned_validator_loaded'}
        require(isinstance(audit.get('checks'),dict) and expected_checks<=set(audit['checks']) and all(v is True for v in audit['checks'].values()),'tool audit has incomplete consistency checks')
        require(all(sha_string(v) for v in [audit.get('verifier_sha256'),receipt.get('tool_sha256'),receipt.get('policy_sha256')]),'tool audit version hashes missing')
    else:require(not p['propose_claim'],'failed/unknown tool result cannot propose supporting claim')
    origin=None
    if p['source_kind']=='fresh_execution':
        require(p.get('origin_task'),'fresh tool result needs an executed producer task')
        origin=wb.store.get('task',p['origin_task'])
        require(origin['role']=='produce' and origin['status']=='completed' and origin.get('exit_code')==0 and origin['node']==node['id'],'fresh tool producer task not completed for node')
        require(p['public_input_artifact'] in origin['inputs'],'fresh tool producer did not bind public input')
        matched=False
        for a in set(origin.get('output_artifacts',[]))|{origin.get('result_artifact')}:
            if a is None:continue
            try:value=load(wb,a)
            except (ResearchError,ValueError,UnicodeError):continue
            if digest(value)==binding['kernel_receipt_sha256']:matched=True;break
        require(matched,'fresh tool receipt was not captured from producer task')
        origin_identity=executed_identity(origin)
        require((audit_identity is None)==(origin_identity is None),'fresh tool producer/audit execution profiles differ')
        if audit_identity:
            require(all(audit_identity[k]==origin_identity[k] for k in ['workspace_sha256','trial_id','arm']),'fresh tool producer/audit trial or arm differs')
    else:require(not p.get('origin_task'),'nonfresh evidence must not claim producer execution')
    if p['source_kind']=='synthetic_fixture':require(not p['propose_claim'],'synthetic tool fixture cannot propose a mathematical claim')
    artifacts={p[k] for k in ['public_input_artifact','binding_artifact','candidate_artifact','audit_artifact','checker_artifact']}|set(task['inputs'])|set(task.get('output_artifacts',[]))
    if origin:artifacts|=set(origin['inputs'])|set(origin.get('output_artifacts',[]))
    for a in sorted(artifacts):wb.store.verify_blob(a)
    claim=None;domain=f"Single explicit finite input: public_task={binding['task_id']}; logical_input_sha256={binding['logical_input_sha256']}. No universal quantifier upgrade."
    if passed and p['propose_claim']:
        claim=wb._claim({'id':'tool-evidence-'+request_digest[:40],'node':node['id'],'statement_digest':node['statement_digest'],'evidence_level':'finite_support','scope':'finite',
          'summary':p['summary'],'evidence_domain':domain,'artifact_ids':sorted(artifacts)},actor)
    record={'id':p['id'],'request_digest':request_digest,'node':node['id'],'statement_digest':node['statement_digest'],'scope':'finite','evidence_domain':domain,
      'source_kind':p['source_kind'],'origin_task':p.get('origin_task'),'audit_task':task['id'],'tool_id':binding['tool_id'],'public_task_id':binding['task_id'],
      'verification_status':audit['verification_status'],'evidence_level':'finite_support' if passed else 'unknown','evidence_status':'verified_finite' if passed else 'unknown_result' if audit['verification_status']=='unknown' else 'rejected_result',
      'artifact_ids':sorted(artifacts),'binding_artifact':p['binding_artifact'],'candidate_artifact':p['candidate_artifact'],'audit_artifact':p['audit_artifact'],'public_input_artifact':p['public_input_artifact'],
      'checker_artifact':p['checker_artifact'],'trust_basis':p['trust_basis'],'summary':p['summary'],'claim':claim['id'] if claim else None,'claim_automatically_accepted':False,
      'input_bytes_sha256':binding['input_bytes_sha256'],'logical_input_sha256':binding['logical_input_sha256'],'kernel_receipt_sha256':binding['kernel_receipt_sha256'],
      'output_sha256':receipt.get('output_sha256'),'producer_sha256':receipt.get('tool_sha256'),'policy_sha256':receipt.get('policy_sha256'),'mathematical_verifier_sha256':audit.get('verifier_sha256'),
      'audit_request_identity_sha256':audit_identity['sha256'] if audit_identity else None,
      'origin_request_identity_sha256':origin_identity['sha256'] if origin_identity else None,
      'trial_id':audit_identity['trial_id'] if audit_identity else None,'arm':audit_identity['arm'] if audit_identity else None,
      'shared_service_admitted':False,'created_at':now(),'actor':actor}
    wb.store.put('tool_evidence',record['id'],record,new=True)
    return record
