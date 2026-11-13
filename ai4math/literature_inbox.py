"""An append-only handoff from research tasks to a separate literature session."""
from pathlib import Path
import time

from .knowledge import count, strings, text_fields
from .store import ResearchError, digest, now


def workspace_key(path):
    return digest(str(Path(path).resolve()))


class LiteratureInbox:
    def __init__(self, store):
        self.store = store
        store.get("academic_library", "main")

    def _next_sequence(self):
        return self.store.db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM events").fetchone()[0]

    def submit(self, payload, origin, actor):
        text_fields(payload, "question", "purpose")
        if set(payload) - {"question", "purpose", "assumptions", "queries"}:
            raise ResearchError("unknown literature request field")
        if any(len(payload[k]) > 1600 for k in ("question", "purpose")):
            raise ResearchError("literature request text exceeds 1600 characters")
        assumptions = strings(payload, "assumptions", 1, 8)
        queries = strings(payload, "queries", 0, 8)
        if any(len(value) > 500 for value in [*assumptions, *queries]):
            raise ResearchError("literature request assumptions and queries are limited to 500 characters each")
        # Preserve case and mathematical notation. Only normalize whitespace and the order of assumptions.
        scope = {"question": " ".join(payload["question"].split()),
                 "assumptions": sorted({" ".join(value.split()) for value in assumptions})}
        key = digest(scope)
        subscription = {"request": key, "origin": origin, "actor": actor,
                        "purpose": payload["purpose"], "queries": queries}
        subscription_id = digest(subscription)
        with self.store.transaction("knowledge.request", actor):
            if not self.store.exists("literature_request", key):
                self.store.put("literature_request", key, {"id": key, **scope, "created_at": now()}, new=True)
            if not self.store.exists("literature_subscription", subscription_id):
                self.store.put("literature_subscription", subscription_id,
                               {"id": subscription_id, **subscription, "created_at": now(),
                                "sequence": self._next_sequence()}, new=True)
            row = self._views(request_id=key)[0]
            return {"request_id": key, "subscription_id": subscription_id, "status": row["status"]}

    def _views(self, request_id=None, workspace=None, task=None):
        requests = ([self.store.get("literature_request", request_id)] if request_id else
                    self.store.all("literature_request"))
        subscribers = {}
        for row in self.store.all("literature_subscription"):
            if workspace is not None and row["origin"]["workspace"] != workspace:
                continue
            if task is not None and row["origin"].get("task") != task:
                continue
            subscribers.setdefault(row["request"], []).append(row)
        attempts = {}
        for row in self.store.all("literature_attempt"):
            prior = attempts.get(row["request"])
            if prior is None or prior["sequence"] < row["sequence"]:
                attempts[row["request"]] = row
        endings = {row["id"]: row for row in self.store.all("literature_attempt_end")}
        rows = []
        for request in requests:
            key = request["id"]
            if key not in subscribers:
                continue
            attempt = attempts.get(key)
            end = endings.get(attempt["id"]) if attempt else None
            status = "published" if end and end["outcome"] != "interrupted" else (
                "claimed" if attempt and end is None else "queued")
            row = {**request, "status": status, "subscriptions": subscribers[key], "attempt": attempt, "result": end}
            if status == "published":
                row["delivery_seq"] = max(end["sequence"], *(sub["sequence"] for sub in subscribers[key]))
            rows.append(row)
        return sorted(rows, key=lambda row: (row["created_at"], row["id"]))

    def list(self, *, status=None, request_id=None, workspace=None, task=None, limit=20, wait=0, after=None):
        count(limit, high=100)
        count(wait, low=0, high=30)
        if after is not None and (status != "published" or type(after) is not int or after < 0):
            raise ResearchError("a nonnegative delivery cursor requires status published")
        deadline = time.monotonic() + wait
        while True:
            with self.store.snapshot():
                rows = [row for row in self._views(request_id, workspace, task) if status is None or row["status"] == status]
                if status == "published":
                    rows = sorted((row for row in rows if row["delivery_seq"] > (after or 0)), key=lambda row: row["delivery_seq"])
            remaining = deadline - time.monotonic()
            if rows or remaining <= 0:
                return rows[:limit]
            time.sleep(min(0.5, remaining))

    def claim(self, actor, request_id=None):
        with self.store.transaction("knowledge.request.claim", actor):
            rows = [row for row in self._views(request_id) if row["status"] == "queued" and
                    all(sub["actor"] != actor for sub in row["subscriptions"])]
            if not rows:
                if request_id:
                    raise ResearchError("request is unavailable or needs a distinct literature actor")
                return None
            row = rows[0]
            sequence = row["attempt"]["sequence"] + 1 if row["attempt"] else 1
            key = digest({"request": row["id"], "sequence": sequence})
            attempt = {"id": key, "request": row["id"], "sequence": sequence, "actor": actor, "claimed_at": now()}
            self.store.put("literature_attempt", key, attempt, new=True)
            return {**row, "status": "claimed", "attempt": attempt, "result": None}

    def finish(self, request_id, attempt_id, payload, actor):
        text_fields(payload, "outcome", "summary")
        outcome = payload["outcome"]
        if outcome not in {"found", "no_match", "blocked", "interrupted"} or len(payload["summary"]) > 4000:
            raise ResearchError("invalid literature outcome or oversized summary")
        if set(payload) - {"outcome", "summary", "queries", "profile_ids"}:
            raise ResearchError("unknown literature result field")
        queries = strings({"queries": payload.get("queries", [])}, "queries", maximum=24)
        profiles = strings({"profile_ids": payload.get("profile_ids", [])}, "profile_ids", maximum=12)
        if outcome in {"found", "no_match"} and not queries:
            raise ResearchError("record the actual literature queries")
        if (outcome == "found") != bool(profiles) or len(set(profiles)) != len(profiles):
            raise ResearchError("only a found outcome names distinct completed profiles")
        with self.store.transaction("knowledge.request.finish", actor):
            attempt = self.store.get("literature_attempt", attempt_id)
            if attempt["request"] != request_id or attempt["actor"] != actor:
                raise ResearchError("finish must bind the exact request and its claiming literature actor")
            record = {"id": attempt_id, "request": request_id, "actor": actor, "outcome": outcome,
                      "summary": payload["summary"], "queries": queries, "profile_ids": profiles, "evidence_level": "unknown"}
            if self.store.exists("literature_attempt_end", attempt_id):
                existing = self.store.get("literature_attempt_end", attempt_id)
                if all(existing[k] == v for k, v in record.items()):
                    return existing
                raise ResearchError("literature attempt already closed")
            for key in profiles:
                profile = self.store.get("paper_profile", key)
                self.store.verify_blob(profile["artifact"])
                content = profile["content"]
                self.store.verify_blob(content["raw_artifact"])
                for anchor in content["anchors"]:
                    self.store.verify_blob(self.store.get("passage", anchor)["artifact"])
            record.update(finished_at=now(), sequence=self._next_sequence())
            self.store.put("literature_attempt_end", attempt_id, record, new=True)
            return record
