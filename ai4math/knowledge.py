"""Versioned academic sources and mechanism cards; retrieval is not validation.

Uses the same append-only event/immutable-byte primitives in a separate library.
Network discovery is explicit, bounded, metadata-only and never runs source code.
"""
from __future__ import annotations

import collections
import html
import json
import math
import re
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from importlib.resources import files
from html.parser import HTMLParser
from pathlib import Path

from .store import ResearchError, Store, digest, encode, now

CAPABILITIES = {"representation", "bridge_lemma", "construction", "discriminating_probe", "branch_portfolio"}
SOURCE_KINDS = {"paper", "repository", "researcher_guidance", "venue", "provider_metadata"}


def text_fields(value, *names):
    if not isinstance(value, dict):
        raise ResearchError("record must be an object")
    for name in names:
        if not isinstance(value.get(name), str) or not value[name].strip() or len(value[name]) > 20000:
            raise ResearchError(f"nonempty bounded text required: {name}")


def strings(value, name, minimum=0, maximum=32):
    result = value.get(name)
    if (not isinstance(result, list) or not minimum <= len(result) <= maximum
            or any(not isinstance(x, str) or not x.strip() or len(x) > 4000 for x in result)):
        raise ResearchError(f"bounded text list required: {name}")
    return result


def count(value, low=1, high=20):
    if type(value) is not int or not low <= value <= high:
        raise ResearchError(f"integer must be in {low}..{high}")
    return value


