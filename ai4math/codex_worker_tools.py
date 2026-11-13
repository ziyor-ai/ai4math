"""Phase-checked tools executed by the local Codex worker adapter."""
from __future__ import annotations

from pathlib import Path
import json
from itertools import islice
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .codex_live import read_json, workspace
from .external_deadline import ensure_live
from .knowledge import PROFILE_FIELDS, PROFILE_LISTS, check_profile_fields, document_text, rank, scan_records
from .literature_inbox import LiteratureInbox, workspace_key
from .store import ResearchError, Store, digest, file_hash, now
from . import research_return


VIEW_BYTES = 24 * 1024
SOURCE_BYTES = 4 * 1024 * 1024
SOURCE_TOTAL_BYTES = 8 * 1024 * 1024
SOURCE_REQUESTS = 3
TOOL_NAME = "ai4math_task"
TOOL = {"type": "function", "name": TOOL_NAME, "description": "Read frozen task inputs, perform bounded literature work, report progress, and submit an ai4math research result. Use action contract first.",
        "inputSchema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["contract", "read", "search", "reuse_passage", "fetch", "request_literature", "literature_updates", "literature_ready", "progress", "write", "run", "handoff"]},
            "payload": {"type": "object"}}, "required": ["action", "payload"], "additionalProperties": False}}


def _text(obj, key, limit=4000):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip() or (limit is not None and len(value) > limit):
        suffix = f" (up to {limit} characters)" if limit is not None else ""
        raise ResearchError(f"nonempty {key} required{suffix}")
    return value


def _write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


class HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ResearchError("original source redirect must stay on HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_worker_profile(profile):
    if "tool_sandbox_artifact" in profile:
        raise ResearchError("the frozen task requires a retired tool sandbox; prepare a new authorized task")


class WorkerTools:
    def __init__(self, root, task, library):
        profile = task["codex_workflow"]
        check_worker_profile(profile)
        self.root = Path(root)
        self.task = task
        self.actor = profile["worker_actor"]
        self.tid = task["id"]
        self.library = Path(library)
        self.directory = self.root / "tasks" / self.tid
        self.live = self.directory / "live"
        self.return_dir = self.directory / "return"
        self.contract = read_json(self.directory / "input.json")
        self.async_literature = task.get("literature", {}).get("acquisition_mode") == "async"
        self.local_literature = profile.get("literature_acquisition") == "local_only"
        self.probe_review = None
        self.derivation = None
        self.inputs = set(task["inputs"])
        self.contract_files = {task["contract_artifact"], task["prompt_artifact"]}
        self.seen = {}
        self.complete_reads = set()
        self.sources = {}
        self.queries = []
        self.literature_requests = set()
        self.read_inputs = set()
        self.written = {}
        self.handoff = None
        self.tool_bytes = 0
        self.source_bytes = 0
        self.fetches = 0
        self.current_process = None
        self.tools = {"python": str(Path(sys.executable).resolve())}
        self.expected_tools = {}
        self.model_settings = None
        model_settings = profile.get("model_settings_artifact")
        toolchain = profile.get("toolchain_artifact")
        if model_settings or toolchain:
            with workspace(root) as w:
                if model_settings:
                    self.model_settings = read_json(w.store.verify_blob(model_settings))
                if toolchain:
                    self.expected_tools = read_json(w.store.verify_blob(toolchain))

    def check(self, action):
        with workspace(self.root) as w:
            task = w.store.get("task", self.tid)
        if (task["status"] != "running" or task["contract_artifact"] != self.task["contract_artifact"]
                or task.get("codex_control", {}).get("held")):
            raise ResearchError("task is stopped, held, or its frozen contract differs")
        ensure_live(task)
        allowed = {
            "literature": {"contract", "read", "search", "reuse_passage", "fetch", "literature_ready", "progress"},
            "exploration": {"contract", "read", "progress", "write", "run", "handoff"},
        }
        if self.async_literature:
            allowed["literature"].remove("fetch")
            allowed["exploration"].update({"search", "reuse_passage"})
            if not self.local_literature:
                for phase in allowed:
                    allowed[phase].update({"request_literature", "literature_updates"})
        if self.local_literature:
            allowed["literature"].discard("fetch")
            if action in {"fetch", "request_literature", "literature_updates"}:
                raise ResearchError("this task uses only the selected local literature")
        if research_return.enabled(task):
            allowed["exploration"].add("probe_review")
        if action == "fetch" and self.async_literature:
            raise ResearchError("submit request_literature for an unarchived source under this contract")
        if action not in allowed.get(task["external_stage"], set()):
            raise ResearchError(f"{action} is not available in {task['external_stage']}")
        if self.handoff is not None:
            raise ResearchError("worker handoff already frozen; finish the model turn")
        return task

    def output(self, name, *, exists=False):
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", name)
                or any(part in {".", ".."} or part.endswith(".") for part in name.split("/"))):
            raise ResearchError("output path must be canonical and relative to return/")
        path = self.return_dir / name
        if not path.is_relative_to(self.return_dir) or path.resolve() != path or (exists and not path.is_file()):
            raise ResearchError("output path escaped the task return directory or does not exist")
        return path

    def read(self, p):
        aid = p.get("artifact_id")
        if aid is not None:
            if aid not in self.inputs and aid not in self.sources and aid not in self.contract_files:
                raise ResearchError("read only this task's frozen handoff, inputs or obtained sources")
            with workspace(self.root) as w:
                source = w.store.verify_blob(aid)
            kind = self.sources.get(aid, {}).get("format")
        else:
            self.check("write")  # A return file is available only after release.
            source = self.output(p.get("path"), exists=True)
            if "if_sha256" in p:
                prior = p["if_sha256"]
                if not isinstance(prior, str) or not re.fullmatch(r"[0-9a-f]{64}", prior):
                    raise ResearchError("if_sha256 must be the previously observed SHA-256")
                actual = file_hash(source)
                if prior == actual:
                    return {"path": p["path"], "sha256": actual, "bytes": source.stat().st_size,
                            "unchanged": True, "next_offset": None}
        offset = p.get("offset", 0)
        if type(offset) is not int or offset < 0:
            raise ResearchError("invalid read offset")
        if aid and offset > self.seen.get(aid, 0):
            raise ResearchError("read successive source/input pages without gaps")
        if aid and kind == "html":
            extracted = document_text(source.read_bytes(), kind)
            if offset > len(extracted):
                raise ResearchError("invalid read offset")
            page = extracted[offset:offset + 9001]
        else:
            # Offsets count Unicode characters, as in earlier task contracts.
            # Bounded reads avoid loading the full artifact for every preview.
            with source.open(encoding="utf-8-sig", newline="" if aid else None) as stream:
                remaining = offset
                while remaining:
                    block = stream.read(min(remaining, 65536))
                    if not block:
                        raise ResearchError("invalid read offset")
                    remaining -= len(block)
                page = stream.read(9001)
        size = min(len(page), 9000)
        while len(json.dumps(page[:size], ensure_ascii=False).encode("utf-8")) > VIEW_BYTES - 512:
            size //= 2
        end = offset + size
        complete = size == len(page)
        if aid:
            self.seen[aid] = max(self.seen.get(aid, 0), end)
            if complete:
                self.complete_reads.add(aid)
                if aid in self.inputs:
                    self.read_inputs.add(aid)
        return {"artifact_id": aid, "path": str(source), "text": page[:size], "next_offset": None if complete else end}

    def contract_view(self, *, compact=False):
        reference = {"task": self.tid, "contract_sha256": self.task["contract_artifact"],
                     "prompt_artifact_id": self.task["prompt_artifact"]}
        tools = sorted(set(self.tools) | set(self.expected_tools))
        if compact:
            return {**reference, "tools": tools, "research_continuation": self.contract["research_continuation"],
                    "probe_review_sha256": self.probe_review["sha256"] if self.probe_review else None}
        response = {**reference, "input_json": str(self.directory / "input.json"),
                    "node": self.contract["node"], "method": self.contract.get("method"),
                    "literature": self.contract.get("literature"), "input_ids": sorted(self.inputs),
                    "return_contract": self.contract.get("external_return_contract"),
                    "tools": tools,
                    "prompt": (self.directory / "PROMPT.md").read_text(encoding="utf-8")}
        if "research_continuation" in self.contract:
            response["research_continuation"] = self.contract["research_continuation"]
        if len(json.dumps(response, ensure_ascii=False).encode("utf-8")) <= VIEW_BYTES:
            return response
        return {**reference, "input_ids": sorted(self.inputs)[:32],
                "input_count": len(self.inputs), "tools": response["tools"],
                "instruction": "Use read(artifact_id=contract_sha256) for the exact input.json, and read(artifact_id=prompt_artifact_id) for PROMPT.md. Follow next_offset until each file is complete. Then read every ID in input_artifacts from input.json."}

    def search(self, p):
        q = " ".join(_text(p, "query", 400).split())
        kind = p.get("kind", "scan")
        limit = p.get("limit", 5)
        if kind not in {"scan", "source", "mechanism"} or type(limit) is not int or not 1 <= limit <= 8:
            raise ResearchError("search kind must be scan, source or mechanism, with limit 1..8")
        store = Store(self.library, read_only=True)
        try:
            with store.snapshot():
                sequence = store.db.execute("SELECT MAX(seq) FROM events").fetchone()[0]
                for row in reversed(self.queries):
                    if (row["query"], row["kind"], row["limit"], row["library_sequence"]) == (q, kind, limit, sequence):
                        return row["results"]
                result = scan_records(store, q, limit) if kind == "scan" else rank(store.all(kind), q, limit)
        finally:
            store.close()
        if kind == "scan":
            rows = [self._scan_preview(row) for row in result]
        else:
            rows = [{"id": row["id"], "content": {k: str(v)[:800] for k, v in row["content"].items()
                     if k in {"title", "name", "url", "summary", "locator", "operator", "limitations"}}} for row in result]
        while rows and len(json.dumps(rows, ensure_ascii=False).encode("utf-8")) > VIEW_BYTES - 512:
            rows.pop()
        self.queries.append({"at": now(), "query": q, "kind": kind, "limit": limit,
                             "library_sequence": sequence, "results": rows})
        return rows

    def request_literature(self, p):
        store = Store(self.library)
        try:
            origin = {"workspace": workspace_key(self.root), "route": self.task["route"], "node": self.task["node"],
                      "task": self.tid, "contract_sha256": self.task["contract_artifact"]}
            result = LiteratureInbox(store).submit(p, origin, self.actor)
        finally:
            store.close()
        self.literature_requests.add(result["request_id"])
        return result

    def literature_updates(self, p):
        request_id = _text(p, "request_id", 64) if "request_id" in p else None
        if request_id is not None and request_id not in self.literature_requests:
            raise ResearchError("read only literature requests submitted by this task")
        store = Store(self.library, read_only=True)
        try:
            with store.snapshot():
                rows = LiteratureInbox(store).list(request_id=request_id, workspace=workspace_key(self.root), task=self.tid, limit=8)
                updates = []
                for row in rows:
                    result = row["result"]
                    update = {"request_id": row["id"], "status": row["status"], "result": None}
                    if result:
                        update["result"] = {"outcome": result["outcome"], "summary": result["summary"][:400],
                                            "profile_ids": result["profile_ids"]}
                        if request_id:
                            update["result"]["profiles"] = [self._published_profile(store, key) for key in result["profile_ids"]]
                    updates.append(update)
                return updates
        finally:
            store.close()

    @staticmethod
    def _published_profile(store, key):
        profile = store.get("paper_profile", key)["content"]
        source = store.get("source", profile["source_id"])["content"]
        return {"id": key, "title": source["title"][:160], "summary": profile["summary"][:220],
                "passage_ids": profile["anchors"]}

    @staticmethod
    def _scan_preview(row):
        result = {key: value[:800] if isinstance(value, str) else value for key, value in row.items()}
        if profile := row.get("profile"):
            result["profile"] = {"id": profile["id"], "summary": profile["summary"][:420],
                "limitations": profile["limitations"][:220], "anchors": profile["anchors"],
                **{key: [item[:120] for item in profile[key][:4]] for key in PROFILE_LISTS}}
        if passage := row.get("passage"):
            result["passage"] = {**passage, "locator": passage["locator"][:400]}
        return result

    def reuse_passage(self, p):
        key = _text(p, "passage_id", 64)
        if key in self.sources:
            if self.sources[key].get("mode") != "library_passage":
                raise ResearchError("artifact already has a different source acquisition")
            return {**self.sources[key], **self.read({"artifact_id": key})}
        store = Store(self.library, read_only=True)
        try:
            with store.snapshot():
                row = store.get("passage", key)
                passage = row["content"]
                doc = store.get("document", passage["document"])
                source = store.get("source", doc["content"]["source_id"])
                for record in (row, doc, source):
                    if record["artifact"] != record["id"] or digest(record["content"]) != record["id"]:
                        raise ResearchError("archived passage provenance failed content verification")
                    store.verify_blob(record["artifact"])
                raw = doc["content"]["raw_artifact"]
                origin = source["content"]
                if origin["kind"] == "provider_metadata" and raw == origin.get("raw_artifact"):
                    raise ResearchError("provider response is not an archived original")
                store.verify_blob(raw)
                path = store.verify_blob(row["artifact"])
                size = path.stat().st_size
                if not self.async_literature and self.source_bytes + size > SOURCE_TOTAL_BYTES:
                    raise ResearchError("source acquisition byte budget exhausted")
                self.check("reuse_passage")
                with workspace(self.root, read_only=False) as w:
                    aid = w.apply("artifact.import", {"path": str(path), "purpose": f"{self.tid} selected archived original passage"}, self.actor)["id"]
                self.source_bytes += size
                self.sources[aid] = {"title": origin["title"], "url": origin["url"], "version": origin["version"], "bytes": size,
                    "format": "text", "mode": "library_passage", "document": doc["id"], "raw_artifact": raw,
                    "source_id": source["id"], "locator": passage["locator"],
                    "origin": passage.get("origin", "indexed_original")}
        finally:
            store.close()
        return {**self.sources[aid], **self.read({"artifact_id": aid})}

    def fetch(self, p):
        url = _text(p, "url", 1600)
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ResearchError("fetch requires an HTTPS original-source URL")
        for aid, source in self.sources.items():
            if source["url"] == url and source.get("mode") == "network":
                return {**source, **self.read({"artifact_id": aid})}
        if self.fetches >= SOURCE_REQUESTS or self.source_bytes >= SOURCE_TOTAL_BYTES:
            raise ResearchError("source acquisition budget exhausted")
        self.fetches += 1
        cap = min(SOURCE_BYTES, SOURCE_TOTAL_BYTES - self.source_bytes)
        path = self.live / "sources" / f"fetch-{self.fetches:02d}.raw"
        path.parent.mkdir(exist_ok=True)
        with build_opener(HTTPSRedirect()).open(Request(url, headers={"User-Agent": "ai4math/1.0"}), timeout=45) as response, path.open("xb") as out:
            mime = response.headers.get_content_type()
            observed = 0
            while True:
                block = response.read(min(65536, cap + 1 - observed))
                if not block:
                    break
                observed += len(block)
                self.source_bytes += len(block)
                out.write(block)
                if observed > cap:
                    raise ResearchError("original source exceeds per-source or task acquisition limit; partial bytes retained")
        kind = "html" if mime == "text/html" else "text" if mime.startswith("text/") else None
        if kind is None:
            raise ResearchError("non-text original captured; supply a frozen text passage in the task inputs")
        self.check("fetch")
        with workspace(self.root, read_only=False) as w:
            aid = w.apply("artifact.import", {"path": str(path), "purpose": f"{self.tid} independently fetched original {url}"}, self.actor)["id"]
        self.sources[aid] = {"url": url, "format": kind, "bytes": observed, "mode": "network"}
        return {**self.sources[aid], **self.read({"artifact_id": aid})}

    def literature_ready(self, p):
        if self.inputs != self.read_inputs:
            raise ResearchError("read all frozen inputs first: " + ", ".join(sorted(self.inputs - self.read_inputs)))
        if not self.queries:
            raise ResearchError("independent worker search is required")
        sources = p.get("sources")
        if not isinstance(sources, list) or any(not isinstance(s, dict) or s.get("artifact_id") not in self.seen for s in sources):
            raise ResearchError("cite only originals or frozen passages actually read by the worker")
        for source in sources:
            for key in ("url", "locator", "finding", "artifact_id"):
                _text(source, key)
            fetched = self.sources.get(source["artifact_id"])
            if fetched and fetched["url"] != source["url"]:
                raise ResearchError("source citation URL differs from the captured acquisition")
            if fetched and fetched.get("mode") == "library_passage" and fetched["locator"] != source["locator"]:
                raise ResearchError("source citation locator differs from the archived passage")
        drafts = p.get("profile_drafts", [])
        cited = {source["artifact_id"] for source in sources}
        if not isinstance(drafts, list) or len(drafts) > 8:
            raise ResearchError("profile_drafts must be a bounded list")
        for draft in drafts:
            if not isinstance(draft, dict) or set(draft) != {"artifact_id", *PROFILE_FIELDS}:
                raise ResearchError("profile draft needs an artifact_id and the structured study fields")
            if _text(draft, "artifact_id", 64) not in cited:
                raise ResearchError("profile draft must reference a cited original read by this worker")
            check_profile_fields(draft)
        path = self.live / f"literature-{uuid4().hex}.json"
        data = {"task": self.tid, "contract_sha256": self.task["contract_artifact"], "searches": self.queries,
                "read_inputs": sorted(self.read_inputs), "acquisitions": self.sources, "source_bytes": self.source_bytes,
                "network_requests": self.fetches, "profile_drafts": drafts,
                "literature_request_ids": sorted(self.literature_requests)}
        _write_new(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        with workspace(self.root, read_only=False) as w:
            trace = w.apply("artifact.import", {"path": str(path), "purpose": "Independent worker literature search and original-source trace"}, self.actor)
            result = w.apply("task.literature_ready", {"task": self.tid, "contract_sha256": self.task["contract_artifact"],
                "read_input_artifacts": sorted(self.read_inputs), "queries": [row["query"] for row in self.queries],
                "sources": sources, "search_artifact": trace["id"], "limitations": _text(p, "limitations"),
                "exploration_plan": _text(p, "exploration_plan")}, self.actor)
        return {"stage": result["external_stage"], "worker_receipt_sha256": result["worker_receipt_sha256"],
                "instruction": "Finish this turn now; the coordinator will review the receipt."}

    def run(self, p, capture):
        if (research_return.deduction_first(self.task) and self.probe_review
                and self.probe_review["checkpoint"]["review"]["target_disposition"] == "limited"):
            if self.derivation is None:
                raise ResearchError("record an actual inference or obstruction in progress.derivation before more computation")
            _text(p, "decision", 2000)
        argv = p.get("argv")
        if not isinstance(argv, list) or not 2 <= len(argv) <= 64 or any(not isinstance(x, str) or not 0 < len(x) <= 16000 for x in argv):
            raise ResearchError("run needs a bounded argv array")
        name = argv[0]
        exe = self.tools.get(name)
        if not exe:
            entry = self.expected_tools.get(name)
            if not isinstance(entry, dict):
                raise ResearchError("tool is absent from the frozen toolchain")
            path = Path(entry["path"]).resolve(strict=True)
            if not Path(entry["path"]).is_absolute() or file_hash(path) != entry["sha256"]:
                raise ResearchError("tool executable differs from the frozen toolchain")
            exe = str(path)
        actual = [exe, "-B", *argv[1:]] if name == "python" else [exe, *argv[1:]]
        output = None
        if "output_path" in p:
            name = p["output_path"]
            if name in (research_return.RETURN, research_return.CHECKPOINT):
                raise ResearchError("output_path cannot replace a broker record")
            output = self.output(name)
            if output.exists():
                raise ResearchError("output_path already exists; choose a new return-relative path")
        self.return_dir.mkdir(exist_ok=True)
        _write_new(capture / "argv.json", json.dumps(actual, ensure_ascii=False).encode("utf-8"))
        observed = 0
        with subprocess.Popen(actual, cwd=self.return_dir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc, (capture / "output.raw").open("xb") as raw:
            self.current_process = proc
            try:
                while block := proc.stdout.read1(65536):
                    observed += len(block)
                    limit = self.task["output_limit_bytes"]
                    raw.write(block if limit is None else block[:max(0, limit - self.tool_bytes)])
                    self.tool_bytes += len(block)
                    if limit is not None and self.tool_bytes > limit:
                        proc.kill()
                        raise ResearchError("worker tool output limit exceeded")
                status = proc.wait()
            finally:
                self.current_process = None
        raw_path = capture / "output.raw"
        sha = file_hash(raw_path)
        with raw_path.open("rb") as stream:
            data = stream.read(9000)
        result = {"exit_code": status, "bytes": observed, "sha256": sha,
                  "output": data.decode("utf-8", errors="replace"), "truncated": observed > 9000}
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            with raw_path.open("rb") as source:
                target = output.open("xb")
                try:
                    with target:
                        shutil.copyfileobj(source, target)
                    if file_hash(output) != sha:
                        raise ResearchError("published tool output differs from the raw capture")
                except (OSError, ResearchError):
                    output.unlink()
                    raise
            self.written[p["output_path"]] = sha
            result["output_path"] = p["output_path"]
        return result

    def record_probe_review(self, review):
        if self.probe_review is not None:
            raise ResearchError("first probe review already frozen; carry out the recorded follow-up")
        research_return.check_probe(review)
        probe_artifacts = {name: file_hash(self.output(name, exists=True))
                           for name in review["first_probe"]["artifacts"]}
        checkpoint = {"task_id": self.tid, "contract_sha256": self.task["contract_artifact"], "recorded_at": now(),
                      "review": review, "probe_artifacts": probe_artifacts}
        path = self.output(research_return.CHECKPOINT)
        _write_new(path, json.dumps(checkpoint, ensure_ascii=False).encode("utf-8"))
        self.probe_review = {"sha256": file_hash(path), "prior_writes": self.written.copy(), "checkpoint": checkpoint}
        return {"probe_review_sha256": self.probe_review["sha256"],
                "target_disposition": review["target_disposition"]}

    def continuation_handoff(self, payload):
        if self.probe_review is None:
            raise ResearchError("handoff requires a frozen probe_review")
        review = self.probe_review
        checkpoint = review["checkpoint"]
        names = research_return.artifact_names(payload.get("paths"))
        paths = {name: self.output(name, exists=True) for name in names}
        hashes = {name: file_hash(path) for name, path in paths.items()}
        record = payload.get("return_record")
        deductive = research_return.deduction_first(self.task)
        research_return.check_return(checkpoint["review"], record, deductive=deductive)
        follow_up = record["follow_up"]
        fresh = None
        if follow_up:
            prior = set(checkpoint["probe_artifacts"].values())
            fresh = next((name for name in follow_up["artifacts"] if name in hashes
                          and name not in review["prior_writes"] and self.written.get(name) == hashes[name]
                          and hashes[name] not in prior), None)
        checkpoint_path = self.output(research_return.CHECKPOINT, exists=True)
        hashes[research_return.CHECKPOINT] = file_hash(checkpoint_path)
        if hashes[research_return.CHECKPOINT] != review["sha256"]:
            raise ResearchError("first probe review bytes changed")
        bindings = self.contract["external_return_contract"]["bindings"]
        result = {**record, **bindings, "contract_sha256": self.task["contract_artifact"],
                  "artifacts": list(hashes), "first_probe": checkpoint["review"]["first_probe"],
                  "probe_review_sha256": review["sha256"], "follow_up_written_after_review": fresh}
        if deductive:
            sources = {}
            for row in record["source_checks"]:
                aid = row["passage_id"]
                if aid is None:
                    continue
                source = self.sources.get(aid, {})
                if source.get("mode") != "library_passage" or aid not in self.complete_reads:
                    raise ResearchError("source check must bind a completely read archived passage")
                fields = ("title", "url", "version", "locator", "source_id", "document", "raw_artifact", "origin")
                sources[aid] = {"passage_id": aid, **{field: source[field] for field in fields}}
            result.update(derivation=self.derivation, source_passages=sources)
        research_return.check_bound_return(self.task, result, checkpoint, hashes)
        raw = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
        limit = self.task["output_limit_bytes"]
        if limit is not None and len(raw) > limit:
            raise ResearchError("return record exceeds the task output limit")
        path = self.output(research_return.RETURN)
        _write_new(path, raw)
        hashes[research_return.RETURN] = file_hash(path)
        self.handoff = {"task": self.tid, "contract_sha256": self.task["contract_artifact"],
                        "artifacts": [{"path": name, "sha256": sha} for name, sha in hashes.items()]}
        return {"prepared": True, "instruction": "End the turn. The adapter will check capture and seal the batch."}

    def execute(self, action, p, capture):
        task = self.check(action)
        continuation = research_return.enabled(task)
        if action == "contract":
            return self.contract_view(compact=continuation and task["external_stage"] == "exploration")
        if action in {"read", "search", "reuse_passage", "fetch", "request_literature", "literature_updates", "literature_ready"}:
            return getattr(self, action)(p)
        if action == "progress":
            result = {k: _text(p, k, 1200) for k in ("summary", "next_step", "bridge")}
            result["node"] = self.task["node"]
            artifacts = p.get("artifacts", [])
            if not isinstance(artifacts, list) or len(artifacts) > 8 or any(not isinstance(x, str) for x in artifacts):
                raise ResearchError("progress artifacts must be a short list of IDs or return paths")
            result["artifacts"] = [entry if entry in self.inputs or entry in self.sources
                                   else str(self.output(entry, exists=True).relative_to(self.directory).as_posix())
                                   for entry in artifacts]
            if "ack" in p:
                result["ack"] = _text(p, "ack", 100)
            if research_return.deduction_first(task) and "derivation" in p:
                if not self.probe_review or self.probe_review["checkpoint"]["review"]["target_disposition"] != "limited":
                    raise ResearchError("progress.derivation follows a limited probe_review")
                research_return.check_derivation(p["derivation"])
                self.derivation = p["derivation"].copy()
                result["derivation_recorded"] = True
            return result
        if action == "write":
            if continuation and p.get("path") in (research_return.RETURN, research_return.CHECKPOINT):
                raise ResearchError("the broker writes return.json and probe-review.json after validation")
            path = self.output(p.get("path"))
            raw = _text(p, "text", self.task["output_limit_bytes"]).encode("utf-8")
            if self.task["output_limit_bytes"] is not None and len(raw) > self.task["output_limit_bytes"]:
                raise ResearchError("file exceeds task output limit")
            _write_new(path, raw)
            sha = file_hash(path)
            self.written[p["path"]] = sha
            return {"path": p["path"], "sha256": sha}
        if action == "run":
            return self.run(p, capture)
        if action == "probe_review":
            return self.record_probe_review(p)
        if action == "handoff":
            if continuation:
                return self.continuation_handoff(p)
            names = p.get("paths")
            if not isinstance(names, list) or not 2 <= len(names) <= 100 or any(not isinstance(x, str) for x in names) or len(set(names)) != len(names) or "return.json" not in names:
                raise ResearchError("handoff needs return.json plus unique output paths")
            paths = {name: self.output(name, exists=True) for name in names}
            record = read_json(paths["return.json"])
            requirements = self.contract["external_return_contract"]
            if (not isinstance(record, dict) or any(record.get(k) != v for k, v in requirements["bindings"].items())
                    or any(k not in record for k in requirements["required_fields"])):
                raise ResearchError("return.json must bind the exact contract and all required fields")
            probe = record.get("first_probe")
            if not isinstance(probe, dict) or any(not isinstance(probe.get(k), str) or not probe[k].strip() for k in ("test", "outcome", "scope")):
                raise ResearchError("return.json first_probe needs test, outcome, and scope")
            links = probe.get("artifacts")
            if not isinstance(links, list) or not links or any(not isinstance(x, str) or x not in paths or x == "return.json" for x in links):
                raise ResearchError("first_probe must name its output evidence paths")
            if record["suggested_evidence_level"] not in {"unknown", "finite_support"}:
                raise ResearchError("producer handoff may suggest unknown or finite_support")
            self.handoff = {"task": self.tid, "contract_sha256": self.task["contract_artifact"],
                            "artifacts": [{"path": n, "sha256": file_hash(path)} for n, path in paths.items()]}
            return {"prepared": True, "instruction": "End the turn. The adapter will check capture and seal the batch."}
        raise ResearchError("unknown worker action")
