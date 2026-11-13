"""Researcher-facing reports derived from the registry and append-only handoffs."""

from collections import Counter
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import sys
from urllib.parse import quote

from ._atomic import atomic_write, file_lock, publish_new
from .research_return import deduction_first
from .store import ResearchError, Store, digest, encode, identifier, now


SUMMARY = "research-summary.md"
HISTORY = "research-history.md"
TRANSITIONS = {"route.checkpoint", "strategy.decide", "task.finish_external", "task.confirm_stop",
               "claim.accept", "claim.revoke", "task.codex_hold", "task.codex_stop", "codex.dispatch_hold",
               "goal.set", "node.plan", "task.recover"}
FIELDS = (("progress", "数学进展"), ("scope", "适用范围"),
          ("gaps", "未解缺口"), ("next_step", "下一步"))
KINDS = {"review": "验收", "pause": "暂停交接", "historical": "历史补录"}
ID_FIELDS = ("tasks", "claims", "artifacts", "passages")


def _text(value):
    # Treat recorded prose as text, including text returned by a worker.
    return re.sub(r"([\\`*_[\]<>|&$])", r"\\\1", " ".join(value.split()))


def _link(parent, path, label):
    try:
        relative = Path(os.path.relpath(path, parent)).as_posix()
        target = quote(relative, safe="/..")
    except ValueError:  # Different Windows drives.
        target = path.as_uri()
    return f"[{_text(label)}]({target})"


def _owned(store, relative):
    path = store.inside(relative)
    if path != store.root / relative:
        raise ResearchError(f"research summary path is redirected: {relative}")
    return path


def _label(artifact, key):
    source = artifact.get("source", "")
    return PureWindowsPath(source).name or key


def _blob_link(store, parent, key, label):
    path = store.blob(key)
    return _link(parent, path, label) if path.is_file() else f"{_text(label)}（归档文件缺失，`{key}`）"


def _artifact_link(store, parent, key):
    return _blob_link(store, parent, key, _label(store.get("artifact", key), key))


def _passages(w, keys, library):
    if not keys:
        return []
    from .research import allowed_source

    if library is not None:
        allowed_source(w, library)
    store = Store(library, read_only=True) if library is not None else w.store
    try:
        with store.snapshot():
            results = []
            for key in keys:
                passage = store.get("passage" if library is not None else "source_passage", key)
                content = passage["content"]
                document = store.get("document" if library is not None else "source_document", content["document"])
                source = store.get("source" if library is not None else "literature_source", content["source_id"])
                raw = document["content"]["raw_artifact"]
                if (content["source_id"] != document["content"]["source_id"]
                        or any(row["artifact"] != row["id"] or digest(row["content"]) != row["id"]
                               for row in (passage, document, source))):
                    raise ResearchError("research summary passage provenance differs from archive")
                if source["content"]["kind"] == "provider_metadata" and raw == source["content"].get("raw_artifact"):
                    raise ResearchError("metadata response is not the archived original")
                for sha in (passage["artifact"], raw):
                    store.verify_blob(sha)
                results.append({"id": key, "library": str(store.root) if library is not None else None,
                                "artifact": passage["artifact"], "raw": raw, "locator": content["locator"],
                                "title": source["content"]["title"], "version": source["content"]["version"]})
            return results
    finally:
        if store is not w.store:
            store.close()


def _record(w, payload, library, actor, event):
    allowed = {"id", "kind", "title", *(name for name, _ in FIELDS), *ID_FIELDS}
    if not isinstance(payload, dict) or payload.keys() - allowed:
        raise ResearchError("invalid research summary fields")
    if payload.get("kind") not in KINDS:
        raise ResearchError("research summary kind must be review, pause or historical")
    for field in ("title", *(name for name, _ in FIELDS)):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise ResearchError(f"research summary needs nonempty {field}")
    ids = {}
    for field in ID_FIELDS:
        values = payload.get(field, [])
        if not isinstance(values, list) or any(not isinstance(key, str) for key in values):
            raise ResearchError(f"research summary {field} must be a list of IDs")
        ids[field] = list(dict.fromkeys(values))
    for key in ids["tasks"]:
        w.store.get("task", key)
    for key in ids["artifacts"]:
        w.store.verify_blob(key)
    claims = {}
    for key in ids["claims"]:
        row = w.store.get("claim", key)
        claims[key] = {field: row.get(field) for field in ("status", "evidence_level", "validation")}
    return {**payload, **ids, "id": identifier(payload.get("id")), "actor": actor, "claim_snapshot": claims,
            "recorded_at": now(), "registry_event": event,
            "sources": _passages(w, ids["passages"], library)}


