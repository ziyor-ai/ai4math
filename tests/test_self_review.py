"""Counterexamples identified by the same-agent engineering audit."""
import sys

import pytest

from ai4math.execution import export_task, run_task
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def research(tmp_path):
    w = Workbench.create(tmp_path / 'ws', 'self-review', 'Self review fixture')
    n = w.apply('node.add', {'id': 'goal', 'statement': 'Fixed statement', 'domain': 'n>=1', 'quantifier': 'universal'})
    w.apply('goal.set', {'node': 'goal'})
    w.apply('route.add', {'id': 'route', 'node': 'goal', 'rationale': 'Test a specific failure', 'next_question': 'Failure?',
                        'stop_condition': 'Stop at failure', 'max_runs': 3, 'stagnation_limit': 2})
    w.apply('route.status', {'id': 'route', 'status': 'active', 'reason': 'Bounded regression'})
    yield w, n
    w.store.close()


def test_finite_support_requires_actual_material(research):
    w, n = research
    with pytest.raises(ResearchError, match='finite support'):
        w.apply('claim.add', {'id': 'empty-support', 'node': 'goal', 'statement_digest': n['statement_digest'],
            'evidence_level': 'finite_support', 'scope': 'finite', 'summary': 'Unsupported observation',
            'evidence_domain': 'n=2', 'artifact_ids': []}, 'author')


def test_latest_review_follows_commit_order_not_wall_clock(research, monkeypatch):
    w, n = research
    w.apply('claim.add', {'id': 'c', 'node': 'goal', 'statement_digest': n['statement_digest'],
                       'evidence_level': 'unknown', 'scope': 'universal', 'summary': 'Unknown'}, 'author')
    base = {'claim': 'c', 'reviewer': 'reviewer', 'reviewer_kind': 'model', 'auditor': 'auditor',
            'coverage_complete': True, 'issues': []}
    monkeypatch.setattr('ai4math.workbench.now', lambda: '2100-01-01T00:00:00+00:00')
    w.apply('review.add', {**base, 'id': 'old', 'verdict': 'ready_for_human'}, 'auditor')
    monkeypatch.setattr('ai4math.workbench.now', lambda: '2000-01-01T00:00:00+00:00')
    w.apply('review.add', {**base, 'id': 'new', 'verdict': 'reject'}, 'auditor')
    with pytest.raises(ResearchError, match='latest review'):
        w.apply('claim.accept', {'claim': 'c', 'review': 'old'}, 'custodian')


def test_stale_research_task_cannot_silently_change_plan(research):
    w, n = research
    w.apply('task.add', {'id': 'task', 'route': 'route', 'role': 'explore', 'argv': [sys.executable, '-c', 'pass'],
                        'timeout_seconds': 3, 'output_limit_bytes': 10000})
    w.apply('node.add', {'id': 'lemma', 'statement': 'New obligation', 'domain': 'n>=1', 'quantifier': 'universal'})
    w.apply('node.plan', {'node': 'goal', 'proof_dependencies': ['lemma'], 'reason': 'Missing prerequisite'})
    with pytest.raises(ResearchError, match='plan changed'):
        run_task(w, 'task')
    assert w.store.get('route', 'route')['runs'] == 0


def test_lost_log_is_not_success(research, monkeypatch):
    w, n = research
    w.apply('task.add', {'id': 'task', 'route': 'route', 'role': 'explore', 'argv': [sys.executable, '-c', 'print(1)'],
                        'timeout_seconds': 3, 'output_limit_bytes': 10000})
    def broken_read(*args):
        raise OSError('simulated log I/O failure')
    monkeypatch.setattr('ai4math.execution.os.read', broken_read)
    result = run_task(w, 'task')
    assert result['status'] == 'failed'
    assert 'log I/O' in result['execution_error']


def test_event_payload_is_frozen_at_put(research):
    w, n = research
    with w.store.transaction('fixture.alias', 'test'):
        original = {'id': 'receipt', 'items': ['original']}
        w.store.put('fixture', 'receipt', original, new=True)
        original['items'].append('later mutation')
    assert w.audit()['passed']
    assert w.store.get('fixture', 'receipt')['items'] == ['original']
