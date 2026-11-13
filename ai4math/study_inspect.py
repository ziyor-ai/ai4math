"""Export frozen study outcomes through the optional Inspect AI log API."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .store import ResearchError, digest, now
from .study import _plan, audit, require


def export(store, destination, at):
    try:
        from inspect_ai.log import EvalConfig, EvalDataset, EvalLog, EvalSample, EvalSpec, read_eval_log, write_eval_log
        from inspect_ai.model import ModelOutput, ModelUsage
    except ImportError as exc:
        raise ResearchError("Inspect AI log API is required for inspect-export") from exc
    destination = Path(destination).resolve()
    require(destination.suffix.lower() == ".json" and not destination.exists(), "choose a new Inspect JSON log")
    with store.snapshot():
        report = audit(store)
        require(report["integrity"]["passed"], "study audit failed")
        plan = _plan(store)
        require(at in plan["checkpoints_seconds"], "unknown checkpoint")
        samples = []
        for row in report["rows"]:
            if row["checkpoint_seconds"] != at:
                continue
            require(row["status"] != "not_recorded", "record every outcome at this checkpoint before exporting")
            case = plan["cases"][row["case"]]
            sample_id = f'{row["case"]}:{row["arm"]}:{at}'
            source_usage = row.get("usage", {}).get("per_model", {})
            draft = store.verify_blob(row["draft"]).read_text(encoding="utf-8") if row.get("draft") else ""
            sealed = {key: value for key, value in row.items() if key != "reviews"}
            samples.append(EvalSample(
                id=sample_id, epoch=1, input=case["statement"] + "\n\n" + case["domain"], target="",
                output=ModelOutput.from_content("archived-study", draft, stop_reason="unknown") if draft else ModelOutput(),
                metadata={"case": row["case"], "arm": row["arm"], "status": row["status"],
                          "outcome_sha256": digest(sealed), "checkpoint_seconds": at},
                store={"sealed_outcome": sealed, "reviews": row["reviews"]},
                model_usage={name: ModelUsage(**usage) for name, usage in source_usage.items()},
                total_time=row.get("elapsed_seconds"),
            ))
        log = EvalLog(status="success", eval=EvalSpec(
            created=now(), task=plan["id"], task_version=plan["scoring_version"], model="archived-study",
            dataset=EvalDataset(name=plan["dataset"]["name"], samples=len(samples), sample_ids=[s.id for s in samples]),
            config=EvalConfig(), metadata={"source": "sealed_ai4math_study", "executed_by_inspect": False,
                                           "checkpoint_seconds": at, "study_event_tail": report["integrity"]["tail_hash"]},
        ), samples=samples)
    handle, temp = tempfile.mkstemp(suffix=".json", dir=destination.parent)
    os.close(handle)
    try:
        write_eval_log(log, temp, format="json")
        reread = read_eval_log(temp)
        require(reread.eval.metadata == log.eval.metadata and
                [(s.id, s.metadata, s.store) for s in reread.samples or []] ==
                [(s.id, s.metadata, s.store) for s in samples], "Inspect round trip changed sealed outcomes or provenance")
        os.link(temp, destination)
    finally:
        Path(temp).unlink(missing_ok=True)
    return {"log": str(destination), "samples": len(samples), "checkpoint_seconds": at}