def _source_links(store, parent, source):
    root = Path(source["library"]) if source["library"] else store.root
    links = []
    for key, label in ((source["artifact"], "段落"), (source["raw"], "原件")):
        path = root / "objects" / key[:2] / key
        links.append(_link(parent, path, label) if path.resolve() == path and path.is_file()
                     else f"{label}缺失或重定向（`{key}`）")
    return " · ".join(links)


def _return(store, task):
    seal = task.get("seal_receipt") or {}
    for key in seal.get("output_artifacts", task.get("output_artifacts", [])):
        if _label(store.get("artifact", key), key) != "return.json":
            continue
        try:
            path = store.blob(key)
            if path.stat().st_size <= 1024 * 1024:
                record = json.loads(store.verify_blob(key).read_text(encoding="utf-8-sig"))
                if isinstance(record, dict):
                    return record
        except (OSError, ValueError):
            return {}
    return {}


def _task(w, parent, task, *, detail=False):
    store = w.store
    stop = task.get("stop_state")
    stage = task.get("external_stage")
    state = " · ".join(_text(value) for value in (task["status"], stage, f"停机 {stop}" if stop else None) if value)
    links = []
    seal = task.get("seal_receipt") or {}
    for key, label in ((task.get("result_artifact"), "验收结果"), (seal.get("artifact_id"), "封存清单")):
        if key:
            links.append(_blob_link(store, parent, key, label))
    if stop == "confirmed" and store.exists("external_stop", task["id"]):
        receipt = store.get("external_stop", task["id"])
        links.append(f"已确认停机 {_text(receipt['confirmed_at'])}")
    outputs = task.get("output_artifacts") or seal.get("output_artifacts", [])
    if detail:
        links.extend(_artifact_link(store, parent, key) for key in outputs)
    else:
        if task.get("contract_artifact"):
            links.append(_blob_link(store, parent, task["contract_artifact"], "任务契约"))
        key = next((key for key in outputs if _label(store.get("artifact", key), key) == "return.json"), None)
        if key:
            links.append(_blob_link(store, parent, key, "交回记录"))
    hold = task.get("codex_control", {})
    if hold.get("held") and task["status"] == "running":
        state += " · 已请求暂停"
    row = f"- `{task['id']}` · `{task['node']}` · {state}"
    if links:
        row += " · " + " · ".join(links)
    if not detail:
        return [row]
    record = _return(store, task)
    rows = [row]
    summary = record.get("summary") or task.get("external_summary") or task.get("failure_reason")
    for label, value in (("交回", summary), ("覆盖", record.get("scope")), ("下一步", record.get("next_step"))):
        if isinstance(value, str) and value.strip():
            rows.append(f"  - **{label}** {_text(value)}")
    gaps = record.get("open_obligations")
    if isinstance(gaps, list) and gaps and all(isinstance(item, str) for item in gaps):
        rows.append("  - **未解缺口** " + "；".join(map(_text, gaps)))
    if deduction_first(task) and seal:
        for citation in record.get("source_checks", []):
            key = citation["passage_id"]
            source = _text(citation["theorem"]) + " · " + _text(citation["use_point"])
            if key:
                locator = record["source_passages"][key]["locator"]
                source += f" · {_text(locator)} · " + _blob_link(store, parent, key, "冻结原文段落")
            else:
                source += " · 原文待核"
            rows.append(f"  - **引用** {source}")
    return rows


