"""Tiny synthetic candidate generator for engineering acceptance, not research."""
import json
import sys
from pathlib import Path

contract = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
design = next(json.loads(Path(a["path"]).read_text(encoding="utf-8")) for a in contract["input_artifacts"]
              if Path(a["path"]).read_text(encoding="utf-8").lstrip().startswith('{"n"'))
values = list(range(1, design["n"] + 1))
if contract["exploration"]["candidate"]["parents"]:
    values.reverse()
Path("result.json").write_text(json.dumps({"values": values, "scope": "finite", "note": "Synthetic engineering fixture"}), encoding="utf-8")
