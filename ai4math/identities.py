"""Named identities: reviewers and humans are enrolled, not typed ad hoc."""
from __future__ import annotations

from .store import ResearchError, now

KINDS = ("human", "model")


def enroll(w, name, kind, provider=None, model=None, actor="dsh-coordinator"):
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        raise ResearchError("identity name must be 1..64 characters")
    if kind not in KINDS:
        raise ResearchError("identity kind must be human or model")
    if w.store.exists("identity", name):
        raise ResearchError("identity already enrolled: " + name)
    record = {"id": name, "kind": kind, "provider": provider, "model": model,
              "enrolled_by": actor, "at": now()}
    w.store.put("identity", name, record, new=True)
    return record


def get(w, name):
    rows = [r for r in w.store.all("identity") if r.get("id") == name]
    return rows[0] if rows else None


def require(w, name, kinds=None):
    record = get(w, name)
    if record is None:
        raise ResearchError("unknown identity " + repr(name) + "; enroll it first (ai4math identity enroll)")
    if kinds and record.get("kind") not in kinds:
        raise ResearchError("identity " + name + " is a " + str(record.get("kind")) + " identity")
    return record
