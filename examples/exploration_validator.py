"""Separate finite-data validator; never imports or invokes the producer."""
import json
import sys
from pathlib import Path

contract = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
documents = [json.loads(Path(a["path"]).read_text(encoding="utf-8")) for a in contract["input_artifacts"]]
design = next(d for d in documents if "n" in d)
data = next(d for d in documents if "values" in d)
values = data["values"]
valid = (isinstance(values, list) and len(values) == design["n"]
         and all(type(x) is int and 1 <= x <= design["n"] for x in values)
         and len(set(values)) == design["n"])
candidate = contract["exploration"]["candidate"]
Path("result.json").write_text(json.dumps({
    "candidate": candidate["id"], "candidate_digest": candidate["candidate_digest"],
    "task_id": contract["task_id"], "evaluation_protocol_sha256": contract["exploration"]["evaluation_protocol"]["sha256"],
    "input_artifacts": sorted(a["id"] for a in contract["input_artifacts"]), "scope": "finite", "valid": valid,
    "metrics": {"quality": len(set(values)), "span": max(values) - min(values)},
    "checks": ["length, integer domain and distinctness"],
    "limitations": ["Synthetic finite data only; no theorem or model-research performance evaluated"]}), encoding="utf-8")
