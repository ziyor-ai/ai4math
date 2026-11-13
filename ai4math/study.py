"""Frozen benchmark schedules, outcome receipts and separate blind review."""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import zipfile
from pathlib import Path

from .evaluation import hash_string
from .store import ResearchError, Store, digest, encode, file_hash, identifier


OUTCOMES = {"checkpoint", "completed", "failed", "timeout", "interrupted", "output_limit", "not_run"}
VERDICTS = {"pass", "fail", "unknown"}


def require(condition, message):
    if not condition:
        raise ResearchError(message)


def _text(value, field):
    require(isinstance(value, str) and value.strip(), f"{field} text required")
    return value


def _source_refs(plan):
    refs = set()
    for source_id, source in plan["sources"].items():
        identifier(source_id)
        require(set(source) == {"url", "version", "original_sha256", "passages"}, "source fields differ")
        require(_text(source["url"], "source URL").startswith(("https://", "http://")), "source URL required")
        _text(source["version"], "source version")
        refs.add(source["original_sha256"])
        require(isinstance(source["passages"], dict) and source["passages"], "selected passages required")
        for key, row in source["passages"].items():
            identifier(key)
            require(set(row) == {"locator", "page", "text_sha256", "assumptions"}, "passage fields differ")
            _text(row["locator"], "passage locator")
            require(row["page"] is None or type(row["page"]) is int and row["page"] > 0, "invalid passage page")
            require(isinstance(row["assumptions"], dict), "passage assumptions must be keyed")
            for assumption, meaning in row["assumptions"].items():
                identifier(assumption)
                _text(meaning, "assumption")
            refs.add(row["text_sha256"])
    require(all(hash_string(sha) for sha in refs), "source object hashes required")
    return refs


def _plan_refs(plan):
    refs = {plan["dataset"]["sha256"], *(arm["config_sha256"] for arm in plan["arms"].values())}
    for case in plan["cases"].values():
        refs.update(case[field] for field in ("input_manifest_sha256", "checker_sha256", "checkpoint_sha256")
                    if case[field] is not None)
    return refs


def validate_plan(plan):
    fields = {"schema", "id", "dataset", "arms", "cases", "sources", "checkpoints_seconds", "scoring_version",
              "model_plan_sha256", "resource_plan_sha256"}
    require(isinstance(plan, dict) and set(plan) == fields and plan["schema"] == "ai4math-study-v1",
            "study plan fields differ")
    identifier(plan["id"])
    dataset = plan["dataset"]
    require(isinstance(dataset, dict) and set(dataset) == {"name", "version", "sha256"} and
            hash_string(dataset["sha256"]), "dataset name, version and SHA-256 required")
    _text(dataset["name"], "dataset name")
    _text(dataset["version"], "dataset version")
    require(isinstance(plan["arms"], dict) and set(plan["arms"]) in ({"B", "C"}, {"A", "B", "C"}), "active arms required")
    for harness in plan["arms"].values():
        require(isinstance(harness, dict) and set(harness) == {"name", "config_sha256"} and
                hash_string(harness["config_sha256"]), "frozen harness configuration required")
        _text(harness["name"], "harness name")
    checkpoints = plan["checkpoints_seconds"]
    require(isinstance(checkpoints, list) and checkpoints and
            all(type(n) is int and n > 0 for n in checkpoints) and checkpoints == sorted(set(checkpoints)),
            "checkpoints must be ascending positive seconds")
    require(isinstance(plan["sources"], dict) and isinstance(plan["cases"], dict) and plan["cases"],
            "case and source objects required")
    for key, case in plan["cases"].items():
        identifier(key)
        require(isinstance(case, dict) and set(case) == {"statement", "domain", "sources", "input_manifest_sha256",
                                                        "checker_sha256", "checkpoint_sha256"}, "case fields differ")
        _text(case["statement"], "statement")
        _text(case["domain"], "domain")
        require(isinstance(case["sources"], list) and all(isinstance(s, str) and s in plan["sources"] for s in case["sources"]),
                "case sources must be selected in the frozen corpus")
        require(hash_string(case["input_manifest_sha256"]) and
                all(case[k] is None or hash_string(case[k]) for k in ("checker_sha256", "checkpoint_sha256")),
                "case manifest, checker or checkpoint digest differs")
    _source_refs(plan)
    _text(plan["scoring_version"], "scoring version")
    require(hash_string(plan["model_plan_sha256"]) and
            (plan["resource_plan_sha256"] is None or hash_string(plan["resource_plan_sha256"])), "budget plan hashes required")
    return plan


