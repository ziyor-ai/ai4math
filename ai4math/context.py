"""Assemble the real research context a worker needs, as a frozen artifact."""
from __future__ import annotations

from . import ledger


def bundle(w, node, limit=5):
    target = w.store.get("node", node)
    lines = ["# Research context bundle", "", "## Locked target",
             "Statement: " + str(target.get("statement")),
             "Domain: " + str(target.get("domain")),
             "Quantifier: " + str(target.get("quantifier")),
             "Proof dependencies: " + ", ".join(target.get("proof_dependencies") or []) or "(none)",
             "", "## Recent rounds"]
    entries = ledger.entries(w.store.root)[-limit:]
    if not entries:
        lines.append("- none recorded yet")
    for entry in entries:
        lines += ["", "### " + str(entry.get("task")),
                  "Question: " + str(entry.get("question")),
                  "Method: " + str(entry.get("method")),
                  "Observed: " + str(entry.get("observed"))[:1200],
                  "Falsified: " + str(entry.get("falsified"))[:600],
                  "Open: " + str(entry.get("open_obligations"))[:600]]
    requests = [r for r in w.store.all("literature_request") if r.get("status") == "queued"]
    lines += ["", "## Queued literature requests"]
    lines += ["- " + str(r.get("query") or r.get("id")) for r in requests] or ["- none"]
    open_nodes = [n["id"] for n in w.store.all("node") if w.node_status(n["id"]) == "open"]
    lines += ["", "## Open obligations"] + ["- " + key for key in open_nodes[:12]]
    return "\n".join(lines)