def _claim(w, parent, key, snapshot=None):
    store = w.store
    claim = store.get("claim", key)
    node = store.get("node", claim["node"])
    level = claim["evidence_level"]
    scope = claim.get("evidence_domain") or node["domain"]
    if not isinstance(scope, str):
        scope = encode(scope)
    rows = [f"- `{key}` · {claim['status']} · `{level}` · {_text(scope)}"]
    if snapshot and snapshot["status"] != claim["status"]:
        rows.append(f"  - 轮次登记时为 `{snapshot['status']}` · `{snapshot['evidence_level']}`。")
    links = [_artifact_link(store, parent, artifact) for artifact in claim["artifact_ids"]]
    validation_id = claim.get("validation") or (snapshot or {}).get("validation")
    if validation_id:
        validation = store.get("validation", validation_id)
        links += [_blob_link(store, parent, artifact, "验收回执") for artifact in validation["artifact_ids"]]
        rows.append(f"  - **验证依据** {_text(validation['trust_basis'])}")
    if claim.get("review"):
        review = store.get("review", claim["review"])
        rows.append(f"  - **评审** `{review['id']}` · {_text(review['reviewer_kind'])} · {_text(review['verdict'])}")
    if links:
        rows.append("  - " + " · ".join(links))
    return rows


def _round(w, parent, record, tasks):
    title = _text(record["title"])
    rows = [f"### {title}", "", f"`{record['id']}` · {KINDS[record['kind']]} · "
            f"登记 {_text(record['recorded_at'])} · 事件 #{record['registry_event']}", ""]
    rows += [f"- **{label}** {_text(record[key])}" for key, label in FIELDS]
    for key in record["tasks"]:
        rows += _task(w, parent, tasks[key], detail=False)
    for key in record["claims"]:
        rows += _claim(w, parent, key, record["claim_snapshot"].get(key))
    if record["artifacts"]:
        rows.append("- **相关文件** " + " · ".join(_artifact_link(w.store, parent, key) for key in record["artifacts"]))
    for source in record["sources"]:
        rows.append(f"- **参考资料** {_text(source['title'])} · {_text(source['version'])} · "
                    f"{_text(source['locator'])} · {_source_links(w.store, parent, source)}")
    rows.append("")
    return rows


def _checkpoint_questions(store):
    """Recover historical questions from the original checkpoint event, when available."""
    questions = {}
    for event in store.db.execute("SELECT changes FROM events WHERE operation='route.checkpoint' ORDER BY seq"):
        for change in json.loads(event["changes"]):
            if change["kind"] == "route":
                route = change["value"]
                questions[(route["id"], len(route["checkpoints"]) - 1)] = route["next_question"]
    return questions