def tokens(value):
    value = value.casefold()
    result = re.findall(r"[a-z0-9_]+", value)
    for run in re.findall(r"[\u3400-\u9fff]+", value):
        result.extend(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return result


def rank(records, query, limit=5, capability=None):
    """Deterministic lexical ranking, including Chinese bigrams; not embeddings."""
    count(limit)
    text_fields({"query": query}, "query")
    if capability is not None and capability not in CAPABILITIES:
        raise ResearchError("unknown exploration capability")
    rows = [r for r in records if capability is None or capability in r["content"].get("capabilities", [])]
    return _rank(rows, query)[:limit]


def _rank(rows, query):
    words = set(tokens(query))
    documents = [collections.Counter(tokens(encode(r["content"]))) for r in rows]
    frequencies = collections.Counter(w for doc in documents for w in doc)
    scored = []
    for row, doc in zip(rows, documents):
        score = sum(math.log1p(len(rows) / (1 + frequencies[w])) * (1 + math.log(doc[w]))
                    for w in words if doc[w])
        if score:
            scored.append({**row, "retrieval_score": round(score, 6), "retrieval_method": "lexical-bilingual-v1"})
    return sorted(scored, key=lambda r: (-r["retrieval_score"], r["id"]))


PROFILE_LISTS = ("contributions", "fields", "keywords", "methods", "assumptions")
PROFILE_FIELDS = ("summary", *PROFILE_LISTS, "limitations")


def check_profile_fields(content):
    text_fields(content, "summary", "limitations")
    if len(content["summary"]) > 1200 or len(content["limitations"]) > 800:
        raise ResearchError("profile summary or limitations exceed the scan limit")
    for key in PROFILE_LISTS:
        if any(len(value) > 300 for value in strings(content, key, 1, 8)):
            raise ResearchError(f"profile {key} item exceeds 300 characters")


def work_key(url):
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    if host in {"doi.org", "dx.doi.org"}:
        return ("doi", urllib.parse.unquote(parts.path).strip("/").casefold())
    if host in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        match = re.fullmatch(r"/(?:abs|pdf|html)/(.+?)(?:v\d+)?(?:\.pdf)?/?", parts.path)
        if match:
            return ("arxiv", match[1])
    return ("url", url)


def current_profiles(store):
    rows = store.all("paper_profile")
    superseded = {row["content"].get("supersedes") for row in rows}
    return {row["content"]["document"]: row for row in rows if row["id"] not in superseded}


def scan_records(store, query, limit=5):
    """Scan all searchable layers in one snapshot, with one best passage per document."""
    count(limit)
    text_fields({"query": query}, "query")
    sources = {row["id"]: row for row in store.all("source")}
    documents = {row["id"]: row for row in store.all("document")}
    profiles = current_profiles(store)
    matches = {}
    for kind, fields in {
        "source": ("title", "summary", "tags"),
        "passage": ("text",),
        "paper_profile": PROFILE_FIELDS,
        "mechanism": ("name", "bottleneck", "operator", "bridge", "conditions", "failure_modes", "tags"),
    }.items():
        rows = store.all(kind) if kind not in {"source", "paper_profile"} else (
            sources.values() if kind == "source" else profiles.values())
        projected = [{"id": row["id"], "row": row, "content": {key: row["content"][key]
                      for key in fields if key in row["content"]}} for row in rows]
        matches[kind] = _rank(projected, query)
    by_document = {}
    for kind in ("passage", "paper_profile"):
        for match in matches[kind]:
            document = match["row"]["content"]["document"]
            by_document.setdefault(document, {}).setdefault(kind, match)
    by_work = {}
    by_source = {}
    for match in matches["source"]:
        by_work.setdefault(work_key(match["row"]["content"]["url"]), match["retrieval_score"])
        by_source[match["id"]] = match["retrieval_score"]
    doc_keys = {key: work_key(sources[row["content"]["source_id"]]["content"]["url"])
                for key, row in documents.items()}
    direct = {doc_keys[key] for key, row in documents.items()
              if key in by_document or row["content"]["source_id"] in by_source}
    preferred = {}
    for key in sorted(documents, key=lambda doc: (doc not in profiles, doc)):
        preferred.setdefault(doc_keys[key], key)
    covered_works = set()
    results = []
    for document_id, row in documents.items():
        sid = row["content"]["source_id"]
        source = sources[sid]["content"]
        hits = by_document.get(document_id, {})
        key = doc_keys[document_id]
        score = max([by_source.get(sid, 0), *(m["retrieval_score"] for m in hits.values())])
        if key not in direct and preferred[key] == document_id:
            score = by_work.get(key, 0)
        if not score:
            continue
        profile = profiles.get(document_id)
        passage = hits.get("passage", {}).get("row")
        if passage is None and profile:
            passage = store.get("passage", profile["content"]["anchors"][0])
        covered_works.add(key)
        results.append({"id": document_id, "kind": "document", "source_id": sid, "retrieval_score": score,
            "title": source["title"], "url": source["url"], "version": source["version"],
            "study_status": "profiled" if profile else "profile_pending",
            "profile": {"id": profile["id"], **profile["content"]} if profile else None,
            "passage": {"id": passage["id"], "locator": passage["content"]["locator"],
                        "preview": passage["content"]["text"][:300]} if passage else None})
    for match in matches["source"]:
        row = match["row"]
        source = row["content"]
        key = work_key(source["url"])
        if key in covered_works:
            continue
        covered_works.add(key)
        results.append({"id": row["id"], "kind": "source", "study_status": "source_record_only",
            "retrieval_score": match["retrieval_score"], **{k: source[k] for k in ("title", "url", "version", "summary", "locator")}})
    for match in matches["mechanism"]:
        row = match["row"]
        results.append({"id": row["id"], "kind": "mechanism", "retrieval_score": match["retrieval_score"],
            **{k: row["content"][k] for k in ("name", "operator", "limitations", "source_ids")}})
    return sorted(results, key=lambda row: (-row["retrieval_score"], row["id"]))[:limit]


def document_text(raw, format):
    if format == "pdf":
        return ""
    text = raw.decode("utf-8-sig")
    if format != "html":
        return text

    class Extract(HTMLParser):
        def __init__(self):
            super().__init__()
            self.skip = 0
            self.parts = []

        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style"}:
                self.skip += 1

        def handle_endtag(self, tag):
            if tag in {"script", "style"}:
                self.skip = max(0, self.skip - 1)

        def handle_data(self, data):
            if not self.skip:
                self.parts.append(data)

    parser = Extract()
    parser.feed(text)
    return "\n".join(parser.parts)


class AcademicLibrary:
    def __init__(self, root):
        self.store = Store(root)
        try:
            self.store.get("academic_library", "main")
        except BaseException:
            self.store.close()
            raise

    @classmethod
    def create(cls, root, max_remote_requests=100):
        count(max_remote_requests, high=10000)
        if Path(root).exists():
            raise ResearchError("library init requires a new directory")
        store = Store(root, create=True)
        try:
            with store.transaction("knowledge.init", "operator"):
                store.put("academic_library", "main", {"schema": 1, "max_remote_requests": max_remote_requests,
                          "created_at": now(), "trust": "source_and_method_index_not_proof"}, new=True)
        finally:
            store.close()
        return cls(root)

    def _healthy(self):
        audit = self.audit()
        if not audit["passed"]:
            raise ResearchError("academic library integrity failed: " + "; ".join(audit["errors"]))

    def _blob(self, data):
        folder = self.store.inside("objects/.incoming")
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as f:
            f.write(data)
            path = Path(f.name)
        try:
            frozen = self.store.freeze(path)
        finally:
            path.unlink(missing_ok=True)
        return self._register_blob(frozen)

    def _register_blob(self, frozen):
        if not self.store.exists("artifact", frozen["id"]):
            self.store.put("artifact", frozen["id"], {**frozen, "purpose": "academic library immutable bytes"}, new=True)
        return frozen["id"]

    def _add(self, kind, content, actor):
        if len(encode(content).encode("utf-8")) > 256 * 1024:
            raise ResearchError("academic record exceeds 256 KiB")
        key = digest(content)
        with self.store.transaction("knowledge." + kind, actor):
            self._healthy()
            return self._put_content(kind, content, actor)

    def _put_content(self, kind, content, actor):
        key = digest(content)
        try:
            return self.store.get(kind, key)
        except ResearchError:
            pass
        artifact = self._blob(encode(content).encode("utf-8"))
        row = {"id": key, "content": content, "artifact": artifact, "added_at": now(), "actor": actor}
        self.store.put(kind, key, row, new=True)
        return row

    def add_document(self, source_id, path, format="text", actor="operator"):
        """Archive the entire original; index small text or explicit passages."""
        source = self.store.get("source", source_id)
        if format not in {"text", "html", "pdf"}:
            raise ResearchError("document format must be text, html or pdf")
        frozen = self.store.freeze(path)
        archived = self.store.blob(frozen["id"])
        extracted = False
        text = ""
        if format == "pdf":
            with archived.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise ResearchError("declared PDF has no PDF header")
            limitations = "PDF retained only"
        else:
            # Keep indexing bounded independently of archival storage. Preserve
            # the prior extraction and IDs for documents that fit the index.
            if frozen["size"] <= 8 * 1024 * 1024:
                text = document_text(archived.read_bytes(), format)
                extracted = len(text) <= 200000
            if not extracted:
                text = ""
            limitations = ("Text extraction is not mathematical transcription validation" if extracted else
                           "Original retained in full; use add-passage to index selected text with an exact locator")
        chunks = [(i, text[i:i + 2048]) for i in range(0, len(text), 1920)]
        with self.store.transaction("knowledge.document", actor):
            self._healthy()
            raw_id = self._register_blob(frozen)
            doc = self._put_content("document", {"source_id": source_id, "raw_artifact": raw_id, "format": format,
                "text_extracted": extracted, "passage_count": len(chunks), "evidence_level": "unknown",
                "limitations": limitations}, actor)
            passages = [self._put_content("passage", {"source_id": source_id, "document": doc["id"], "ordinal": n,
                "text": chunk, "locator": f"extracted character range {start}:{start+len(chunk)}; " + source["content"]["locator"],
                "evidence_level": "unknown"}, actor)["id"] for n, (start, chunk) in enumerate(chunks)]
        return {"document": doc["id"], "raw_artifact": raw_id, "passages": passages, "text_extracted": extracted}

    def add_source(self, content, actor="operator"):
        content = json.loads(encode(content))
        text_fields(content, "title", "url", "kind", "version", "locator", "summary", "limitations")
        if content["kind"] not in SOURCE_KINDS:
            raise ResearchError("unsupported academic source kind")
        url = urllib.parse.urlsplit(content["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ResearchError("source needs a credential-free HTTPS URL")
        strings(content, "tags")
        if content.get("evidence_level", "unknown") != "unknown":
            raise ResearchError("source registration never certifies a mathematical claim")
        if content.get("raw_artifact"):
            self.store.verify_blob(content["raw_artifact"])
        content["evidence_level"] = "unknown"
        return self._add("source", content, actor)

    def add_passage(self, document, locator, text, actor="operator"):
        """Record an operator-supplied transcription with its exact original."""
        content = {"locator": locator, "text": text}
        text_fields(content, "locator", "text")
        if len(locator) > 500 or len(text) > 4000:
            raise ResearchError("selected passage exceeds locator or text limit")
        doc = self.store.get("document", document)["content"]
        return self._add("passage", {**content, "document": document, "source_id": doc["source_id"],
                                     "origin": "supplied_transcription", "evidence_level": "unknown"}, actor)

    def add_profile(self, content, actor="operator"):
        required = {"document", *PROFILE_FIELDS, "anchors"}
        if not isinstance(content, dict) or not required <= content.keys() or content.keys() - required - {"supersedes"}:
            raise ResearchError("profile requires a document, summary, contributions, fields, keywords, methods, assumptions, limitations and passage anchors")
        text_fields(content, "document")
        check_profile_fields(content)
        anchors = strings(content, "anchors", 1, 8)
        if len(set(anchors)) != len(anchors):
            raise ResearchError("profile passage anchors must be distinct")
        with self.store.transaction("knowledge.paper_profile", actor):
            self._healthy()
            doc = self.store.get("document", content["document"])["content"]
            source = self.store.get("source", doc["source_id"])["content"]
            if doc["raw_artifact"] == source.get("raw_artifact") and source["kind"] == "provider_metadata":
                raise ResearchError("provider response bytes cannot serve as the original paper")
            for anchor in anchors:
                passage = self.store.get("passage", anchor)["content"]
                if passage["document"] != content["document"]:
                    raise ResearchError("profile passage must belong to the exact document version")
            content = {**content, "source_id": doc["source_id"], "raw_artifact": doc["raw_artifact"],
                       "reading_scope": "selected_passages", "evidence_level": "unknown"}
            key = digest(content)
            if self.store.exists("paper_profile", key):
                return self.store.get("paper_profile", key)
            prior = current_profiles(self.store).get(content["document"])
            if content.get("supersedes") != (prior["id"] if prior else None):
                raise ResearchError("profile revision must supersede the current profile for this document")
            return self._put_content("paper_profile", content, actor)

    def review_queue(self, limit=20):
        count(limit, high=100)
        with self.store.snapshot():
            self._healthy()
            sources = {row["id"]: row["content"] for row in self.store.all("source")}
            documents = self.store.all("document")
            profiled = current_profiles(self.store)
            readable = {row["content"]["document"] for row in self.store.all("passage")}
            queue = []
            for doc in documents:
                if doc["id"] in profiled:
                    continue
                source = sources[doc["content"]["source_id"]]
                queue.append({"document": doc["id"], "source_id": doc["content"]["source_id"],
                              "title": source["title"], "url": source["url"], "version": source["version"],
                              "status": "needs_profile" if doc["id"] in readable else "needs_passage"})
            queue.sort(key=lambda row: (row["status"] != "needs_profile", row["title"], row["document"]))
            archived = {work_key(sources[row["content"]["source_id"]]["url"]) for row in documents}
            return {"total_pending_documents": len(queue), "profiled_documents": len(profiled),
                    "source_works_without_document": len({work_key(row["url"]) for row in sources.values()} - archived),
                    "items": queue[:limit]}

    def add_card(self, content, actor="operator"):
        content = json.loads(encode(content))
        text_fields(content, "name", "bottleneck", "operator", "bridge", "prediction", "limitations")
        capabilities = strings(content, "capabilities", 1, 5)
        if not set(capabilities) <= CAPABILITIES:
            raise ResearchError("unknown exploration capability")
        for name in ("source_ids", "conditions", "failure_modes", "tags"):
            strings(content, name, 1)
        for key in content["source_ids"]:
            self.store.get("source", key)
        if content.get("evidence_level", "unknown") != "unknown":
            raise ResearchError("mechanism transfer remains unknown until independently established")
        content["evidence_level"] = "unknown"
        return self._add("mechanism", content, actor)

    def seed(self, actor="operator"):
        payload = json.loads(files("ai4math").joinpath("academic_seed.json").read_text(encoding="utf-8"))
        mapping = {p["key"]: self.add_source(p, actor=actor)["id"] for p in payload["sources"]}
        cards = []
        for item in payload["mechanisms"]:
            content = {k: v for k, v in item.items() if k != "source_keys"}
            content["source_ids"] = [mapping[key] for key in item["source_keys"]]
            cards.append(self.add_card(content, actor=actor)["id"])
        return {"sources": mapping, "mechanisms": cards, "seed_version": payload["version"]}

    def search(self, query, kind="mechanism", limit=5, capability=None):
        if kind not in {"source", "mechanism", "passage", "scan"}:
            raise ResearchError("search kind must be source, mechanism, passage or scan")
        with self.store.snapshot():
            self._healthy()
            if kind == "scan":
                if capability is not None:
                    raise ResearchError("capability filter requires a typed search")
                return scan_records(self.store, query, limit)
            return rank(self.store.all(kind), query, limit, capability)

    def audit(self):
        with self.store.snapshot():
            result = self.store.audit()
            for kind in ("source", "mechanism", "document", "passage", "paper_profile"):
                for row in self.store.all(kind):
                    if digest(row["content"]) != row["id"] or row["artifact"] != row["id"]:
                        result["errors"].append(f"academic identity mismatch: {row['id']}")
            indexed = collections.Counter(p["content"]["document"] for p in self.store.all("passage")
                                          if "ordinal" in p["content"])
            for document in self.store.all("document"):
                if indexed[document["id"]] != document["content"]["passage_count"]:
                    result["errors"].append("incomplete document text index: " + document["id"])
            result["passed"] = not result["errors"]
            result["sources"] = len(self.store.all("source"))
            result["mechanisms"] = len(self.store.all("mechanism"))
            result["remote_requests"] = len(self.store.all("discovery"))
            result["documents"] = len(self.store.all("document"))
            result["passages"] = len(self.store.all("passage"))
            result["profiles"] = len(self.store.all("paper_profile"))
            return result

    def discover(self, provider, query, limit=5, timeout=20, *, refresh=False, actor="operator"):
        """One fixed-provider request, persisted before I/O. Failed attempts count."""
        count(limit)
        count(timeout, high=30)
        text_fields({"query": query}, "query")
        if len(query) > 500 or provider not in {"crossref", "arxiv"}:
            raise ResearchError("bounded query and crossref/arxiv provider required")
        with self.store.transaction("knowledge.discover.reserve", actor):
            self._healthy()
            previous = self.store.all("discovery")
            if not refresh:
                for row in reversed(previous):
                    if (row["status"] == "completed" and row["provider"] == provider and row["limit"] == limit
                            and " ".join(row["query"].split()) == " ".join(query.split())):
                        return {**row, "reused": True}
            if any(r["status"] == "pending" for r in previous):
                raise ResearchError("unfinished discovery; explicitly recover it before another request")
            if len(previous) >= self.store.get("academic_library", "main")["max_remote_requests"]:
                raise ResearchError("library remote-request budget exhausted")
            if provider == "arxiv" and any(time.time() - r["started_epoch"] < 3 for r in previous):
                raise ResearchError("arXiv requests require at least three seconds spacing")
            key = f"request-{len(previous) + 1:06}"
            request = {"id": key, "provider": provider, "query": query, "limit": limit,
                       "status": "pending", "started_epoch": time.time(), "started_at": now(), "actor": actor}
            self.store.put("discovery", key, request, new=True)
        admitted = []
        try:
            raw, url = fetch_metadata(provider, query, limit, timeout)
            with self.store.transaction("knowledge.discover.raw", actor):
                raw_id = self._blob(raw)
            request.update(raw_artifact=raw_id, request_url=url)
            candidates = parse_metadata(provider, raw, limit)
            for item in candidates:
                admitted.append(self.add_source({**item, "raw_artifact": raw_id}, actor=actor)["id"])
            request.update(status="completed", raw_artifact=raw_id, source_ids=admitted, request_url=url)
        except Exception as exc:
            request.update(status="failed", error=f"{type(exc).__name__}: {exc}", source_ids=admitted)
        with self.store.transaction("knowledge.discover.finish", actor):
            request["finished_at"] = now()
            self.store.put("discovery", key, request)
        return request

    def recover(self, request_id, reason, actor="operator"):
        text_fields({"reason": reason}, "reason")
        with self.store.transaction("knowledge.discover.recover", actor):
            self._healthy()
            row = self.store.get("discovery", request_id)
            if row["status"] != "pending":
                raise ResearchError("only pending discovery can be recovered")
            row.update(status="interrupted", recovery_reason=reason, finished_at=now())
            self.store.put("discovery", request_id, row)
            return row


def fetch_metadata(provider, query, limit, timeout):
    if provider == "crossref":
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode({"query.bibliographic": query, "rows": limit})
    else:
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": query, "start": 0, "max_results": limit})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ResearchError("metadata redirect refused; no implicit host changes")
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "ai4math/0.4 academic-metadata-research", "Accept-Encoding": "identity"})
    started = time.monotonic()
    with opener.open(request, timeout=timeout) as response:
        chunks, size = [], 0
        while True:
            if time.monotonic() - started > timeout:
                raise ResearchError("metadata overall time budget exceeded")
            block = response.read(65536)
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > 2 * 1024 * 1024:
                raise ResearchError("metadata response exceeds 2 MiB")
        return b"".join(chunks), url


def parse_metadata(provider, raw, limit):
    records = []
    if provider == "crossref":
        data = json.loads(raw)
        items = data["message"]["items"][:limit]
        for item in items:
            title = " ".join(item.get("title", []))
            doi = item.get("DOI", "")
            if not title or not doi:
                continue
            records.append({"title": title, "url": "https://doi.org/" + doi, "kind": "provider_metadata",
                "version": "Crossref deposit retrieved " + now(), "locator": "Crossref record DOI " + doi,
                "summary": html.unescape(re.sub(r"<[^>]+>", " ", item.get("abstract", title)))[:12000],
                "limitations": "Provider metadata only; full text, hypotheses, novelty and correctness not checked.",
                "tags": [item.get("type", "work"), *item.get("subject", [])[:8]], "doi": doi,
                "authors": [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in item.get("author", [])[:32]]})
    else:
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ResearchError("XML entities are not accepted")
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in ET.fromstring(raw).findall("a:entry", ns)[:limit]:
            get = lambda key: " ".join((entry.findtext("a:" + key, "", ns)).split())
            url = get("id").replace("http://", "https://", 1)
            if not url.startswith("https://arxiv.org/abs/"):
                raise ResearchError("unexpected arXiv entry identity")
            records.append({"title": get("title"), "url": url, "kind": "provider_metadata",
                "version": get("updated"), "locator": "Atom entry " + url,
                "summary": get("summary")[:12000] or get("title"),
                "limitations": "arXiv metadata only; posting is not peer review or proof validation.",
                "tags": [c.get("term") for c in entry.findall("a:category", ns) if c.get("term")][:16]})
    return records
