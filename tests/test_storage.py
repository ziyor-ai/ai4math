"""Storage report buckets bytes and makes the bookkeeping share explicit."""
import json
import sys

import pytest

from ai4math.cli import main
from ai4math.storage import panel, survey
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Storage")
    yield instance
    instance.store.close()


def test_storage_reports_groups_and_share(wb):
    goal = wb.store.root / "notes.md"
    goal.write_text("prose", encoding="utf-8")
    payload = wb.store.root / "blob.jsonl"
    payload.write_text("{}\n" * 100, encoding="utf-8")
    state = survey(wb.store.root)
    assert state["files"] >= 2
    assert state["by_group"]["prose"] > 0
    assert state["by_group"]["bookkeeping"] > 0
    assert 0 < state["bookkeeping_share"] <= 1
    assert "bookkeeping+raw streams" in panel(state)


def test_storage_cli(wb, capsys):
    capsys.readouterr()
    assert main(["--workspace", str(wb.store.root), "storage", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["bytes"] > 0
    assert main(["--workspace", str(wb.store.root), "storage"]) == 0
    assert "workspace:" in capsys.readouterr().out