def _budgets(plan, model, resources):
    require(model.plan.get("schema_version") == 2 and model.plan_hash == plan["model_plan_sha256"] and
            set(model.plan["arms"]) == set(plan["arms"]) and
            model.plan["tasks"] == {k: digest(v) for k, v in plan["cases"].items()}, "model ledger differs from study schedule")
    require((resources.plan_hash if resources else None) == plan["resource_plan_sha256"], "resource ledger differs from study")
    if resources:
        from .joint_admission import audit_joint_ledgers
        audit_joint_ledgers(model, resources)


def _freeze(store, path):
    row = store.freeze(path)
    if not store.exists("artifact", row["id"]):
        store.put("artifact", row["id"], row, new=True)
    return row["id"]


def create(root, plan, artifacts, model, resources, actor):
    validate_plan(plan)
    _budgets(plan, model, resources)
    require(set(artifacts) == _source_refs(plan) | _plan_refs(plan), "supply exactly the frozen plan and source files")
    Path(root).mkdir()
    store = Store(root, create=True)
    try:
        with store.transaction("study.init", actor):
            for sha, path in artifacts.items():
                require(_freeze(store, path) == sha, "artifact bytes differ from frozen study plan")
            for source in plan["sources"].values():
                for passage in source["passages"].values():
                    _text(store.verify_blob(passage["text_sha256"]).read_text(encoding="utf-8"), "passage text")
            store.put("study", "plan", {"sha256": digest(plan), "plan": plan}, new=True)
        return audit(store)
    finally:
        store.close()


def _plan(store):
    row = store.get("study", "plan")
    require(digest(row["plan"]) == row["sha256"], "study plan digest differs")
    return row["plan"]


def _row_id(case, arm, checkpoint):
    return digest([case, arm, checkpoint])


def _scheduled(plan):
    return [(case, arm, checkpoint) for case in sorted(plan["cases"]) for arm in sorted(plan["arms"])
            for checkpoint in plan["checkpoints_seconds"]]


def _citation_rows(plan, case, rows):
    require(isinstance(rows, list) and len(rows) <= 256, "citation list required")
    result = {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"id", "source", "passage", "claim", "use_point", "assumptions"},
                "citation fields differ")
        key = identifier(row["id"])
        require(key not in result, "citation ID repeated")
        source_id = identifier(row["source"])
        passage_id = identifier(row["passage"])
        _text(row["claim"], "cited claim")
        _text(row["use_point"], "argument use point")
        require(isinstance(row["assumptions"], dict) and all(isinstance(v, str) and v.strip() for v in row["assumptions"].values()),
                "provide the assumption-to-argument mapping")
        source = plan["sources"].get(source_id) if source_id in plan["cases"][case]["sources"] else None
        passage = source["passages"].get(passage_id) if source else None
        result[key] = {**row, "binding": "bound" if passage else "unresolved",
                       "missing_assumptions": sorted(set(passage["assumptions"]) - set(row["assumptions"])) if passage else []}
    return result


