"""Minimal .env loader: the repository stays dependency-free.

Secrets live in a gitignored .env next to the package.  Loading is explicit so
library callers never read the file by accident; the CLI loads it once at start.
"""
import os
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / ".env"


def parse(text):
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value:
            values[key] = value
    return values


def load(path=None):
    """Load KEY=VALUE pairs into os.environ without overriding existing values."""
    target = Path(path or os.environ.get("AI4MATH_ENV_FILE") or DEFAULT)
    if not target.is_file():
        return {}
    values = parse(target.read_text(encoding="utf-8"))
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values
