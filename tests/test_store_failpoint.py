"""Batch 8.0: a test-only failpoint seam for the crash and recovery acceptance tests."""
import pytest

from ai4math import store as store_module
from ai4math.store import Failpoint
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Failpoint seam")
    yield instance
    instance.store.close()
    store_module.disarm()


def test_before_commit_failpoint_rolls_the_transaction_back(wb):
    store_module.arm("before_commit")
    with pytest.raises(Failpoint):
        with wb.store.transaction("fixture.write", "test"):
            wb.store.put("fixture", "row", {"id": "row"}, new=True)
    assert not wb.store.exists("fixture", "row")
    assert wb.audit()["passed"]


def test_after_commit_failpoint_leaves_the_commit_in_place(wb):
    store_module.arm("after_commit")
    with pytest.raises(Failpoint):
        with wb.store.transaction("fixture.write", "test"):
            wb.store.put("fixture", "row", {"id": "row"}, new=True)
    assert wb.store.exists("fixture", "row")
    assert wb.audit()["passed"]


def test_the_connection_still_works_after_an_after_commit_failpoint(wb):
    store_module.arm("after_commit")
    with pytest.raises(Failpoint):
        with wb.store.transaction("fixture.write", "test"):
            wb.store.put("fixture", "first", {"id": "first"}, new=True)
    with wb.store.transaction("fixture.write", "test"):
        wb.store.put("fixture", "second", {"id": "second"}, new=True)
    assert wb.store.exists("fixture", "second")
    assert wb.audit()["passed"]


def test_a_failpoint_fires_once_and_only_when_armed(wb):
    with wb.store.transaction("fixture.write", "test"):
        wb.store.put("fixture", "calm", {"id": "calm"}, new=True)
    store_module.arm("before_commit")
    with pytest.raises(Failpoint):
        with wb.store.transaction("fixture.write", "test"):
            wb.store.put("fixture", "armed", {"id": "armed"}, new=True)
    with wb.store.transaction("fixture.write", "test"):
        wb.store.put("fixture", "after", {"id": "after"}, new=True)
    assert not wb.store.exists("fixture", "armed")
    assert wb.store.exists("fixture", "after")
