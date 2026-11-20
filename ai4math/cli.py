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
              "policy.migrate", "workspace.migrate", "round.open", "round.attach", "round.record_attempt",
              "round.assess", "round.settle", "round.recover",
              "research.register", "node.propose", "checker.policy.add", "inference.add",
              "generation.request", "strategy.generate", "generation.skip", "generation.resolve",
              "policy.configure", "discovery.record",
              "obstacle.add", "obstacle.verify", "obstacle.status",
              "research_hypothesis.add", "hypothesis.link", "hypothesis.support",
              "hypothesis.refute", "hypothesis.abandon",
              "approach.add", "approach.review", "approach.activate", "approach.status",
              "approach.narrow", "strategy.select", "research_intent.add", "task.supersede",
              "batch.open", "batch.add", "batch.status", "task.preflight",
              "critique.add", "sketch.add", "obstacle.evidence", "obstacle.dispute",
              "ablation.configure", "finding.add", "finding.revise",
              "formalization.request", "formalization.resolve", "formalization.skip",
              "verification.policy.add", "verification.recheck", "legacy.audit",
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
    skills = sub.add_parser("codex-skills", help="verify project Skills or explicitly install user copies")
    skills.add_argument("action", choices=("install", "check"))
    skills.add_argument("--upgrade", action="store_true", help="explicitly replace divergent installed Skill files")
    managed = sub.add_parser("codex-run", help="launch an owned App Server research task")
    managed.add_argument("task")
    managed.add_argument("--codex-exe")
    managed.add_argument("--driver", choices=("codex", "acp"), default=None,
                         help="owned worker runtime; defaults to the task's worker_driver, then codex")
    managed.add_argument("--runtime-exe", help="worker runtime executable; defaults to codex or dsh")
    mcp = sub.add_parser("mcp-task", help="stdio MCP server exposing the ai4math_task tool")
    mcp.add_argument("--task", required=True)
    mcp.add_argument("--library", required=True, help="academic library this worker may read")
    legacy_cmd = sub.add_parser("legacy-review", help="re-review historical rounds")
    legacy_sub = legacy_cmd.add_subparsers(dest="legacy_action", required=True)
    legacy_packet = legacy_sub.add_parser("packet")
    legacy_packet.add_argument("--task", required=True)
    legacy_batch = legacy_sub.add_parser("batch")
    legacy_batch.add_argument("--size", type=int, default=6)
    legacy_batch.add_argument("--skip", type=int, default=0)
    legacy_rec = legacy_sub.add_parser("record")
    legacy_rec.add_argument("--task", required=True)
    legacy_rec.add_argument("--verdict", required=True, choices=("pass", "fail", "unsure"))
    legacy_rec.add_argument("--artifact", required=True)
    legacy_rec.add_argument("--notes", required=True)
    legacy_rec2 = legacy_sub.add_parser("record-batch")
    legacy_rec2.add_argument("--file", required=True)
    legacy_rec2.add_argument("--artifact", required=True)
    legacy_sub.add_parser("report")
    legacy_sub.add_parser("summary")
    legacy_import = legacy_sub.add_parser("import-review")
    legacy_import.add_argument("--batch", required=True)
    legacy_sub.add_parser("rounds")
    identity_cmd = sub.add_parser("identity", help="enroll reviewer and human identities")
    identity_sub = identity_cmd.add_subparsers(dest="identity_action", required=True)
    identity_enroll = identity_sub.add_parser("enroll")
    identity_enroll.add_argument("--name", required=True)
    identity_enroll.add_argument("--kind", required=True, choices=("human", "model"))
    identity_enroll.add_argument("--provider", default=None)
    identity_enroll.add_argument("--model", default=None)
    identity_sub.add_parser("list")
    human_cmd = sub.add_parser("human-checkpoint", help="require and record a human review")
    human_sub = human_cmd.add_subparsers(dest="human_action", required=True)
    human_sub.add_parser("status", help="show pending human checkpoints")
    human_confirm = human_sub.add_parser("confirm", help="record the human confirmation")
    human_confirm.add_argument("--id", required=True)
    human_confirm.add_argument("--note", required=True)
    route_assess = sub.add_parser("route-assess", help="record a kill-criteria verdict for a route")
    route_assess.add_argument("--route", required=True)
    route_assess.add_argument("--verdict", required=True, choices=("continue", "pivot", "stop"))
    route_assess.add_argument("--argument", required=True)
    inputs_cmd = sub.add_parser("inputs", help="audit whether a task froze real evidence")
    inputs_sub = inputs_cmd.add_subparsers(dest="inputs_action", required=True)
    inputs_audit = inputs_sub.add_parser("audit")
    inputs_audit.add_argument("--task", required=True)
    inputs_ack = inputs_sub.add_parser("acknowledge-thin")
    inputs_ack.add_argument("--task", required=True)
    inputs_ack.add_argument("--reason", required=True)
    review_cmd = sub.add_parser("review", help="two independent reviews gate a worker round")
    review_sub = review_cmd.add_subparsers(dest="review_action", required=True)
    review_sub.add_parser("prompt", help="print the reviewer prompt").add_argument("--role", required=True, choices=("adversarial", "verify", "strategy"))
    review_record = review_sub.add_parser("record", help="record a reviewer verdict")
    review_record.add_argument("--task", required=True)
    review_record.add_argument("--role", required=True, choices=("adversarial", "verify", "strategy"))
    review_record.add_argument("--verdict", required=True, choices=("pass", "fail"))
    review_record.add_argument("--artifact", required=True)
    review_record.add_argument("--notes", required=True)
    review_record.add_argument("--payload-file", default=None)
    review_sub.add_parser("status", help="print both review verdicts").add_argument("--task", required=True)
    ledger_cmd = sub.add_parser("ledger", help="append-only research ledger of finished rounds")
    ledger_sub = ledger_cmd.add_subparsers(dest="ledger_action", required=True)
    ledger_sub.add_parser("digest", help="print the current ledger digest")
    ledger_show = ledger_sub.add_parser("show", help="print ledger entries")
    ledger_show.add_argument("--limit", type=int, default=20)
    ledger_record = ledger_sub.add_parser("record", help="distil one sealed task into the ledger")
    ledger_record.add_argument("--task", required=True)
    opener = sub.add_parser("open-round", help="open one coordinated subagent round")
    opener.add_argument("--round", required=True)
    opener.add_argument("--node", required=True)
    opener.add_argument("--library", required=True)
    opener.add_argument("--card", default=None)
    opener.add_argument("--question", required=True)
    opener.add_argument("--ledger-sha", default="")
    opener.add_argument("--direction-note", default=None)
    opener.add_argument("--design", default=None,
                        help="JSON design file (mechanism, proposed_tool, falsifier, "
                             "representation, bridge, plan); there is no default design")
    opener.add_argument("--allow-thin-inputs", action="store_true")
    opener.set_defaults(func=None)
    batcher = sub.add_parser("open-batch", help="open 1..4 rounds, then start their tasks")
    batcher.add_argument("--file", required=True)
    batcher.add_argument("--library", required=True)
    batcher.add_argument("--card", default=None)
    batcher.add_argument("--ledger-sha", default="")
    batcher.add_argument("--direction-note", default=None)
    batcher.add_argument("--allow-thin-inputs", action="store_true")
    from .mcp_task import HOSTS
    dispatcher = sub.add_parser("dispatch-subagent", help="freeze a task for a native host subagent")
    dispatcher.add_argument("--task", required=True)
    dispatcher.add_argument("--library", required=True)
    dispatcher.add_argument("--host", choices=HOSTS, help="host observed by the coordinator")
    seal_sub = sub.add_parser("seal-subagent", help="seal a task from the native subagent broker captures")
    seal_sub.add_argument("--task", required=True)
    seal_sub.add_argument("--host-run-id", help="worker identifier observed in the declared host")
    task_cli = sub.add_parser("worker-task", help="call one ai4math_task action from a shell agent")
    task_cli.add_argument("--task", required=True)
    task_cli.add_argument("--library", required=True)
    task_cli.add_argument("--action", required=True)
    task_cli.add_argument("--payload", default="{}")
    task_cli.set_defaults(func=cmd_worker_task)
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
    health = sub.add_parser("health", help="read-only obligation health: stagnation, claims, artifacts, scope trend")
    health.add_argument("--json", action="store_true", help="emit the snapshot instead of the text panel")
    gate_review = sub.add_parser("review-gate", help="record the long-cycle review gate and clear or set the halt")
    gate_review.add_argument("--file", type=Path, required=True, help="JSON with decision, reason, next_question")
    handoff_cmd = sub.add_parser("handoff", help="print the uniform handoff record of a task")
    handoff_cmd.add_argument("--task", required=True)
    storage = sub.add_parser("storage", help="read-only report of where a workspace's bytes go")
    storage.add_argument("--json", action="store_true")
    survey = sub.add_parser("workspaces", help="survey every workspace under a root and flag duplicate goals")
    survey.add_argument("--root", required=True)
    survey.add_argument("--json", action="store_true")
    corr = sub.add_parser("correspondence", help="record the model-to-statement mapping for an obligation")
    corr.add_argument("--file", type=Path, required=True, help="JSON with node, verdict, mapping, artifacts")
    brief = sub.add_parser("strategy-brief", help="show the current global research brief and review gate")
    brief.add_argument("--goal-impact", action="store_true", help="add an audited read-only goal impact view")
    brief.add_argument("--impact-from-event", type=int, help="compare from this event instead of the latest decision")
    sub.add_parser("strategy-map", help="one readable page of approaches, hypotheses, obstacles and intents")
    sub.add_parser("strategy-schedule", help="ranked open obligations (a suggestion, not a dispatch)")
    batch_status = sub.add_parser("batch-status", help="aggregate a batch registry of one-goal workspaces")
    batch_status.add_argument("--batch", required=True)
    provenance = sub.add_parser("provenance", help="audit one claim: validations, policy trust, inference chain, effective state")
    provenance.add_argument("--claim", required=True)
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