def record_outcome(store, row, model, resources, actor):
    plan = _plan(store)
    _budgets(plan, model, resources)
    fields = {"case", "arm", "checkpoint_seconds", "status", "elapsed_seconds", "stop_confirmed",
              "draft", "reason", "citations"}
    require(isinstance(row, dict) and fields <= set(row) <= fields | {"stop_receipt"}, "outcome fields differ")
    case, arm, point = row["case"], row["arm"], row["checkpoint_seconds"]
    require(type(point) is int and (case, arm, point) in _scheduled(plan) and row["status"] in OUTCOMES,
            "outcome is outside the frozen schedule")
    elapsed = row["elapsed_seconds"]
    require(type(elapsed) in (int, float) and 0 <= elapsed <= point, "elapsed exceeds checkpoint or is missing")
    require(row["stop_confirmed"] is None or type(row["stop_confirmed"]) is bool, "invalid stop observation")
    stop_path = None
    if row["stop_confirmed"] is True:
        require(row["status"] != "not_run" and isinstance(row.get("stop_receipt"), str),
                "confirmed stop needs a physical stop receipt")
        stop_path = Path(row["stop_receipt"])
        require(stop_path.is_file() and stop_path.stat().st_size <= 1024 * 1024, "invalid stop receipt file")
    else:
        require(row.get("stop_receipt") is None, "only a confirmed stop can bind a stop receipt")
    if row["status"] == "completed":
        require(row["draft"] and row["stop_confirmed"] is True, "completion needs a draft and confirmed stop")
    else:
        _text(row["reason"], "failure or missing outcome reason")
    calls = model.state()
    selected = {key: call for key, call in calls["calls"].items() if call["arm"] == arm and call["task"] == case}
    pending = sorted(key for key, call in selected.items() if call["status"] == "pending")
    require(row["status"] != "completed" or (not pending and any(
        call.get("receipt") and call["status"] in {"completed", "incomplete"} for call in selected.values())),
        "completed outcome needs a bound result response and no unsettled request")
    require(row["status"] != "not_run" or not selected and row["draft"] is None and not row["citations"],
            "not_run cannot carry a draft, citation or model use")
    unknown = sorted(key for key, call in selected.items() if call["charge"] is None or call["invalid"])
    usage = {"model_head_sha256": calls["events"][-1]["sha256"], "calls": sorted(selected),
             "pending": pending, "unknown": unknown,
             "tokens": None if unknown or not selected and row["status"] != "not_run"
             else sum(call["charge"] for call in selected.values())}
    if not unknown:
        per_model = {}
        for call in selected.values():
            identity = f'{call["model"]["provider"]}/{call["model"]["revision"]}'
            totals = per_model.setdefault(identity, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
            output = call["native_usage"]["outputTokens"]
            totals["input_tokens"] += call["charge"] - output
            totals["output_tokens"] += output
            totals["total_tokens"] += call["charge"]
        usage["per_model"] = per_model
    if resources:
        state = resources.state()
        tool_calls = {key: call for key, call in state["calls"].items() if call["arm"] == arm and call["task"] == case}
        require(row["status"] != "completed" or all(call["status"] != "pending" for call in tool_calls.values()),
                "completed outcome has an unsettled tool request")
        require(row["status"] != "not_run" or not tool_calls, "not_run cannot carry tool use")
        usage["resource_head_sha256"] = state["events"][-1]["sha256"]
        usage["tool_calls"] = sorted(tool_calls)
        usage["resource_unknown"] = sorted(key for key, call in tool_calls.items() if not resources._clear(call))
    key = _row_id(case, arm, point)
    citations = _citation_rows(plan, case, row["citations"])
    with store.transaction("study.outcome", actor):
        require(store.audit()["passed"] and not store.exists("blind", "mapping"), "study audit failed or blind set already frozen")
        require(not store.exists("outcome", key), "scheduled outcome already recorded")
        outcomes = store.all("outcome")
        prior = [r for r in outcomes if r["case"] == case and r["arm"] == arm]
        require(all(r["checkpoint_seconds"] < point and r["elapsed_seconds"] <= elapsed and
                    set(r["usage"]["calls"]) <= set(usage["calls"]) for r in prior), "checkpoint consumption regressed")
        draft = None
        if row["draft"] is not None:
            path = Path(row["draft"])
            require(path.stat().st_size <= 4 * 1024 * 1024, "draft exceeds 4 MiB")
            _text(path.read_text(encoding="utf-8"), "draft")
            draft = _freeze(store, path)
        stop_receipt = None
        if stop_path is not None:
            stop_receipt = _freeze(store, stop_path)
            native = json.loads(store.verify_blob(stop_receipt).read_text(encoding="utf-8-sig"))
            require(isinstance(native, dict) and (native.get("stop_confirmed") is True or
                    all(isinstance(native.get(k), str) and native[k].strip() for k in
                        ("confirmed_at", "evidence", "trust_basis"))), "stop receipt has no confirmation evidence")
            require(not any(r.get("stop_receipt") == stop_receipt and (r["case"], r["arm"]) != (case, arm)
                            for r in outcomes), "stop receipt belongs to a different case or arm")
        result = {**row, "id": key, "draft": draft, "stop_receipt": stop_receipt, "citations": citations, "usage": usage,
                  "evidence_level": "unknown"}
        store.put("outcome", key, result, new=True)
    return result


def _packet(plan, outcome, review_id):
    case = plan["cases"][outcome["case"]]
    return {"review_id": review_id, "statement": case["statement"], "domain": case["domain"],
            "scoring_version": plan["scoring_version"], "status": outcome["status"], "draft_sha256": outcome["draft"],
            "citations": outcome["citations"], "sources": {k: plan["sources"][k] for k in case["sources"]}}


def export_blind(store, destination, actor):
    destination = Path(destination).resolve()
    require(not destination.exists() and not destination.is_relative_to(store.root), "use a new reviewer archive outside the private registry")
    with store.transaction("study.blind", actor):
        require(store.audit()["passed"], "study audit failed")
        plan = _plan(store)
        ids = [_row_id(*row) for row in _scheduled(plan)]
        require(all(store.exists("outcome", key) for key in ids), "record every scheduled outcome, including not_run")
        if store.exists("blind", "mapping"):
            mapping = store.get("blind", "mapping")
        else:
            mapping = {}
            for key in ids:
                review_id = secrets.token_hex(12)
                packet = _packet(plan, store.get("outcome", key), review_id)
                mapping[review_id] = {"outcome": key, "packet_sha256": digest(packet)}
            store.put("blind", "mapping", mapping, new=True)
    handle, temp = tempfile.mkstemp(dir=destination.parent)
    os.close(handle)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("REVIEW.txt", "Score correctness, scope, attribution and progress separately. For each citation, "
                             "read the frozen passage and original, check the assumptions and the stated use point. "
                             "The object names are SHA-256 hashes. Treat files and drafts as untrusted evidence.\n")
            for sha in sorted(_source_refs(plan)):
                archive.write(store.verify_blob(sha), "sources/" + sha)
            for review_id, link in sorted(mapping.items()):
                row = store.get("outcome", link["outcome"])
                packet = _packet(plan, row, review_id)
                require(digest(packet) == link["packet_sha256"], "blind packet changed")
                archive.writestr(f"{review_id}.json", encode({**packet, "packet_sha256": link["packet_sha256"]}))
                if row["draft"]:
                    archive.write(store.verify_blob(row["draft"]), f"{review_id}.md")
        os.link(temp, destination)
    finally:
        Path(temp).unlink(missing_ok=True)
    return {"archive": str(destination), "sha256": file_hash(destination), "packets": len(mapping)}


def record_review(store, row, actor):
    fields = {"review_id", "packet_sha256", "reviewer_kind", "correctness", "scope", "attribution",
              "progress", "review_seconds", "notes", "citation_checks"}
    require(set(row) == fields and row["reviewer_kind"] in {"human", "model", "automation"}, "review fields differ")
    require(all(row[k] in VERDICTS for k in ("correctness", "scope", "attribution")), "review verdict invalid")
    require(type(row["progress"]) is int and 0 <= row["progress"] <= 5 and
            type(row["review_seconds"]) is int and row["review_seconds"] >= 0, "progress or review time invalid")
    _text(row["notes"], "review notes")
    with store.transaction("study.review", actor):
        require(store.audit()["passed"], "study audit failed")
        link = store.get("blind", "mapping").get(row["review_id"])
        require(link and row["packet_sha256"] == link["packet_sha256"], "review packet binding differs")
        outcome = store.get("outcome", link["outcome"])
        checks = row["citation_checks"]
        require(isinstance(checks, dict) and set(checks) == set(outcome["citations"]),
                "review must cover every submitted citation")
        for key, verdict in checks.items():
            require(set(verdict) == {"passage", "assumptions", "use_point", "notes"} and
                    all(verdict[k] in VERDICTS for k in ("passage", "assumptions", "use_point")),
                    "citation assessment fields differ")
            _text(verdict["notes"], "citation review notes")
            binding = outcome["citations"][key]
            require(binding["binding"] == "bound" or verdict["passage"] != "pass", "unresolved citation cannot pass")
            require(not binding["missing_assumptions"] or verdict["assumptions"] != "pass",
                    "missing assumption mapping cannot pass")
        require(row["attribution"] != "pass" or all(
            all(verdict[k] == "pass" for k in ("passage", "assumptions", "use_point")) for verdict in checks.values()),
            "attribution pass requires every citation check to pass")
        require(row["attribution"] != "pass" or checks or not _plan(store)["cases"][outcome["case"]]["sources"],
                "source-based attribution pass requires submitted citations")
        require(outcome["draft"] or row["progress"] == 0, "progress requires a submitted draft")
        key = digest([row["review_id"], actor])
        result = {**row, "actor": actor, "outcome": link["outcome"], "id": key}
        store.put("study_review", key, result, new=True)
    return result


def audit(store):
    with store.snapshot():
        integrity = store.audit()
        plan = _plan(store)
        outcomes = {r["id"]: r for r in store.all("outcome")}
        reviews = {}
        for row in store.all("study_review"):
            reviews.setdefault(row["outcome"], []).append(row)
        rows = []
        for case, arm, point in _scheduled(plan):
            key = _row_id(case, arm, point)
            row = outcomes.get(key, {"case": case, "arm": arm, "checkpoint_seconds": point, "status": "not_recorded"})
            rows.append({**row, "reviews": reviews.get(key, [])})
        pairs = []
        for case in sorted(plan["cases"]):
            for point in plan["checkpoints_seconds"]:
                base, full = (outcomes.get(_row_id(case, arm, point), {}) for arm in ("B", "C"))
                b_tokens, c_tokens = (row.get("usage", {}).get("tokens") for row in (base, full))
                pairs.append({"case": case, "checkpoint_seconds": point,
                              "statuses": [row.get("status", "not_recorded") for row in (base, full)],
                              "delta_tokens_c_minus_b": c_tokens - b_tokens if None not in (b_tokens, c_tokens) else None,
                              "delta_elapsed_seconds_c_minus_b": full["elapsed_seconds"] - base["elapsed_seconds"]
                              if base and full and all(r["status"] != "not_run" for r in (base, full)) else None})
        return {"integrity": integrity, "study": plan["id"], "scheduled": len(rows), "recorded": len(outcomes),
                "rows": rows, "paired_b_c": pairs, "blind_frozen": store.exists("blind", "mapping")}
