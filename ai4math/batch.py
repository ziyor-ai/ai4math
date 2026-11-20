"""A first-class batch: many independent propositions, each in its own workspace.

Batch 8.x audit fix F1. The framework keeps one goal per workspace on purpose, so a
batch is a *registry*: a control workspace records which proposition lives where and
aggregates their status by opening each workspace read-only. No fake conjunction, no
second goal per store, and no metric pollution.
"""
from .store import ResearchError, now
from pathlib import Path


def _target(w, value):
    """Resolve a registered workspace path.

    A batch registry intentionally references workspaces outside its own store (each
    proposition keeps its own goal), so ``Store.inside`` cannot be used. Relative
    paths are tried against the control workspace first, then the process directory.
    """
    raw = Path(value)
    candidates = [raw] if raw.is_absolute() else [w.store.root / raw, Path.cwd() / raw]
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "research.sqlite3").is_file():
            return resolved
    raise ResearchError("batch.add needs an existing workspace: " + str(value))


def open_batch(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("batch.open needs an object payload")
    for key in ("id", "title"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("batch.open needs nonempty " + key)
    if w.store.exists("batch", p["id"]):
        raise ResearchError("batch already exists: " + p["id"])
    row = {"id": p["id"], "title": p["title"], "source_doc": p.get("source_doc"),
           "items": [], "created_by": actor, "created_at": now()}
    w.store.put("batch", row["id"], row, new=True)
    return row


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("batch.add needs an object payload")
    for key in ("batch", "slug", "workspace", "node"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("batch.add needs nonempty " + key)
    row = w.store.get("batch", p["batch"])
    if any(item["slug"] == p["slug"] for item in row["items"]):
        raise ResearchError("batch already lists " + p["slug"])
    _target(w, p["workspace"])
    row["items"].append({"slug": p["slug"], "workspace": p["workspace"], "node": p["node"],
                         "source": p.get("source"), "added_at": now()})
    w.store.put("batch", row["id"], row)
    return row


def status(w, p, actor):
    from .workbench import Workbench
    row = w.store.get("batch", p.get("batch"))
    items = []
    for item in row["items"]:
        entry = {"slug": item["slug"], "node": item["node"], "source": item.get("source"),
                 "workspace": item["workspace"]}
        try:
            target = _target(w, item["workspace"])
        except ResearchError:
            entry["state"] = "missing"
            items.append(entry)
            continue
        other = Workbench(target, read_only=True)
        try:
            accepted = [c for c in other.store.all("claim")
                        if c.get("node") == item["node"] and c.get("status") == "accepted"]
            rounds = [r for r in other.store.all("round") if r.get("state") == "settled"]
            entry.update(node_status=other.node_status(item["node"]),
                         claim=accepted[0]["id"] if accepted else None,
                         evidence_level=accepted[0]["evidence_level"] if accepted else None,
                         settled_rounds=len(rounds),
                         state="proved" if accepted else "open")
        finally:
            other.store.close()
        items.append(entry)
    proved = [item for item in items if item.get("state") == "proved"]
    return {"batch": row["id"], "title": row["title"], "total": len(items),
            "proved": len(proved), "items": items}


HANDLERS = {"batch.open": open_batch, "batch.add": add, "batch.status": status}