def _render(w, parent, records, event):
    store = w.store
    project = store.get("project", "main")
    goal = project.get("goal")
    tasks = sorted(store.all("task"), key=lambda row: row.get("finished_at") or row.get("created_at", ""), reverse=True)
    task_index = {row["id"]: row for row in tasks}
    terminal = [row for row in tasks if row["status"] not in {"pending", "running"}]
    routes = store.all("route")
    questions = _checkpoint_questions(store)
    checkpoints = sorted(((item, route, item.get("next_question") or questions.get((route["id"], index)))
                          for route in routes for index, item in enumerate(route["checkpoints"])),
                         key=lambda row: row[0]["timestamp"], reverse=True)
    claims = sorted((row for row in store.all("claim") if row["status"] == "accepted"),
                    key=lambda row: row.get("accepted_at") or row.get("created_at", ""), reverse=True)
    main = [f"# 研究总汇总 · {_text(project['title'])}", "",
            f"注册事件 #{event} · [历史轮次与回执]({HISTORY})", "", "## 锁定目标", ""]
    if goal:
        node = store.get("node", goal)
        statuses = Counter(w.node_status(key) for key in w.cone())
        main += [f"`{goal}` · `{w.evidence_status(goal)}`", "", _text(node["statement"]), "",
                 f"**适用域** {_text(node['domain'])}", "", f"陈述 SHA-256 `{node['statement_digest']}`", "",
                 f"目标依赖范围内有 {statuses['open']} 项开放、{statuses['proved']} 项已证明、{statuses['refuted']} 项已证伪。"]
    else:
        main.append("尚未登记目标。")
    main += ["", "## 最近一次协调汇总", ""]
    if records:
        main += _round(w, parent, records[0], task_index)
    else:
        main += ["尚无验收或交接叙述。以下显示注册表中的事实。", ""]
    main += ["## 当前运行与待停机", ""]
    if store.exists("codex_dispatch", "main"):
        dispatch = store.get("codex_dispatch", "main")
        if dispatch["held"]:
            main += [f"派工已暂停 · {_text(dispatch['reason'])}", ""]
    active = [row for row in tasks if row["status"] == "running" or row.get("stop_state") == "requested"]
    for row in active:
        main += _task(w, parent, row)
    if not active:
        main.append("当前无运行中或等待停机确认的已登记任务。")
    main += ["", "## 最近登记的已接纳结论", ""]
    for claim in claims[:5]:
        node = store.get("node", claim["node"])
        main.append(f"- `{node['id']}` · `{claim['evidence_level']}` · {_text(node['statement'])}")
    if not claims:
        main.append("暂无已接纳结论。")
    main += ["", "## 最近路线检查点与后续问题", ""]
    recent_routes = {}
    for item, route, _ in checkpoints:
        recent_routes.setdefault(route["id"], (item, route))
    for item, route in list(recent_routes.values())[:6]:
        main += [f"- `{route['id']}` · {route['status']} · {_text(item['finding'])}",
                 f"  - **该路线当前下一步** {_text(route['next_question'])}"]
    if not checkpoints:
        main.append("暂无路线检查点。")
    main += ["", "## 最近交回与验收", ""]
    for row in terminal[:4]:
        main += _task(w, parent, row, detail=True)
    if not terminal:
        main.append("暂无终态任务。")
    main += ["", "运行状态与协调叙述供追踪。数学证据以声明的注册状态和独立验证为准。", ""]

    history = ["# 研究历史", "", f"[返回研究总汇总]({SUMMARY}) · 注册事件 #{event}", "",
               "## 轮次汇总与暂停交接", ""]
    for record in records:
        history += _round(w, parent, record, task_index)
        history += [_link(parent, parent / "rounds" / f"{record['id']}.json", "原始登记"), ""]
    history += ["## 路线检查点", ""]
    for item, route, question in checkpoints:
        history.append(f"- {_text(item['timestamp'])} · `{route['id']}` · {_text(item['finding'])}")
        if question:
            history.append("  - **当时的下一步** " + _text(question))
        if item["claims"]:
            history.append("  - **关联主张** " + "、".join(f"`{key}`" for key in item["claims"]))
    history += ["", "## 已结束任务与当前验收记录", ""]
    for task in terminal:
        history += _task(w, parent, task, detail=True)
    history += ["", "## 当前已接纳结论及验收回执", ""]
    for claim in claims:
        history += _claim(w, parent, claim["id"])
    return "\n".join(main), "\n".join(history) + "\n"


def refresh(w, *, payload=None, library=None, actor="human"):
    """Serialize publication, preserving all registered handoffs on retry."""
    store = w.store
    parent = _owned(store, "reports")
    parent.mkdir(exist_ok=True)
    with file_lock(_owned(store, "reports/.ai4math-summary.lock")):
        summary = _owned(store, f"reports/{SUMMARY}")
        history = _owned(store, f"reports/{HISTORY}")
        rounds = _owned(store, "reports/rounds")
        with store.snapshot():
            event = store.db.execute("SELECT MAX(seq) FROM events").fetchone()[0]
            if payload is not None:
                record = _record(w, payload, library, actor, event)
                rounds.mkdir(exist_ok=True)
                path = _owned(store, f"reports/rounds/{record['id']}.json")
                if path.exists():
                    prior = json.loads(path.read_text(encoding="utf-8"))
                    derived = {"recorded_at", "registry_event", "claim_snapshot"}
                    compare = {key: value for key, value in record.items() if key not in derived}
                    if any(prior.get(key) != value for key, value in compare.items()):
                        raise ResearchError("research summary ID already exists with different content")
                else:
                    publish_new(path, (encode(record) + "\n").encode("utf-8"))
            records = [json.loads(_owned(store, path.relative_to(store.root)).read_text(encoding="utf-8"))
                       for path in rounds.glob("*.json")]
            records.sort(key=lambda row: (row["recorded_at"], row["id"]), reverse=True)
            main, archive = _render(w, parent, records, event)
        atomic_write(history, archive.encode("utf-8"))
        atomic_write(summary, main.encode("utf-8"))
    return {"path": str(summary), "history": str(history), "rounds": len(records), "registry_event": event}


def refresh_after_transition(w):
    """Keep an already committed research operation successful if reporting fails."""
    try:
        refresh(w)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(f"research summary refresh failed: {exc}; run the summary command to retry", file=sys.stderr)
