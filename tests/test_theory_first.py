import sys
from pathlib import Path

import pytest

from ai4math.execution import export_task, run_task
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def theory(tmp_path):
    w = Workbench.create(tmp_path / 'workspace', 'fresh', 'Theory-first experiment')
    w.apply('node.add', {'id': 'goal', 'statement': 'Fixed exact target', 'domain': 'all n>=1', 'quantifier': 'universal'})
    w.apply('goal.set', {'node': 'goal'})
    w.apply('research.configure', {'mode': 'theory_first', 'exclude_history': True, 'allowed_roots': [str(tmp_path / 'fresh')],
             'max_probe_cases': 3, 'rationale': 'Independent restart from definitions'})
    w.apply('route.add', {'id': 'route', 'node': 'goal', 'rationale': 'A structural normal form', 'next_question': 'Does it reduce unbounded complexity?',
             'stop_condition': 'No genuine structural reduction', 'max_runs': 3, 'stagnation_limit': 2})
    w.apply('route.status', {'id': 'route', 'status': 'active', 'reason': 'Inspect a mathematical mechanism'})
    design = w.store.inside('design.txt')
    design.write_text('Synthetic method with a precise mechanism, not a proved theorem.', encoding='utf-8')
    a = w.apply('artifact.import', {'path': str(design), 'purpose': 'new mathematical design'})['id']
    yield w, a, tmp_path
    w.store.close()


def method(w, a, key='method', **extra):
    return w.apply('method.add', {'id': key, 'route': 'route', 'kind': 'new_representation', 'proposed_tool': 'A bounded-defect normal form',
        'mechanism': 'Capacity equality restricts the number of defects uniformly.', 'falsifier': 'A saturating example with unbounded defects.',
        'minimal_probe': 'Check one adversarial boundary example only.', 'learning_goal': 'Reduce the universal proof to a smaller interface.',
        'obligations': ['goal'], 'artifact_ids': [a], **extra}, 'researcher')


def review(w, key='method-review', verdict='trial', **extra):
    return w.apply('method.review', {'id': key, 'method': 'method', 'review_kind': 'self', 'verdict': verdict,
        'reason': 'Design is sufficiently explicit for a bounded test; theorem unresolved.',
        'checks': [{'check': c, 'outcome': 'open', 'detail': 'Explicitly considered; remains a proof obligation.'}
                   for c in ['scope', 'mechanism', 'counterexample', 'noncomputational_value']], **extra}, 'researcher')


def job(w, **extra):
    return w.apply('task.add', {'id': 'job', 'route': 'route', 'role': 'explore', 'argv': [sys.executable, '-c', 'print(1)'],
        'timeout_seconds': 3, 'output_limit_bytes': 10000, 'method': 'method', 'activity': 'focused_probe',
        'learning_question': 'Can one small counterexample reject the precise proposed simplification?', 'cases': 1, **extra})


def test_history_source_is_not_importable(theory):
    w, a, tmp = theory
    old = tmp / 'old-trace.txt'
    old.write_text('Historical exploration', encoding='utf-8')
    with pytest.raises(ResearchError, match='allowlist'):
        w.apply('artifact.import', {'path': str(old), 'purpose': 'bias the new route'})


def test_policy_must_precede_evidence(theory):
    w, a, tmp = theory
    with pytest.raises(ResearchError, match='precede'):
        w.apply('research.configure', {'mode': 'theory_first', 'exclude_history': True, 'allowed_roots': [str(tmp)]})


def test_no_task_before_method_design_and_review(theory):
    w, a, tmp = theory
    with pytest.raises(ResearchError):
        job(w, method='')
    method(w, a)
    with pytest.raises(ResearchError, match='reviewed trial'):
        job(w)


def test_self_review_is_honest_and_never_proves(theory):
    w, a, tmp = theory
    method(w, a)
    r = review(w)
    assert r['same_author'] and not r['upgrades_mathematical_evidence']
    assert w.node_status('goal') == 'open' and not w.store.all('claim')


def test_self_cannot_claim_independent_method_review(theory):
    w, a, tmp = theory
    method(w, a)
    with pytest.raises(ResearchError, match='honestly'):
        review(w, review_kind='independent')


def test_incomplete_or_blocked_design_cannot_be_approved(theory):
    w, a, tmp = theory
    method(w, a)
    with pytest.raises(ResearchError, match='needs scope'):
        review(w, checks=[])
    blocked = [{'check': c, 'outcome': 'blocked', 'detail': 'Specific unresolved design error'}
               for c in ['scope', 'mechanism', 'counterexample', 'noncomputational_value']]
    with pytest.raises(ResearchError, match='blocked'):
        review(w, checks=blocked)


def test_bulk_probe_is_rejected(theory):
    w, a, tmp = theory
    method(w, a)
    review(w)
    with pytest.raises(ResearchError, match='case budget'):
        job(w, cases=1000000)


def test_focused_probe_contract_contains_mathematical_question(theory):
    w, a, tmp = theory
    method(w, a)
    review(w)
    job(w)
    exported = export_task(w, 'job')['contract']
    assert exported['method']['mechanism'] and exported['research_policy'][0]['exclude_history']
    assert run_task(w, 'job')['status'] == 'completed'
    assert w.board()['goal_evidence_level'] == 'unknown'


def test_rejected_method_stops_prepared_job(theory):
    w, a, tmp = theory
    method(w, a)
    review(w)
    job(w)
    review(w, 'reconsidered', verdict='park')
    with pytest.raises(ResearchError, match='method review changed'):
        run_task(w, 'job')
    assert w.store.get('route', 'route')['runs'] == 0


def test_method_approval_does_not_authorize_proved_note(theory):
    w, a, tmp = theory
    method(w, a)
    review(w)
    with pytest.raises(ResearchError):
        w.apply('research.note', {'id': 'note', 'method': 'method', 'kind': 'proof_attempt', 'summary': 'Self-approved proof',
            'artifact_ids': [a], 'evidence_level': 'proved'}, 'researcher')


def test_revisions_preserve_parent_and_material(theory):
    w, a, tmp = theory
    original = method(w, a)
    review(w, verdict='revise')
    revised = method(w, a, 'revision', parent='method', revision_reason='Separate a sufficient construction from a necessary normal form.')
    assert revised['parent'] == original['id']
    assert w.store.get('method', 'method')['status'] == 'revise'