def cmd_worker_task(args):
    from .mcp_task import TaskServer
    server = TaskServer(args.workspace, args.task, args.library)
    print(server.execute({"action": args.action, "payload": json.loads(args.payload or "{}")}))
    return 0


def main(argv=None):
    from .env import load as load_env
    load_env()
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
            result = (install_skills(upgrade=args.upgrade) if args.action == "install"
                      else skill_manifest(check_installed=False))
            _print_json(result, sys.stdout, indent=2)
            return 0
        if args.command == "lean-preflight":
            from .lean_preflight import check
            result = check(json.loads(Path(args.file).read_text(encoding="utf-8-sig")))
            _print_json(result, sys.stdout, indent=2)
            return 0 if result["passed"] else 2
        if args.command == "study":
            return study_main(args)
        if args.command == "workspaces":
            from .workspaces import panel as survey_panel, survey as survey_workspaces
            state = survey_workspaces(args.root)
            if args.json:
                _print_json(state, sys.stdout, indent=2)
            else:
                _output(None, None, survey_panel(state), plain=True)
            return 0
        if not args.workspace:
            raise ResearchError("--workspace is required for v2 commands")
        if args.command == "mcp-task":
            from .mcp_task import serve
            return serve(args.workspace, args.task, args.library)
        if args.command == "legacy-review":
            from . import legacy_review
            from .workbench import Workbench as _LegacyWorkbench
            _lw = _LegacyWorkbench(args.workspace)
            try:
                if args.legacy_action == "packet":
                    print(legacy_review.packet(_lw, args.task))
                elif args.legacy_action == "batch":
                    print(json.dumps(legacy_review.batch(_lw, args.size, args.skip), ensure_ascii=False))
                elif args.legacy_action == "record":
                    with _lw.store.transaction("legacy_review.record", args.actor):
                        print(json.dumps(legacy_review.record(_lw, args.task, args.verdict, args.artifact,
                                                              args.notes, args.actor), ensure_ascii=False))
                elif args.legacy_action == "record-batch":
                    print(json.dumps(legacy_review.record_batch(_lw, args.file, args.artifact, args.actor),
                                     ensure_ascii=False))
                elif args.legacy_action == "import-review":
                    print(json.dumps(legacy_review.import_review(_lw, args.batch, args.actor), ensure_ascii=False))
                elif args.legacy_action == "rounds":
                    print(json.dumps({"written": len(legacy_review.write_round_docs(_lw))}, ensure_ascii=False))
                elif args.legacy_action == "summary":
                    print(json.dumps(legacy_review.summary(_lw), ensure_ascii=False))
                else:
                    result = legacy_review.report(_lw)
                    print(json.dumps({k: result[k] for k in ("completed", "reviewable", "reviewed",
                                                             "unreviewable")}, ensure_ascii=False)[:400])
                    print("pass", len(result["pass"]), "fail", len(result["fail"]),
                          "unsure", len(result["unsure"]), "pending", len(result["pending"]))
            finally:
                _lw.store.close()
            return 0
        if args.command == "identity":
            from . import identities
            from .workbench import Workbench as _IdentityWorkbench
            _iw = _IdentityWorkbench(args.workspace)
            try:
                if args.identity_action == "enroll":
                    with _iw.store.transaction("identity.enroll", args.actor):
                        print(json.dumps(identities.enroll(_iw, args.name, args.kind, args.provider,
                                                           args.model, args.actor), ensure_ascii=False))
                else:
                    print(json.dumps(_iw.store.all("identity"), ensure_ascii=False))
            finally:
                _iw.store.close()
            return 0
        if args.command == "human-checkpoint":
            from . import checkpoint_human
            from .workbench import Workbench as _HumanWorkbench
            _hw = _HumanWorkbench(args.workspace)
            try:
                if args.human_action == "confirm":
                    with _hw.store.transaction("human_checkpoint.confirm", args.actor):
                        print(json.dumps(checkpoint_human.confirm(_hw, args.id, args.actor, args.note), ensure_ascii=False))
                else:
                    rows = [r for r in _hw.store.all("human_checkpoint")]
                    print(json.dumps({"pending": [r["id"] for r in rows if not r.get("confirmed_at")],
                                      "confirmed": [r["id"] for r in rows if r.get("confirmed_at")],
                                      "completed_rounds": len(checkpoint_human.pending_rounds(_hw)),
                                      "every": checkpoint_human.every()}, ensure_ascii=False))
            finally:
                _hw.store.close()
            return 0
        if args.command == "route-assess":
            from .workbench import Workbench as _RouteWorkbench
            _rw = _RouteWorkbench(args.workspace)
            try:
                print(json.dumps(_rw.apply("route.assess", {"id": args.route, "verdict": args.verdict,
                                                            "argument": args.argument}, args.actor), ensure_ascii=False))
            finally:
                _rw.store.close()
            return 0
        if args.command == "inputs":
            from . import inputs as _inputs
            from .workbench import Workbench as _InputsWorkbench
            _iw = _InputsWorkbench(args.workspace)
            try:
                if args.inputs_action == "audit":
                    print(json.dumps(_inputs.audit(_iw, _iw.store.get("task", args.task)), ensure_ascii=False))
                else:
                    with _iw.store.transaction("task.thin_inputs_ack", args.actor):
                        task = _iw.store.get("task", args.task)
                        task["thin_inputs_ack"] = {"reason": args.reason, "by": args.actor}
                        _iw.store.put("task", args.task, task)
                    print(json.dumps({"acknowledged": args.task}, ensure_ascii=False))
            finally:
                _iw.store.close()
            return 0
        if args.command == "review":
            from . import review as _review
            from .workbench import Workbench as _ReviewWorkbench
            _rw = _ReviewWorkbench(args.workspace)
            try:
                if args.review_action == "prompt":
                    print(_review.PROMPTS[args.role])
                elif args.review_action == "record":
                    payload = None
                    if args.payload_file:
                        payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
                    with _rw.store.transaction("review.record", args.actor):
                        print(json.dumps(_review.record(_rw, args.task, args.role, args.verdict, args.artifact,
                                                        args.notes, args.actor, payload), ensure_ascii=False))
                else:
                    print(json.dumps(_review.status(_rw, args.task), ensure_ascii=False, default=str))
            finally:
                _rw.store.close()
            return 0
        if args.command == "ledger":
            from . import ledger as _ledger
            from .workbench import Workbench as _LedgerWorkbench
            _lw = _LedgerWorkbench(args.workspace)
            try:
                if args.ledger_action == "digest":
                    print(_ledger.digest(_lw.store.root))
                elif args.ledger_action == "show":
                    for entry in _ledger.entries(_lw.store.root)[-args.limit:]:
                        print(json.dumps(entry, ensure_ascii=False))
                else:
                    print(json.dumps({"sha": _ledger.record_task(_lw, args.task, args.actor)}, ensure_ascii=False))
            finally:
                _lw.store.close()
            return 0
        if args.command == "open-round":
            from .round import open_round
            from .workbench import Workbench as _RoundWorkbench
            _rw = _RoundWorkbench(args.workspace)
            try:
                design = None
                if args.design:
                    design = json.loads(Path(args.design).read_text(encoding="utf-8"))
                print(json.dumps(open_round(_rw, args.round, args.node, args.library,
                                            args.question, ledger_sha=args.ledger_sha,
                                            direction_note=args.direction_note,
                                            allow_thin=args.allow_thin_inputs, card=args.card,
                                            design=design),
                                 ensure_ascii=False))
            finally:
                _rw.store.close()
            return 0
        if args.command == "open-batch":
            from .round import open_batch
            from .workbench import Workbench as _BatchWorkbench
            _bw = _BatchWorkbench(args.workspace)
            try:
                specs = json.loads(Path(args.file).read_text(encoding="utf-8"))
                print(json.dumps(open_batch(_bw, specs, args.library, ledger_sha=args.ledger_sha,
                                            direction_note=args.direction_note,
                                            allow_thin=args.allow_thin_inputs, card=args.card), ensure_ascii=False, default=str))
            finally:
                _bw.store.close()
            return 0
        if args.command == "dispatch-subagent":
            from .codex_runner import prepare_subagent
            from .workbench import Workbench as _Workbench
            _w = _Workbench(args.workspace)
            try:
                print(json.dumps(prepare_subagent(_w, args.task, args.actor, args.library, host=args.host),
                                 ensure_ascii=False, default=str))
            finally:
                _w.store.close()
            return 0
        if args.command == "seal-subagent":
            from .worker_finish import seal_subagent
            print(json.dumps(seal_subagent(args.workspace, args.task, args.actor, host_run_id=args.host_run_id),
                             ensure_ascii=False))
            return 0
        if args.command == "worker-task":
            return cmd_worker_task(args)
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
            read_only = (args.command in {"codex-steer", "diagnose", "strategy-brief"} or (args.command == "seal-external" and not args.submit) or
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
                runtime = args.runtime_exe or args.codex_exe
                result = run_managed(wb, args.task, args.actor, runtime, args.library, args.driver)
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
                if args.impact_from_event is not None and not args.goal_impact:
                    raise ResearchError("--impact-from-event requires --goal-impact")
                with wb.store.snapshot():
                    result = brief(wb)
                    if args.goal_impact:
                        from .goal_impact import view as goal_impact
                        result["goal_impact"] = goal_impact(wb, from_event=args.impact_from_event)
            elif args.command == "strategy-map":
                from .strategy import map_view
                result = map_view(wb)
            elif args.command == "strategy-schedule":
                from .strategy import schedule
                result = schedule(wb)
            elif args.command == "batch-status":
                from .batch import status as batch_status
                result = batch_status(wb, {"batch": args.batch}, "reader")
            elif args.command == "provenance":
                from .verification import provenance as claim_provenance
                result = claim_provenance(wb, args.claim)
            elif args.command == "status":
                result = wb.board()
            elif args.command == "storage":
                from .storage import panel as storage_panel, survey as storage_survey
                state = storage_survey(wb.store.root)
                if args.json:
                    result = state
                else:
                    _output(wb, None, storage_panel(state), plain=True)
                    return 0
            elif args.command == "handoff":
                from .handoff import read as handoff_read
                result = handoff_read(wb.store, args.task)
            elif args.command == "correspondence":
                payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
                from .correspondence import record as correspondence_record
                result = correspondence_record(wb, payload, args.actor)
            elif args.command == "review-gate":
                from .review_gate import review as gate_review_record
                payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
                result = gate_review_record(wb, payload, args.actor)
            elif args.command == "health":
                from .health import panel, snapshot
                state = snapshot(wb)
                if args.json:
                    result = state
                else:
                    _output(wb, None, panel(state), plain=True)
                    return 0
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
