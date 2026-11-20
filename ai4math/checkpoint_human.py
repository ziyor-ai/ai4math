"""Human checkpoints: every N finished rounds need an explicit human confirmation."""
from __future__ import annotations

import os
from . import identities
from .store import ResearchError, now

DEFAULT_EVERY = 5


def every():
    try:
        value = int(os.environ.get("AI4MATH_HUMAN_CHECKPOINT_ROUNDS", ""))
    except ValueError:
        return DEFAULT_EVERY
    return value if value > 0 else DEFAULT_EVERY


def _completed(w):
    return [t for t in w.store.all("task") if t.get("status") == "completed"]


def pending_rounds(w):
    rows = sorted(w.store.all("human_checkpoint"), key=lambda r: r["at"])
    confirmed = [r for r in rows if r.get("confirmed_at")]
    since = confirmed[-1]["confirmed_at"] if confirmed else ""
    return [t for t in _completed(w) if (t.get("finished_at") or "") > since]


def require(w, actor="dsh-coordinator"):
    due = pending_rounds(w)
    if len(due) < every():
        return None
    open_rows = [r for r in w.store.all("human_checkpoint") if not r.get("confirmed_at")]
    if open_rows:
        raise ResearchError("a human checkpoint is already waiting for confirmation: " + open_rows[-1]["id"])
    record = {"id": "human-" + str(len(list(w.store.all("human_checkpoint"))) + 1),
              "at": now(), "rounds": [t["id"] for t in due], "every": every(),
              "question": "review the last " + str(len(due)) + " rounds before opening another",
              "confirmed_at": None, "confirmed_by": None, "note": None}
    with w.store.transaction("human_checkpoint.open", actor):
        w.store.put("human_checkpoint", record["id"], record, new=True)
    raise ResearchError("human checkpoint required after " + str(len(due)) + " rounds; "
                        "confirm with: ai4math human-checkpoint confirm --id " + record["id"])


def confirm(w, key, actor, note):
    record = w.store.get("human_checkpoint", key)
    if record.get("confirmed_at"):
        raise ResearchError("this human checkpoint was already confirmed")
    if actor == "dsh-coordinator":
        raise ResearchError("the coordinator cannot confirm its own checkpoint; a human must")
    identities.require(w, actor, ("human",))
    if not isinstance(note, str) or not note.strip():
        raise ResearchError("confirmation needs a short note")
    record.update(confirmed_at=now(), confirmed_by=actor, note=note)
    w.store.put("human_checkpoint", key, record)
    return record
