"""Generate a tiny witness; the checker does not import this program."""
import json
from pathlib import Path

Path("witness.json").write_text(json.dumps({"n": 2, "labels": [1, 2], "minimum": 2}), encoding="utf-8")
print("Candidate witness written. No mathematical status assigned by producer.")
