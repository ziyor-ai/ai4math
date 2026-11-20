"""Input provenance checks: a task must freeze evidence, not empty placeholders."""
from __future__ import annotations

from .store import ResearchError

MIN_BYTES = 800


def audit(w, task):
    """Return one finding per frozen input artifact."""
    findings = []
    for key in task.get("inputs") or []:
        record = w.store.get("artifact", key)
        path = w.store.verify_blob(key)
        size = path.stat().st_size
        findings.append({"artifact": key, "bytes": size,
                         "purpose": record.get("purpose"),
                         "placeholder": size < MIN_BYTES})
    return findings


def require_real(w, task):
    rows = audit(w, task)
    if any(row["purpose"] == "research context bundle" and not row["placeholder"] for row in rows):
        return
    exempt = {"frozen skill manifest"}
    thin = [row for row in rows if row["placeholder"] and row["purpose"] not in exempt]
    if not thin or task.get("thin_inputs_ack"):
        return
    names = ", ".join(f"{row['artifact'][:12]}({row['bytes']}B)" for row in thin)
    raise ResearchError("frozen inputs smaller than " + str(MIN_BYTES) +
                        " bytes look like placeholders: " + names +
                        "; instantiate the real rows or acknowledge thin inputs explicitly")
