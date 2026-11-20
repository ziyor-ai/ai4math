"""Read-only survey of every workspace under one root.

One goal should have one live workspace.  This survey lists each workspace
with its locked goal digest and status, and groups workspaces that share a
goal, so a split or a duplicated research line is visible instead of implied.
"""

from pathlib import Path

from .store import ResearchError


def survey(root):
    base = Path(root).resolve()
    if not base.is_dir():
        raise ResearchError("workspace root is not a directory: " + str(base))
    from .workbench import Workbench
    rows = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or not (entry / "research.sqlite3").is_file():
            continue
        row = {"workspace": entry.name, "path": str(entry)}
        try:
            workbench = Workbench(entry, read_only=True)
            try:
                project = workbench.store.get("project", "main")
                goal = project.get("goal")
                node = workbench.store.get("node", goal) if goal else None
                row.update({
                    "goal": goal,
                    "statement_digest": node["statement_digest"] if node else None,
                    "status": workbench.node_status(goal) if goal else "no-goal",
                    "nodes": len(workbench.store.all("node")),
                    "open_obligations": len([n for n in workbench.store.all("node")
                                            if n["id"] != goal and workbench.node_status(n["id"]) == "open"]),
                    "accepted_claims": len([c for c in workbench.store.all("claim") if c["status"] == "accepted"]),
                    "routes": len(workbench.store.all("route")),
                })
                counts = workbench.store.db.execute("SELECT COUNT(*), MAX(timestamp) FROM events").fetchone()
                row.update({"events": counts[0], "last_event": counts[1]})
            finally:
                workbench.store.close()
        except (ResearchError, OSError, KeyError, ValueError) as exc:
            row["error"] = str(exc)
        rows.append(row)
    groups = {}
    for row in rows:
        digest = row.get("statement_digest")
        if digest:
            groups.setdefault(digest, []).append(row["workspace"])
    duplicates = {digest: names for digest, names in groups.items() if len(names) > 1}
    return {"root": str(base), "workspaces": rows, "duplicate_goals": duplicates,
            "split_goal": bool(duplicates)}


def panel(state):
    lines = ["root: " + state["root"],
             "workspaces: " + str(len(state["workspaces"])) +
             " | duplicate goal groups: " + str(len(state["duplicate_goals"]))]
    for row in state["workspaces"]:
        if "error" in row:
            lines.append("{:<44} ERROR {}".format(row["workspace"][:44], row["error"]))
            continue
        lines.append("{:<44} {:<9} nodes {:>4} open {:>4} claims {:>3} events {:>6}  {}".format(
            row["workspace"][:44], row["status"], row["nodes"], row["open_obligations"],
            row["accepted_claims"], row["events"], (row["statement_digest"] or "")[:12]))
    for digest, names in state["duplicate_goals"].items():
        lines.append("duplicate goal " + digest[:12] + ": " + ", ".join(names))
    return "\n".join(lines) + "\n"
