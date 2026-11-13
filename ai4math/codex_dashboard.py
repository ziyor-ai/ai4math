"""Atomically render a local, read-only projection of research progress."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from urllib.parse import quote

from ._atomic import atomic_write, file_lock
from .literature import status as literature_status
from .store import ResearchError, identifier
from .strategy import due, state
from .workbench import Workbench

REPORT = Path("reports/research-progress.html")
REPORT_VERSION = 1
TAIL_BYTES = 128 * 1024
TAIL_LINES = 120
HEARTBEAT_BYTES = 16 * 1024
TERMINAL_DISPLAY = 12
PENDING_DISPLAY = 12
RUNNING_DISPLAY = 20
STAGES = {
    "pending": "等待启动", "running": "运行中", "literature": "独立文献调研",
    "awaiting_coordinator_review": "等待主会话审核", "exploration": "数学首检",
    "seal": "准备封存", "stopping": "等待进程停止", "ready_for_intake": "待独立验收",
    "sealed": "已封存", "held": "已请求暂停", "completed": "已回收", "failed": "失败",
    "unknown_timeout": "超时", "interrupted": "已中断", "output_limit": "输出超限",
    "monitor_stale": "心跳待核查", "check_progress": "进度待核查",
}
EVENT_LABELS = {
    "progress": "结构化进度", "message": "会话消息", "tool_started": "开始工具调用",
    "tool_completed": "完成工具调用", "turn_started": "开始执行轮", "turn_completed": "结束执行轮",
    "steer_accepted": "纠偏已送达", "steer_ack": "纠偏已确认", "failure": "执行异常",
    "soft_alert": "进度提醒", "literature_ready": "文献回执已提交", "thread_started": "会话已启动",
    "exploration_released": "首检已放行", "handoff_prepared": "已提交交接",
    "capture_closed": "工具记录已闭合", "sealed": "产物已封存", "control_rejected": "控制请求未接受",
}
ACTION = {
    "awaiting_coordinator_review": "审核独立文献回执并登记放行。",
    "ready_for_intake": "独立复核封存产物、作用域和证据，再登记收轮。",
    "stopping": "核对监督器与物理停机回执。",
    "monitor_stale": "查看本机监督器与捕获回执。",
    "check_progress": "查看最近检查点，必要时请求结构化进度。",
}


def _text(value, size=800):
    if not isinstance(value, str):
        return ""
    return escape(value[:size] + ("…" if len(value) > size else ""))


def _time(value):
    if not isinstance(value, str) or len(value) > 50:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else None


def _time_label(value):
    stamp = _time(value)
    return stamp.strftime("%Y-%m-%d %H:%M:%S UTC") if stamp else "—"


def _elapsed(now, value):
    stamp = _time(value)
    return max(0, int((now - stamp).total_seconds())) if stamp else None


def _safe_file(path, root):
    # Read only the literal owned path, even if an untrusted worker left a link.
    try:
        return path.is_relative_to(root) and path.resolve() == path and path.is_file()
    except (OSError, RuntimeError):
        return False


def _event_tail(path, task_id, root):
    if not _safe_file(path, root):
        return []
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - TAIL_BYTES)
            stream.seek(start)
            raw = stream.read(size - start)
    except OSError:
        return []
    if start:
        raw = raw.partition(b"\n")[2]
    # Exclude the final incomplete line during concurrent appends.
    lines = raw.split(b"\n")[:-1]
    events = {}
    for line in lines[-TAIL_LINES:]:
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if (isinstance(row, dict) and row.get("task") == task_id
                and type(row.get("seq")) is int and row["seq"] > 0
                and isinstance(row.get("kind"), str) and _time(row.get("at"))):
            events[row["seq"]] = row
    return [events[i] for i in sorted(events)]


def _heartbeat(path, task_id, root):
    if not _safe_file(path, root):
        return {}
    try:
        with path.open("rb") as stream:
            raw = stream.read(HEARTBEAT_BYTES + 1)
        if len(raw) <= HEARTBEAT_BYTES:
            value = json.loads(raw)
            if isinstance(value, dict) and value.get("task") == task_id:
                return value
    except (OSError, ValueError, RecursionError):
        pass
    return {}


def _snapshot(w):
    with w.store.snapshot():
        project = w.store.get("project", "main")
        claims = w.store.all("claim")
        nodes = {key: w.node_status(key) for key in w.cone()}
        strategy = state(w) or {}
        literature = literature_status(w, strategy.get("decision"), strategy.get("focus_routes", [])) or {}
        routes = [r for r in w.store.all("route") if r["node"] in nodes]
        tasks = w.store.all("task")
        held = w.store.get("codex_dispatch", "main").get("held", False) if w.store.exists("codex_dispatch", "main") else False
        tail = w.store.db.execute("SELECT seq FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return {"project": project, "nodes": nodes, "claims": claims, "routes": routes, "tasks": tasks,
                "strategy": strategy, "literature": literature, "held": held, "registry_seq": tail["seq"] if tail else 0}


def _data(row):
    return row["data"] if isinstance(row.get("data"), dict) else {}


def _latest(events, kind):
    return next((e for e in reversed(events) if e["kind"] == kind), None)


def _correction_id(row):
    data = _data(row)
    return data.get("request_id") or data.get("correction_id") or data.get("id") or data.get("ack")


def _link(root, output, task_id, value, *, label=None):
    if isinstance(value, dict):
        value = value.get("path") or value.get("artifact_id")
    if not isinstance(value, str) or not value or len(value) > 500 or any(ord(c) < 32 for c in value):
        return ""
    if re.fullmatch(r"[0-9a-f]{64}", value):
        rel = Path("objects", value[:2], value)
    else:
        slash_path = value.replace("\\", "/")
        parts = slash_path.split("/")
        if PureWindowsPath(value).drive or any(p in {"", ".", ".."} or ":" in p for p in parts):
            return ""
        if parts[0] == "return" and len(parts) > 1:
            rel = Path("tasks", task_id, *parts)
        elif parts[:3] == ["tasks", task_id, "return"] and len(parts) > 3:
            rel = Path(*parts)
        else:
            return ""
    target = root / rel
    if not _safe_file(target, root):
        return ""
    href = quote(PurePosixPath(os.path.relpath(target, output.parent).replace("\\", "/")).as_posix())
    return f'<a href="{escape(href, quote=True)}">{_text(label or rel.as_posix(), 500)}</a>'


def _task_state(task, beat, events, now):
    status, stage = task["status"], task.get("external_stage")
    if status != "running":
        return status
    if stage == "sealed":
        return "ready_for_intake" if task.get("stop_state") == "confirmed" else "stopping"
    if stage == "awaiting_coordinator_review":
        return stage
    control = task.get("codex_control") or {}
    if control.get("held"):
        return "held"
    if task.get("codex_workflow") or beat or events:
        age = _elapsed(now, beat.get("at") or task.get("started_at"))
        progress = _elapsed(now, beat.get("last_progress_at") or task.get("started_at"))
        if age is not None and age > 20:
            return "monitor_stale"
        if progress is not None and progress >= 900:
            return "check_progress"
    return stage or status


def _task_card(root, output, task, now):
    tid = identifier(task["id"])
    live = root / "tasks" / tid / "live"
    events = _event_tail(live / "events.jsonl", tid, root)
    beat = _heartbeat(live / "heartbeat.json", tid, root)
    status = _task_state(task, beat, events, now)
    stage = task.get("external_stage")
    label = STAGES.get(status, status)
    progress = _latest(events, "progress")
    latest = events[-1] if events else {}
    beat_seq = beat.get("seq")
    seq = max(latest.get("seq", 0), beat_seq if type(beat_seq) is int and beat_seq >= 0 else 0)
    turn = beat.get("turn_id") or latest.get("turn_id")
    ready = status in ACTION
    content = [f'<article class="task"><div class="task-head"><h3>{_text(tid, 128)}</h3>'
               f'<span class="badge {"attention" if ready else ""}">{_text(label)}</span></div>',
               f'<p class="muted">{_text(task["route"], 128)} · {_text(task["node"], 128)}</p>']
    if stage and stage != status:
        content.append(f'<p class="muted">原生阶段 · {_text(STAGES.get(stage, stage))}</p>')
    if ready:
        content.append(f'<p class="action"><strong>待处理</strong> {ACTION[status]}</p>')
    if task.get("failure_reason"):
        content.append(f'<p>{_text(task["failure_reason"], 400)}</p>')
    if task["status"] == "running" and beat.get("near_output_limit") is True:
        content.append('<p class="action">接近输出额度。</p>')
    if progress:
        values = _data(progress)
        summary = _text(values.get("summary"))
        if summary:
            content.append(f'<p class="summary">{summary}</p>')
        for title, field in (("下一步", "next_step"), ("回接义务", "bridge")):
            if text := _text(values.get(field)):
                content.append(f'<p><strong>{title}</strong> {text}</p>')
        artifacts = values.get("artifacts")
        if isinstance(artifacts, list):
            links = [_link(root, output, tid, item) for item in artifacts[:8]]
            if any(links):
                content.append('<p><strong>产物</strong> ' + ' · '.join(filter(None, links)) + '</p>')
    seal = task.get("seal_receipt") or {}
    manifest = _link(root, output, tid, seal.get("artifact_id"), label="封存清单")
    if manifest:
        content.append(f'<p>{manifest}</p>')
    accepted, acknowledged = _latest(events, "steer_accepted"), _latest(events, "steer_ack")
    if accepted:
        correction = _correction_id(accepted)
        ack = correction and any(e["seq"] > accepted["seq"] and _correction_id(e) == correction
                                 for e in events if e["kind"] == "steer_ack")
        content.append(f'<p><strong>纠偏</strong> #{accepted["seq"]} {"已确认" if ack else "已送达，等待确认"}'
                       f' <code>{_text(correction, 128)}</code></p>')
    elif acknowledged:
        content.append(f'<p><strong>纠偏</strong> #{acknowledged["seq"]} 已确认'
                       f' <code>{_text(_correction_id(acknowledged), 128)}</code></p>')
    if latest:
        content.append(f'<p class="muted">最近事件 · {EVENT_LABELS.get(latest["kind"], "执行更新")}'
                       f' · #{latest["seq"]} · {_time_label(latest["at"])}</p>')
    if seq:
        heartbeat = _time_label(beat.get("at"))
        content.append(f'<p class="muted">事件序号 {seq} · 心跳 {heartbeat}'
                       + (f' · 执行轮 <code>{_text(turn, 128)}</code>' if isinstance(turn, str) else "") + '</p>')
    content.append('</article>')
    return ''.join(content)


_CSS = """
:root{color-scheme:light;--ink:#162841;--muted:#475a71;--rule:#ccd8e6;--page:#edf2f7;--panel:#fff;--link:#195794;--warn:#853b08}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.6 Inter,system-ui,"Microsoft YaHei",sans-serif}
header{background:#162841;color:#fff;padding:2.3rem max(1.2rem,calc((100% - 1080px)/2)) 2rem}h1{font-size:clamp(1.7rem,4vw,2.7rem);margin:.1rem 0;line-height:1.25;overflow-wrap:anywhere}header p{margin:.4rem 0 0;color:#d9e6f5}
main,footer{max-width:1120px;margin:auto;padding:1.2rem}nav{display:flex;flex-wrap:wrap;gap:1rem;margin:0 0 1rem}a{color:var(--link);text-underline-offset:.18em;overflow-wrap:anywhere}
h2{font-size:1.35rem;margin:1.9rem 0 .65rem}h3{font-size:1rem;margin:0;overflow-wrap:anywhere}p{margin:.3rem 0;overflow-wrap:anywhere}code{font-family:Consolas,monospace;overflow-wrap:anywhere}.muted{color:var(--muted);font-size:.89rem}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(125px,1fr));gap:.7rem;margin:0}.metrics div,.task,.panel{background:var(--panel);border:1px solid var(--rule);border-radius:.65rem;padding:.8rem 1rem}.metrics dt{color:var(--muted)}.metrics dd{font-size:1.35rem;font-weight:700;margin:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,350px),1fr));gap:.8rem}.task-head{display:flex;justify-content:space-between;align-items:start;gap:.5rem}.badge{background:#e3edf6;color:#183f65;padding:.14rem .45rem;border-radius:.32rem;font-size:.81rem;white-space:nowrap}.attention{background:#fff0da;color:var(--warn)}.action{color:var(--warn);margin:.6rem 0}.summary{white-space:pre-wrap;margin:.65rem 0}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left}td,th{padding:.5rem .65rem;border-bottom:1px solid var(--rule);vertical-align:top;overflow-wrap:anywhere}th{color:var(--muted)}footer{color:var(--muted);font-size:.86rem}
@media print{header{padding:1rem;background:white;color:var(--ink)}header p{color:var(--muted)}body{background:white}nav{display:none}.task{break-inside:avoid}}
"""


def _render(root, output, snap, now):
    project, strategy, nodes = snap["project"], snap["strategy"], snap["nodes"]
    focus = set(strategy.get("focus_routes", []))
    running = [t for t in snap["tasks"] if t["status"] == "running"]
    pending = sorted((t for t in snap["tasks"] if t["status"] == "pending" and (not focus or t["route"] in focus)),
                     key=lambda t: t.get("created_at", ""), reverse=True)
    terminal = sorted((t for t in snap["tasks"] if t["status"] not in {"pending", "running"}),
                      key=lambda t: t.get("finished_at", t.get("created_at", "")), reverse=True)[:TERMINAL_DISPLAY]
    if due(strategy):
        gate = "全局复盘到期"
    elif strategy.get("dispatch_deferred") or snap["held"]:
        gate = "暂缓派工"
    elif snap["literature"].get("external_research") and not snap["literature"].get("plan_frozen"):
        gate = "等待主会话文献计划"
    else:
        gate = "可准备派工"
    claims = {status: [c for c in snap["claims"] if c["status"] == status and c["node"] in nodes]
              for status in ("accepted", "proposed")}
    goal = project.get("goal")
    goal_status = nodes.get(goal, "unset")
    open_count = sum(k != goal and value == "open" for k, value in nodes.items())
    title = _text(project.get("title"), 250)
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<title>{title} · 研究进度</title><style>{_CSS}</style>']
    if running or pending:
        parts.append('<meta http-equiv="refresh" content="5">')
    parts.extend([f'</head><body><header><h1>{title}</h1><p>研究进度 · <code>{_text(goal) or "未设置目标"}</code>'
                  f' · {escape(goal_status)}</p></header><main>',
                  '<nav aria-label="页面导航"><a href="#work">任务</a><a href="#routes">路线</a><a href="#evidence">证据</a></nav>',
                  '<dl class="metrics">'])
    for name, value in (("运行任务", len(running)), ("当前待启", len(pending)), ("开放义务", open_count),
                        ("已完成执行轮", strategy.get("rounds", 0))):
        parts.append(f'<div><dt>{name}</dt><dd>{value}</dd></div>')
    parts.append(f'</dl><section><h2>全局关口</h2><div class="panel"><strong>{gate}</strong>')
    if strategy.get("decision"):
        parts.append(f'<p>当前决定 <code>{_text(strategy["decision"], 128)}</code></p>')
    if due(strategy):
        parts.append('<p class="action">登记新的全局复盘决定后再派工。</p>')
    parts.append('</div></section><section id="work"><h2>当前任务</h2><div class="grid">')
    for task in [*running[:RUNNING_DISPLAY], *pending[:PENDING_DISPLAY]]:
        parts.append(_task_card(root, output, task, now))
    if not running and not pending:
        parts.append('<p class="muted">当前没有运行或等待启动的任务。</p>')
    if len(pending) > PENDING_DISPLAY:
        parts.append(f'<p class="muted">另有 {len(pending) - PENDING_DISPLAY} 个待启动任务，详见工作区状态。</p>')
    if len(running) > RUNNING_DISPLAY:
        parts.append(f'<p class="muted">另有 {len(running) - RUNNING_DISPLAY} 个运行任务，详见工作区状态。</p>')
    parts.append('</div></section>')
    if terminal:
        parts.append('<section><h2>最近终态</h2><div class="grid">')
        parts.extend(_task_card(root, output, task, now) for task in terminal)
        parts.append('</div></section>')
    parts.append('<section id="routes"><h2>证明路线</h2><div class="panel scroll"><table>'
                 '<thead><tr><th>路线</th><th>义务</th><th>状态</th><th>运行额度</th></tr></thead><tbody>')
    routes = sorted(snap["routes"], key=lambda r: (r["id"] not in focus, r["status"] != "active", r["id"]))
    for route in routes[:20]:
        parts.append(f'<tr><td>{_text(route["id"], 128)}</td><td>{_text(route["node"], 128)}</td>'
                     f'<td>{_text(route["status"])}</td><td>{route["runs"]}/{route["max_runs"]}</td></tr>')
    parts.append('</tbody></table>')
    if len(routes) > 20:
        parts.append(f'<p class="muted">显示 20 / {len(routes)} 条路线。</p>')
    parts.append('</div></section><section id="evidence"><h2>证据</h2><div class="grid">')
    for status, label in (("accepted", "已接纳"), ("proposed", "待审候选")):
        entries = sorted(claims[status], key=lambda c: c.get("created_at", ""), reverse=True)
        parts.append(f'<section class="panel"><h3>{label} · {len(entries)}</h3>')
        for claim in entries[:10]:
            evidence = f' · {_text(claim["evidence_level"])}' if status == "accepted" else ""
            parts.append(f'<p><code>{_text(claim["id"], 128)}</code>{evidence}</p>')
        if not entries:
            parts.append('<p class="muted">暂无记录</p>')
        parts.append('</section>')
    parts.extend(['</div></section></main>',
                  f'<footer>更新 {_time_label(now.isoformat())} · 注册事件 #{snap["registry_seq"]}'
                  f' · 看板版本 {REPORT_VERSION}。状态来自注册表，实时事件仅用于进度与协调。</footer></body></html>'])
    return '\n'.join(parts)


def refresh(workspace, *, output=None):
    """Render an HTML report from a consistent registry snapshot and bounded live data."""
    root = Path(workspace).resolve()
    w = Workbench(root, read_only=True)
    try:
        relative = Path(output) if output is not None else REPORT
        if relative.is_absolute() or relative.suffix.lower() != ".html" or ".." in relative.parts:
            raise ResearchError("HTML report output must be a workspace-relative .html path")
        parent = w.store.inside(relative.parent)
        if parent != root / relative.parent:
            raise ResearchError("HTML report directory is redirected")
        parent.mkdir(parents=True, exist_ok=True)
        lock = parent / ".ai4math-dashboard.lock"
        if lock.is_symlink():
            raise ResearchError("HTML report lock path is redirected")
        with file_lock(lock):
            # A Windows reader must resolve the destination after obtaining the
            # lock, rather than racing another writer's atomic replacement.
            path = w.store.inside(relative)
            if path != root / relative:
                raise ResearchError("HTML report path is redirected")
            if relative != REPORT and path.exists():
                raise ResearchError("custom HTML report output already exists")
            snap = _snapshot(w)
            atomic_write(path, _render(root, path, snap, datetime.now(timezone.utc)).encode("utf-8"))
        return path
    finally:
        w.store.close()
