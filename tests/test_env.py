"""The .env loader fills missing keys without overriding the environment."""
import os

from ai4math.env import load, parse


def test_parse_ignores_comments_and_empty_values():
    values = parse("# comment\n\nDEEPSEEK_API_KEY=abc\nEMPTY=\nQUOTED=\"x y\"\n")
    assert values == {"DEEPSEEK_API_KEY": "abc", "QUOTED": "x y"}


def test_load_does_not_override_existing(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("DEEPSEEK_API_KEY=from-file\nOTHER=value\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    monkeypatch.delenv("OTHER", raising=False)
    loaded = load(path)
    assert loaded["OTHER"] == "value"
    assert os.environ["DEEPSEEK_API_KEY"] == "from-env"
    assert os.environ["OTHER"] == "value"


def test_missing_file_is_a_noop(tmp_path):
    assert load(tmp_path / "absent.env") == {}
