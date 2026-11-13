"""AI4Math v2 command interface. JSON files are the stable automation API."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from .execution import export_task, run_task, start_external
from .report import markdown
from .store import ResearchError
from .workbench import Workbench
from . import __version__

OPERATIONS = ["node.add", "node.plan", "goal.set", "route.add", "route.status", "route.checkpoint",
              "artifact.import", "claim.add", "validation.add", "review.add", "claim.accept", "claim.revoke",
              "task.add", "task.recover", "ledger.add", "research.configure", "method.add", "method.review", "research.note", "tool.evidence.add",
              "knowledge.attach", "exploration.open", "exploration.propose", "exploration.probe", "exploration.evaluate", "exploration.feedback",
              "exploration.transfer", "exploration.counterexample", "exploration.resolve",
              "diagnosis.revision", "diagnosis.replay", "diagnosis.indistinguishability", "diagnosis.source_check",
              "strategy.enable", "strategy.decide", "task.finish_external",
              "literature.enable", "literature.search", "literature.plan", "task.literature_ready",
              "task.release_exploration", "task.begin_seal", "task.submit_seal", "task.confirm_stop",
              "task.preflight_seal_batch", "task.submit_seal_batch", "task.codex_hold", "task.codex_stop", "codex.dispatch_hold"]


def build_parser():
    parser = argparse.ArgumentParser(prog="ai4math", description="Auditable mathematical research workbench")
    parser.add_argument("--workspace", help="v2 workspace directory; separate from the mathematical project")
    parser.add_argument("--actor", help="local attribution label, not an authenticated identity")
    parser.add_argument("--version", action="version", version="ai4math " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a v2 research workspace")
    init.add_argument("--project-id", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--review-interval", type=int, default=3, help="execution rounds per global review")
    init.add_argument("--local-research", action="store_true",
                      help="use the local scripted workflow instead of requiring external literature-gated research")
    apply = sub.add_parser("apply", help="apply one atomic research operation")
    apply.add_argument("operation", choices=OPERATIONS)
    source = apply.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="UTF-8 JSON object")
    source.add_argument("--json", help="inline JSON object")
    for name in ("run", "export-task", "start-external", "check-external"):
        command = sub.add_parser(name)
        command.add_argument("task")
    supervisor = sub.add_parser("supervise-external", help="dispatch and hard-stop an owned external worker process tree")
    supervisor.add_argument("task")
    supervisor.add_argument("--worker-argv-file", required=True, help="JSON array of worker executable and arguments; no shell")
    skills = sub.add_parser("codex-skills", help="install or verify the local ai4math Skills")
    skills.add_argument("action", choices=("install", "check"))
    skills.add_argument("--upgrade", action="store_true", help="explicitly replace divergent installed Skill files")
    managed = sub.add_parser("codex-run", help="launch an owned App Server research task")
    managed.add_argument("task")
    managed.add_argument("--codex-exe")
    managed.add_argument("--library", required=True, help="academic library path pinned in the task")
    checkpoint = sub.add_parser("checkpoint", help="export an event prefix or restore an auditable evaluation branch")
    cp = checkpoint.add_subparsers(dest="checkpoint_action", required=True)
    cp_export = cp.add_parser("export")
    cp_export.add_argument("--at", required=True, type=int)
    cp_export.add_argument("--out", required=True, type=Path)
    cp_restore = cp.add_parser("restore")
    cp_restore.add_argument("--from", dest="source", required=True, type=Path)
    cp_restore.add_argument("--sha256", required=True)
    cp_restore.add_argument("--allowed-root", action="append", default=[])
    for command in (cp_export, cp_restore):
        command.add_argument("--model-ledger", type=Path)
        command.add_argument("--resource-ledger", type=Path)
    study = sub.add_parser("study", help="freeze a benchmark schedule and audit its outcomes and blind reviews")
    study.add_argument("--root", required=True, type=Path)
    st = study.add_subparsers(dest="study_action", required=True)
    st_init = st.add_parser("init")
    st_init.add_argument("--artifacts", type=Path, help="JSON mapping of selected source hashes to local paths")
    st_record = st.add_parser("record")
    for command in (st_init, st_record):
        command.add_argument("--file", required=True, type=Path)
        command.add_argument("--model-ledger", required=True, type=Path)
        command.add_argument("--resource-ledger", type=Path)
    st_blind = st.add_parser("blind-export")
    st_blind.add_argument("--out", required=True, type=Path)
    st_review = st.add_parser("review")
    st_review.add_argument("--file", required=True, type=Path)
    st_inspect = st.add_parser("inspect-export")
    st_inspect.add_argument("--at", required=True, type=int)
    st_inspect.add_argument("--out", required=True, type=Path)
    st.add_parser("audit")
    watch = sub.add_parser("codex-queue", help="read live progress and pending coordinator actions")
    watch.add_argument("--after", default="{}", help="JSON task-to-sequence cursor from the last response")
    watch.add_argument("--wait", type=int, default=0)
    watch.add_argument("--limit", type=int, default=30)
    steer = sub.add_parser("codex-steer", help="send feedback to an exact active research turn")
    steer.add_argument("task")
    steer.add_argument("--turn", required=True)
    steer.add_argument("--event", required=True, type=int)
    steer.add_argument("--text", required=True)
    controls = sub.add_parser("codex-control", help="hold dispatch, resume dispatch, or stop managed tasks")
    controls.add_argument("action", choices=("hold", "continue", "stop"))
    controls.add_argument("task", nargs="?")
    controls.add_argument("--reason", help="required for stop; optional note for hold or continue")
    lean = sub.add_parser("lean-preflight", help="check pinned Lean version and direct-import files without compilation")
    lean.add_argument("--file", required=True, help="JSON configuration with pinned binary, probe and dependencies")
    seal = sub.add_parser("seal-external", help="preflight (default) or atomically submit a hash-bound output batch")
    seal.add_argument("task")
    seal.add_argument("--file", required=True, help="JSON object with contract_sha256 and artifacts [{path,sha256}]")
    mode = seal.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only preflight; no staging or deadline mutation (default)")
    mode.add_argument("--submit", action="store_true", help="stage bytes and atomically commit the complete seal")
    sub.add_parser("status", help="show goal, route board, claim and review queue")
    sub.add_parser("strategy-brief", help="show the current global research brief and review gate")
    audit = sub.add_parser("audit", help="check event replay and all frozen bytes")
    audit.add_argument("--anchor", help="expected previously published event tail hash")
    report = sub.add_parser("report", help="render Markdown route board and evidence ledger")
    report.add_argument("--output", help="derived file path relative to workspace")
    report.add_argument("--format", choices=("markdown", "html"), default="markdown")
    summary = sub.add_parser("summary", help="refresh the research summary or append a reviewed round handoff")
    summary.add_argument("--record", type=Path, help="JSON round record to append")
    summary.add_argument("--library", type=Path, help="selected academic library for the round's passage IDs")
    events = sub.add_parser("events", help="export complete event log")
    events.add_argument("--output", help="derived file path relative to workspace")
    sub.add_parser("delivery-check", help="check goal and manuscript evidence readiness")
    knowledge = sub.add_parser("knowledge", help="separate, versioned academic knowledge library")
    knowledge.add_argument("--root", required=True, help="academic library directory, not a mathematical workspace")
    ks = knowledge.add_subparsers(dest="knowledge_command", required=True)
    ki = ks.add_parser("init")
    ki.add_argument("--empty", action="store_true")
    ki.add_argument("--max-remote-requests", type=int, default=100)
    ks.add_parser("seed", help="idempotently add bundled sources and mechanism cards")
    for kind in ("source", "card", "profile"):
        add = ks.add_parser("add-" + kind)
        add.add_argument("--file", required=True)
    search = ks.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--kind", choices=("source", "mechanism", "passage", "scan"), default="mechanism")
    search.add_argument("--capability")
    search.add_argument("--limit", type=int, default=5)
    show = ks.add_parser("show")
    show.add_argument("--kind", choices=("source", "mechanism", "discovery", "document", "passage", "paper_profile"), required=True)
    show.add_argument("--id", required=True)
    discovery = ks.add_parser("discover", help="reuse a completed query or make one explicit metadata request")
    discovery.add_argument("--provider", choices=("crossref", "arxiv"), required=True)
    discovery.add_argument("--query", required=True)
    discovery.add_argument("--limit", type=int, default=5)
    discovery.add_argument("--timeout", type=int, default=20)
    discovery.add_argument("--refresh", action="store_true", help="make a new request and charge the existing quota")
    document = ks.add_parser("add-document", help="freeze a local original and index UTF-8 text/HTML; PDF bytes retained only")
    document.add_argument("--source", required=True)
    document.add_argument("--file", required=True)
    document.add_argument("--format", choices=("text", "html", "pdf"), default="text")
    passage = ks.add_parser("add-passage", help="register a selected transcription with its original document and locator")
    passage.add_argument("--document", required=True)
    passage.add_argument("--locator", required=True)
    passage.add_argument("--file", required=True)
    queue = ks.add_parser("review-queue", help="list archived originals missing a profile or readable passage")
    queue.add_argument("--limit", type=int, default=20)
    request = ks.add_parser("request", help="submit a literature gap from a research workspace without waiting")
    request.add_argument("--route", required=True)
    request.add_argument("--file", required=True)
    inbox = ks.add_parser("inbox", help="list literature requests and published results")
    inbox.add_argument("--status", choices=("queued", "claimed", "published"))
    inbox.add_argument("--id")
    inbox.add_argument("--limit", type=int, default=20)
    inbox.add_argument("--wait", type=int, default=0, help="wait at most this many seconds for a matching request")
    inbox.add_argument("--after", type=int, help="read published deliveries after this sequence number")
    claim = ks.add_parser("request-claim", help="claim a queued request as the literature session")
    claim.add_argument("--id", help="default: oldest queued request")
    finish = ks.add_parser("request-finish", help="publish a result or record an interrupted attempt")
    finish.add_argument("--id", required=True)
    finish.add_argument("--attempt", required=True)
    finish.add_argument("--file", required=True)
    recovery = ks.add_parser("recover")
    recovery.add_argument("--id", required=True)
    recovery.add_argument("--reason", required=True)
    ks.add_parser("audit")
    explore = sub.add_parser("explore", help="read-only portfolio, probe ranking and agent guidance")
    explore.add_argument("--pool", required=True)
    explore.add_argument("--limit", type=int, default=5)
    diagnose = sub.add_parser("diagnose", help="inspect counterexample impact, witness replays and theorem applicability")
    diagnose.add_argument("--node", required=True)
    coprime = sub.add_parser("import-coprime", help="seed a workspace from read-only historical project records")
    coprime.add_argument("--config", default="projects/coprime-path4.json")
    legacy = sub.add_parser("legacy", help="read-only v1 audit compatibility")
    legacy.add_argument("legacy_args", nargs=argparse.REMAINDER)
    for name in ("gate", "registry-audit"):
        command = sub.add_parser(name, help="read-only v1 compatibility alias")
        command.add_argument("--config", required=True)
        if name == "gate":
            command.add_argument("--phase", type=int, required=True)
    return parser


def legacy_main(argv):
    from . import legacy_cli
    if not argv or argv[0] not in {"gate", "registry-audit", "status"}:
        raise ResearchError("v2 exposes only read-only legacy gate, registry-audit and status")
    args = legacy_cli.build_parser().parse_args(argv)
    return args.func(args)


def _print_json(value, stream, *, indent=None):
    content = json.dumps(value, ensure_ascii=False, indent=indent)
    encoding = getattr(stream, "encoding", None)
    if encoding:
        try:
            content.encode(encoding)
        except UnicodeEncodeError:
            content = json.dumps(value, ensure_ascii=True, indent=indent)
    print(content, file=stream)


def _output(wb, output, value, plain=False):
    if output:
        path = wb.store.inside(output)
        if not path.is_relative_to(wb.store.inside("exports")):
            raise ResearchError("derived output must be under exports/ in the workspace")
        path.parent.mkdir(parents=True, exist_ok=True)
        content = value if plain else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        path.write_text(content, encoding="utf-8")
        _print_json({"output": str(path)}, sys.stdout)
    elif plain:
        print(value, end="" if value.endswith("\n") else "\n")
    else:
        _print_json(value, sys.stdout, indent=2)


def codex_control(w, args):
    from .codex_live import control_request
    from .codex_runner import refresh_report
    if args.action != "stop":
        payload = {"held": args.action == "hold", "reason": args.reason or f"Coordinator requested {args.action}"}
        if args.task:
            result = w.apply("task.codex_hold", {"task": args.task, **payload}, args.actor)
        else:
            result = w.apply("codex.dispatch_hold", payload, args.actor)
    else:
        if not args.reason or not args.reason.strip():
            raise ResearchError("codex-control stop requires --reason")
        if args.task:
            tasks = [w.store.get("task", args.task)]
        else:
            tasks = [t for t in w.store.all("task") if t.get("codex_workflow") and t["status"] == "running"
                     and t["dispatched_by"] == args.actor and t.get("external_stage") != "sealed"]
            w.apply("codex.dispatch_hold", {"held": True, "reason": args.reason}, args.actor)
        pending = []
        for task in tasks:
            request = control_request(w, task["id"], args.actor, "interrupt", reason=args.reason)
            reply = w.store.inside(Path("tasks") / task["id"] / "live/responses" / (request["id"] + ".json"))
            pending.append((task["id"], reply))
        end = time.monotonic() + 3
        while pending and not all(path.is_file() for _, path in pending) and time.monotonic() < end:
            time.sleep(0.1)
        result = {"stop_requested": []}
        for task, _ in pending:
            current = w.store.get("task", task)
            if current["status"] == "running" and current.get("external_stage") != "sealed":
                current = w.apply("task.codex_stop", {"task": task, "reason": args.reason}, args.actor)
            result["stop_requested"].append({"task": task, "status": current["status"], "stop_state": current.get("stop_state")})
    rendered = refresh_report(w.store.root)
    if isinstance(rendered, dict):
        print(rendered["report_error"], file=sys.stderr)
    return result


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    wb = None
    try:
        if args.command == "legacy":
            return legacy_main(args.legacy_args)
        if args.command in {"gate", "registry-audit"}:
            legacy_args = [args.command, "--config", args.config]
            if args.command == "gate":
                legacy_args.extend(["--phase", str(args.phase)])
            return legacy_main(legacy_args)
        if args.command == "knowledge":
            return knowledge_main(args)
        if args.actor is None:
            args.actor = "human"
        if args.command == "codex-skills":
            from .codex_skills import install_skills, skill_manifest
            if args.action == "check" and args.upgrade:
                raise ResearchError("--upgrade is only available with install")
            result = install_skills(upgrade=args.upgrade) if args.action == "install" else skill_manifest()
            _print_json(result, sys.stdout, indent=2)
            return 0
        if args.command == "lean-preflight":
            from .lean_preflight import check
            result = check(json.loads(Path(args.file).read_text(encoding="utf-8-sig")))
            _print_json(result, sys.stdout, indent=2)
            return 0 if result["passed"] else 2
        if args.command == "study":
            return study_main(args)
        if not args.workspace:
            raise ResearchError("--workspace is required for v2 commands")
        if args.command == "checkpoint":
            from .checkpoint import export_checkpoint, restore_checkpoint
            from .evaluation import EvaluationLedger
            from .resource_ledger import ResourceLedger
            ledgers = {}
            if args.model_ledger:
                ledgers["model"] = EvaluationLedger(args.model_ledger)
            if args.resource_ledger:
                ledgers["resource"] = ResourceLedger(args.resource_ledger)
            if args.checkpoint_action == "export":
                wb = Workbench(args.workspace, read_only=True)
                result = export_checkpoint(wb, args.out, args.at, ledgers=ledgers)
            else:
                result = restore_checkpoint(args.source, args.workspace, args.sha256, actor=args.actor,
                                            ledgers=ledgers, allowed_roots=args.allowed_root)
            _print_json(result, sys.stdout, indent=2)
            return 0
        if args.command == "codex-queue":
            from .codex_live import queue_view
            result = queue_view(args.workspace, json.loads(args.after), args.wait, args.limit)
            _print_json(result, sys.stdout, indent=2)
            return 0
        if args.command == "report" and args.format == "html":
            from .codex_dashboard import refresh
            _print_json({"path": str(refresh(Path(args.workspace), output=args.output))}, sys.stdout, indent=2)
            return 0
        if args.command == "summary":
            from .research_summary import refresh
            wb = Workbench(args.workspace, read_only=True)
            payload = json.loads(args.record.read_text(encoding="utf-8-sig")) if args.record else None
            if args.record and not isinstance(payload, dict):
                raise ResearchError("summary record must be a JSON object")
            if args.library and not args.record:
                raise ResearchError("summary --library requires --record")
            _print_json(refresh(wb, payload=payload, library=args.library, actor=args.actor), sys.stdout, indent=2)
            return 0
        if args.command == "init":
            wb = Workbench.create(args.workspace, args.project_id, args.title, args.actor,
                                  review_interval=args.review_interval, external_research=not args.local_research)
            result = {"workspace": str(wb.store.root), "project": wb.store.get("project", "main")}
        else:
            read_only = (args.command in {"codex-steer", "diagnose"} or (args.command == "seal-external" and not args.submit) or
                         (args.command == "apply" and args.operation == "task.preflight_seal_batch"))
            wb = Workbench(args.workspace, read_only=read_only)
            if args.command == "apply":
                payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig") if args.file else args.json)
                if not isinstance(payload, dict):
                    raise ResearchError("operation payload must be an object")
                result = wb.apply(args.operation, payload, args.actor)
                if args.operation in {"task.literature_ready", "task.release_exploration", "task.finish_external", "claim.accept", "claim.revoke", "strategy.decide"}:
                    report_path = wb.store.root / "reports/research-progress.html"
                    if report_path.is_file() or any(t.get("codex_workflow") for t in wb.store.all("task")):
                        from .codex_runner import refresh_report
                        rendered = refresh_report(wb.store.root)
                        if isinstance(rendered, dict):
                            print(rendered["report_error"], file=sys.stderr)
            elif args.command == "codex-run":
                from .codex_runner import run_managed
                result = run_managed(wb, args.task, args.actor, args.codex_exe, args.library)
            elif args.command == "codex-steer":
                from .codex_live import control_request
                result = control_request(wb, args.task, args.actor, "steer", turn=args.turn, event=args.event, text=args.text)
            elif args.command == "codex-control":
                result = codex_control(wb, args)
            elif args.command == "run":
                result = run_task(wb, args.task, args.actor)
            elif args.command == "export-task":
                result = export_task(wb, args.task)
            elif args.command == "start-external":
                result = start_external(wb, args.task, args.actor)
            elif args.command == "check-external":
                from .external_deadline import expire
                expire(wb, args.task, args.actor)
                result = wb.store.get("task", args.task)
            elif args.command == "supervise-external":
                from .external_supervisor import supervise_external
                argv = json.loads(Path(args.worker_argv_file).read_text(encoding="utf-8-sig"))
                result = supervise_external(wb, args.task, argv, args.actor)
            elif args.command == "seal-external":
                payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
                if not isinstance(payload, dict) or payload.get("task", args.task) != args.task:
                    raise ResearchError("seal file must bind the selected task")
                payload["task"] = args.task
                result = wb.apply("task.submit_seal_batch" if args.submit else "task.preflight_seal_batch", payload, args.actor)
            elif args.command == "strategy-brief":
                from .strategy import brief
                result = brief(wb)
            elif args.command == "status":
                result = wb.board()
            elif args.command == "explore":
                from .exploration import board
                result = board(wb, args.pool, args.limit)
            elif args.command == "diagnose":
                from .diagnosis import report
                result = report(wb, args.node)
            elif args.command == "audit":
                result = wb.audit()
                if args.anchor and args.anchor != result["tail_hash"]:
                    result["errors"].append("event tail differs from supplied external anchor")
                    result["passed"] = False
            elif args.command == "delivery-check":
                result = wb.delivery_check()
            elif args.command == "report":
                _output(wb, args.output, markdown(wb), plain=True)
                return 0
            elif args.command == "events":
                _output(wb, args.output, wb.store.export_events())
                return 0
            elif args.command == "import-coprime":
                from .adapters import import_coprime
                result = import_coprime(wb, Path(args.config), args.actor)
            else:
                raise ResearchError("unhandled command")
        _output(wb, None, result)
        if args.command in {"audit", "delivery-check"}:
            return 0 if result["passed"] else 2
        if args.command == "seal-external" and not args.submit:
            return 0 if result["passed"] else 2
        if args.command == "apply" and args.operation == "task.preflight_seal_batch":
            return 0 if result["passed"] else 2
        if args.command in {"run", "supervise-external", "codex-run"}:
            if args.command in {"supervise-external", "codex-run"} and result.get("external_stage") == "sealed" and result.get("stop_state") == "confirmed":
                return 0
            return 0 if result["status"] == "completed" else 2
        return 0
    except (ResearchError, OSError, ValueError, sqlite3.Error, KeyError, TypeError, subprocess.SubprocessError) as exc:
        error = {"error": str(exc), "type": type(exc).__name__}
        if hasattr(exc, "report"):
            error["preflight"] = exc.report
        _print_json(error, sys.stderr)
        return 2
    finally:
        if wb:
            wb.store.close()


def study_main(args):
    from . import study
    from .evaluation import EvaluationLedger
    from .resource_ledger import ResourceLedger
    from .store import Store

    action = args.study_action
    payload = json.loads(args.file.read_text(encoding="utf-8-sig")) if hasattr(args, "file") else None
    model = EvaluationLedger(args.model_ledger) if hasattr(args, "model_ledger") else None
    resources = ResourceLedger(args.resource_ledger) if getattr(args, "resource_ledger", None) else None
    if action == "init":
        artifacts = json.loads(args.artifacts.read_text(encoding="utf-8-sig")) if args.artifacts else {}
        result = study.create(args.root, payload, artifacts, model, resources, args.actor)
    else:
        store = Store(args.root, read_only=action in {"audit", "inspect-export"})
        try:
            if action == "record":
                result = study.record_outcome(store, payload, model, resources, args.actor)
            elif action == "blind-export":
                result = study.export_blind(store, args.out, args.actor)
            elif action == "review":
                result = study.record_review(store, payload, args.actor)
            elif action == "inspect-export":
                from .study_inspect import export
                result = export(store, args.out, args.at)
            else:
                result = study.audit(store)
        finally:
            store.close()
    _print_json(result, sys.stdout, indent=2)
    return 0 if result.get("integrity", {"passed": True})["passed"] else 2


def knowledge_main(args):
    from .knowledge import AcademicLibrary
    command = args.knowledge_command
    actor = "operator" if args.actor is None else args.actor
    lib = (AcademicLibrary.create(args.root, args.max_remote_requests) if command == "init"
           else AcademicLibrary(args.root))
    try:
        if command in {"init", "seed"}:
            result = lib.audit() if command == "init" and args.empty else lib.seed(actor=actor)
        elif command in {"add-source", "add-card", "add-profile"}:
            data = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
            add = {"add-source": lib.add_source, "add-card": lib.add_card, "add-profile": lib.add_profile}
            result = add[command](data, actor=actor)
        elif command == "search":
            result = lib.search(args.query, args.kind, args.limit, args.capability)
        elif command == "show":
            lib._healthy()
            result = lib.store.get(args.kind, args.id)
        elif command == "discover":
            result = lib.discover(args.provider, args.query, args.limit, args.timeout, refresh=args.refresh, actor=actor)
        elif command == "add-document":
            result = lib.add_document(args.source, args.file, args.format, actor=actor)
        elif command == "add-passage":
            with Path(args.file).open(encoding="utf-8-sig") as passage:
                result = lib.add_passage(args.document, args.locator, passage.read(4001), actor=actor)
        elif command == "review-queue":
            result = lib.review_queue(args.limit)
        elif command in {"request", "inbox", "request-claim", "request-finish"}:
            result = knowledge_inbox(lib, args)
        elif command == "recover":
            result = lib.recover(args.id, args.reason, actor=actor)
        else:
            result = lib.audit()
        _print_json(result, sys.stdout, indent=2)
        return 2 if isinstance(result, dict) and (result.get("passed") is False or result.get("status") == "failed") else 0
    finally:
        lib.store.close()


def knowledge_inbox(lib, args):
    from .literature_inbox import LiteratureInbox, workspace_key
    from .research import allowed_source

    inbox = LiteratureInbox(lib.store)
    command = args.knowledge_command
    actor = "human" if args.actor is None else args.actor
    if command == "inbox":
        return inbox.list(status=args.status, request_id=args.id, limit=args.limit, wait=args.wait,
                          workspace=workspace_key(args.workspace) if args.workspace else None, after=args.after)
    if command == "request-claim":
        return inbox.claim(actor, args.id)
    payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
    if command == "request-finish":
        return inbox.finish(args.id, args.attempt, payload, actor)
    if not args.workspace:
        raise ResearchError("literature request requires --workspace")
    wb = Workbench(args.workspace, read_only=True)
    try:
        allowed_source(wb, lib.store.root)
        route = wb.store.get("route", args.route)
    finally:
        wb.store.close()
    return inbox.submit(payload, {"workspace": workspace_key(args.workspace), "route": args.route, "node": route["node"]}, actor)


if __name__ == "__main__":
    raise SystemExit(main())
