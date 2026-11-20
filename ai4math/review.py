"""Two independent reviews gate a worker round: adversarial and verification.

Both reviewers run without the exploration context: they receive the sealed
outputs and the frozen contract only.  Reviewer models are configurable from
.env so a different (or stronger) model can be pointed at the checks.
"""
from __future__ import annotations

import json
import os

from . import identities
from .store import ResearchError, now

ROLES = ("adversarial", "verify")
# Batch 8.0 adds a direction review that must NOT become a default acceptance gate:
# `passed()` keeps reading ROLES only, so existing rounds stay valid, while the
# strategy role can be recorded and audited per round.
CRITIQUE_ROLE = "strategy"
CRITIQUE_ITEMS = ("intent", "connection", "evidence", "stop_condition")

ADVERSARIAL = """You are an adversarial reviewer. The worker claims the results in the attached
sealed artifacts. Your only job is to break them. Do not summarise. Do not praise.

Check, in this order, and stop at the first decisive failure:
1. Does every quoted number appear in the sealed artifacts, with the same units and
   the same sign? Recompute at least two of them from the stated model.
2. Is any conclusion stronger than the evidence (a finite family used to claim a
   universal, a ratio used to claim an absolute bound, a self-check used as a proof)?
3. Are the falsifier and the observed outcome actually linked, or did the worker test
   a neighbouring statement?
4. Is any required hypothesis assumed rather than checked? Name it exactly.
5. Could the artifact be produced without the computation it describes?

Reply with JSON only: {"verdict":"pass"|"fail","failures":[{"claim":"...","reason":"...","evidence":"..."}],"strongest_unchecked_assumption":"..."}
"""

VERIFY = """You are an independent verifier with no exploration context. Re-derive the
central quantitative claim of the attached sealed artifacts from the frozen model
description alone, without reading the worker prose first.

Rules:
- Work only from the model definition and the contract; use the worker text only to
  locate the claim.
- Report your own numbers next to theirs, and the relative error.
- A claim passes only if your independent numbers agree to the tolerance the claim
  states; if no tolerance is stated, require 1e-6 relative.

Reply with JSON only: {"verdict":"pass"|"fail","your_numbers":{...},"their_numbers":{...},"relative_error":...,"note":"..."}
"""

PROMPTS = {"adversarial": ADVERSARIAL, "verify": VERIFY}

CRITIQUE = """You are the strategy reviewer for one finished round. You do not re-run the
mathematics; you check whether the round still belongs to the research direction it claims.

Check, in this order:
1. intent: does the round answer the intent it was opened with, or a neighbouring question?
2. connection: does the stated link to the locked goal actually hold as written?
3. evidence: is every conclusion supported by a frozen artifact, or by a model opinion?
4. stop_condition: was the declared stop condition respected (budget, rounds, falsifier)?

You may answer "open" when the evidence genuinely cannot decide an item, and you must name
it under "unresolved". Do not invent certainty to fill the rubric.

Reply with JSON only:
{"verdict":"pass"|"fail","basis":"...","unresolved":["..."],
 "rubric":{"intent":"clear"|"open"|"blocked","connection":"...","evidence":"...","stop_condition":"..."}}
"""

PROMPTS[CRITIQUE_ROLE] = CRITIQUE


def models():
    return {"provider": os.environ.get("AI4MATH_REVIEW_PROVIDER") or None,
            "model": os.environ.get("AI4MATH_REVIEW_MODEL") or None}


def _check_payload(role, verdict, payload):
    if not isinstance(payload, dict) or not payload:
        raise ResearchError("a review needs its structured payload (numbers or failures)")
    if role == CRITIQUE_ROLE:
        basis = payload.get("basis")
        if not isinstance(basis, str) or not basis.strip():
            raise ResearchError("strategy review needs a nonempty basis")
        rubric = payload.get("rubric")
        if not isinstance(rubric, dict) or set(rubric) != set(CRITIQUE_ITEMS):
            raise ResearchError("strategy review needs the rubric items: " + ", ".join(CRITIQUE_ITEMS))
        for item, value in rubric.items():
            if value not in {"clear", "open", "blocked"}:
                raise ResearchError("strategy rubric outcome must be clear, open or blocked: " + item)
        if not isinstance(payload.get("unresolved", []), list):
            raise ResearchError("strategy review unresolved must be a list")
        if verdict == "pass" and any(value == "blocked" for value in rubric.values()):
            raise ResearchError("a blocked rubric item cannot pass the strategy review")
        return
    if role == "verify":
        for key in ("your_numbers", "their_numbers", "relative_error", "note"):
            if key not in payload:
                raise ResearchError("verify review payload needs " + key)
        try:
            error = float(payload["relative_error"])
        except (TypeError, ValueError):
            raise ResearchError("verify review needs a numeric relative_error")
        if verdict == "pass" and error > 1e-6:
            raise ResearchError("verify review cannot pass with relative_error above 1e-6")
    else:
        if not isinstance(payload.get("failures"), list):
            raise ResearchError("adversarial review payload needs a failures list")
        if not isinstance(payload.get("strongest_unchecked_assumption"), str) or not payload["strongest_unchecked_assumption"].strip():
            raise ResearchError("adversarial review must name its strongest unchecked assumption")


def record(w, tid, role, verdict, artifact, notes, actor, payload=None):
    if role not in ROLES + (CRITIQUE_ROLE,):
        raise ResearchError("review role must be adversarial, verify or strategy")
    if verdict not in {"pass", "fail"}:
        raise ResearchError("review verdict must be pass or fail")
    if not isinstance(notes, str) or not notes.strip():
        raise ResearchError("review needs notes naming what was checked")
    payload = payload if isinstance(payload, dict) else (json.loads(payload) if isinstance(payload, str) and payload.strip() else None)
    _check_payload(role, verdict, payload)
    key = f"{tid}-{role}-review"
    if w.store.exists("round_review", key):
        raise ResearchError("this role already reviewed the round; cannot be overwritten")
    task = w.store.get("task", tid)
    if actor == task.get("dispatched_by"):
        raise ResearchError("the reviewer must not be the dispatching coordinator")
    identities.require(w, actor, ("model", "human"))
    record = {"id": key, "task": tid, "role": role, "verdict": verdict,
              "artifact": artifact, "notes": notes, "reviewer": actor, "at": now()}
    w.store.put("round_review", key, record, new=True)
    return record


def status(w, tid):
    rows = {row["role"]: row for row in w.store.all("round_review") if row.get("task") == tid}
    return {role: rows.get(role) for role in ROLES}


def passed(w, tid):
    rows = status(w, tid)
    return all(rows[role] is not None and rows[role]["verdict"] == "pass" for role in ROLES)
