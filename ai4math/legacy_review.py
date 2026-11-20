"""Re-review historical rounds with the current two-stage review standard."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .store import ResearchError, now

BATCH = 6


def _return_of(root, tid):
    path = Path(root) / "tasks" / tid / "return" / "return.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _declared(record):
    if isinstance(record, dict) and isinstance(record.get("artifact_digests"), dict) and record["artifact_digests"]:
        return [(name, sha) for name, sha in record["artifact_digests"].items()]
    if isinstance(record, dict) and not record.get("artifacts") and isinstance(record.get("artifact_ids"), list):
        rows = record["artifact_ids"]
    else:
        rows = record.get("artifacts") if isinstance(record, dict) else None
    if isinstance(rows, list):
        out = []
        for row in rows:
            if isinstance(row, str):
                out.append((row, None))
            elif isinstance(row, dict) and isinstance(row.get("path"), str):
                out.append((row["path"], row.get("sha256")))
        return out
    return []


def packet(w, tid):
    root = Path(w.store.root)
    task = w.store.get("task", tid)
    record = _return_of(root, tid)
    folder = root / "tasks" / tid / "return"
    lines = ["## " + tid,
             "- node: " + str(task.get("node")),
             "- question: " + str(task.get("learning_question") or ""),
             "- evidence level: " + str(task.get("evidence_level")),
             "- contract: " + str(task.get("contract_artifact"))[:16]]
    if record is None:
        lines.append("- return.json: MISSING (no frozen claim can be reviewed)")
        return "\n".join(lines)
    for key in ("summary", "suggested_evidence_level", "next_step", "open_obligations", "scope",
                "limitations", "statement_digest"):
        if key in record:
            lines.append("- " + key + ": " + str(record[key])[:1200])
    probe = record.get("first_probe")
    if isinstance(probe, dict):
        for key in ("test", "outcome", "scope"):
            lines.append("- probe." + key + ": " + str(probe.get(key))[:800])
    declared = _declared(record)
    if not declared and (folder / "seal-result.json").is_file():
        try:
            sealed = json.loads((folder / "seal-result.json").read_text(encoding="utf-8"))
            outs = (sealed.get("receipt") or {}).get("output_artifacts") or []
            names = [p.name for p in folder.iterdir() if p.is_file() and p.name != "return.json"]
            if outs and len(outs) == len(names):
                declared = [(name, sha) for name, sha in zip(sorted(names), outs)]
        except (OSError, ValueError):
            pass
    on_disk = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")
                     if p.is_file() and p.relative_to(folder).as_posix() != "return.json") if folder.is_dir() else []
    tracked = {path for path, _ in declared}
    lines.append("- declared artifacts: " + str(len(declared)))
    for path, sha in declared:
        target = folder / path if (folder / path).is_file() else folder / Path(path).name
        size = target.stat().st_size if target.is_file() else "?"
        note = sha
        if not note and target.is_file():
            note = hashlib.sha256(target.read_bytes()).hexdigest() + " (computed from disk, not declared)"
        lines.append("  - " + str(path) + " sha256=" + (str(note) if note else "unstated") + " size=" + str(size))
    outputs = task.get("output_artifacts") or []
    lines.append("- task inventory: " + str(len(on_disk)) + " files under return/ (declared " + str(len(declared)) +
                 " plus untracked and scaffolding; not a hash manifest)")
    missing = [path for path, _ in declared
               if not (folder / path).is_file() and not (folder / Path(path).name).is_file()]
    if missing:
        lines.append("- declared files missing on disk: " + ", ".join(missing))
    return_time = (folder / "return.json").stat().st_mtime if (folder / "return.json").is_file() else 0
    untracked = [name for name in on_disk if name not in tracked and name not in {Path(p).name for p in tracked}]
    lines.append("- untracked files (not in return.artifacts): " + (", ".join(untracked) if untracked else "none"))
    later = [name for name in on_disk if (folder / name).stat().st_mtime > return_time]
    lines.append("- WRITTEN AFTER return.json: " + (", ".join(later) if later else "none"))
    lines.append("- counted files under return/ (excludes return.json): " + str(len(on_disk)))
    lean = [path for path, _ in declared if str(path).endswith(".lean")] + [n for n in on_disk if n.endswith(".lean")]
    if not lean:
        lines.append("- Lean: none (no formal clause in this round)")
    if lean:
        receipts = [n for n in on_disk if Path(n).name == "worker-command-ledger.json"
                    or any(word in Path(n).name.lower() for word in ("compile", "build", "log", "axiom", "final-audit", "replay",
                                                              "lean-run", "original", "receipt", "audit"))]
        lines.append("- Lean modules: " + str(len(set(lean))) +
                     "; build/axiom receipt: " + (", ".join(receipts) if receipts else "MISSING (cannot clear stage 1 without it)"))
    return "\n".join(lines)

def reviewable(w):
    # Sorted so a batch offset always names the same rounds.
    return sorted((t for t in w.store.all("task") if t.get("status") == "completed"
                   and _return_of(w.store.root, t["id"]) is not None), key=lambda t: t["id"])


def next_offset(w, size=BATCH):
    """First chunk whose rounds have no verdict yet."""
    done = {row["task"] for row in w.store.all("legacy_review")}
    tasks = reviewable(w)
    for skip in range(0, len(tasks), size):
        if any(t["id"] not in done for t in tasks[skip:skip + size]):
            return skip
    return len(tasks)


def batch(w, size=BATCH, skip=0):
    done = reviewable(w)
    picked = done[skip:skip + size]
    text = "\n\n".join(packet(w, t["id"]) for t in picked)
    folder = Path(w.store.root) / "reports" / "legacy-review" / "packets"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"batch-{skip // max(1, size):03d}.md"
    if path.is_file():
        # Never rewrite a packet a reviewer may already have read.
        import re
        tasks = re.findall(r"^## (.+)$", path.read_text(encoding="utf-8"), re.M)
        return {"path": str(path), "tasks": tasks, "offset": skip, "existing": True}
    path.write_text("# Legacy review batch (offset " + str(skip) + ")\n\n" + text, encoding="utf-8")
    return {"path": str(path), "tasks": [t["id"] for t in picked], "offset": skip, "existing": False}


def record(w, tid, verdict, artifact, notes, actor, force=False):
    if verdict not in {"pass", "fail", "unsure"}:
        raise ResearchError("legacy verdict must be pass, fail or unsure")
    if not isinstance(notes, str) or not notes.strip():
        raise ResearchError("legacy review needs notes")
    if not w.store.exists("task", tid):
        raise ResearchError("legacy review needs an existing task: " + tid)
    key = tid + "-legacy-review" if len(tid) <= 100 else "legacy-" + hashlib.sha256(tid.encode("utf-8")).hexdigest()[:32]
    existing = [r for r in w.store.all("legacy_review") if r.get("id") == key and not r.get("retracted")]
    if existing and not force:
        raise ResearchError("this round already has a legacy review")
    row = {"id": key, "task": tid, "verdict": verdict, "artifact": artifact,
           "notes": notes, "reviewer": actor, "at": now()}
    prior = [r for r in w.store.all("legacy_review") if r.get("id") == key]
    if force and prior:
        row["corrected_from"] = prior[-1].get("verdict")
    w.store.put("legacy_review", key, row, new=not prior)
    return row


def _classify(notes):
    text = (notes or "").lower()
    if ("unsealed" in text or "bare names" in text or "no hash" in text
            or "unstated" in text or "not declared" in text or "0/2" in text or "0/3" in text):
        return "unsealed record (no declared hashes)"
    if "receipt" in text or "unattested" in text or "no build" in text:
        return "missing build/axiom receipt"
    if "conditional" in text or "premise" in text:
        return "conditional premise not discharged"
    if "witness" in text or "not constructed" in text:
        return "missing witness for the frozen object"
    return "other / needs a closer look"


def record_batch(w, path, artifact, actor):
    """Record many verdicts from one JSON file: [{"task","verdict","reason"}]."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    done, skipped = [], []
    for row in rows:
        try:
            with w.store.transaction("legacy_review.record", actor):
                record(w, row["task"], row["verdict"], artifact, row["reason"], actor)
            done.append(row["task"])
        except Exception as exc:
            skipped.append({"task": row.get("task"), "error": str(exc)[:120]})
    return {"recorded": len(done), "skipped": skipped}


def _verdicts_from_text(text):
    """Extract the largest JSON array of {task, verdict, reason} objects."""
    best = []
    decoder = json.JSONDecoder()
    index = 0
    while True:
        start = text.find("[", index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text[start:])
        except ValueError:
            index = start + 1
            continue
        if (isinstance(value, list) and value and all(isinstance(r, dict) for r in value)
                and all({"task", "verdict"} <= set(r) for r in value)):
            if len(value) > len(best):
                best = value
        index = start + max(1, end)
    return best


def import_review(w, batch, actor):
    name = str(batch)
    review = Path(w.store.root) / "reports" / "legacy-review" / "reviews" / ("batch-" + name + ".md")
    if not review.is_file():
        raise ResearchError("no review document for batch " + name)
    rows = _verdicts_from_text(review.read_text(encoding="utf-8"))
    if not rows:
        raise ResearchError("no verdict JSON found in " + review.name)
    artifact = w.apply("artifact.import", {"path": str(review), "purpose": "legacy review batch " + name}, actor)["id"]
    result = {"recorded": 0, "skipped": []}
    for row in rows:
        try:
            with w.store.transaction("legacy_review.record", actor):
                record(w, row["task"], row["verdict"], artifact,
                       str(row.get("reason") or "no reason given")[:900], actor)
            result["recorded"] += 1
        except Exception as exc:
            result["skipped"].append({"task": row.get("task"), "error": str(exc)[:120]})
    return {**result, "artifact": artifact, "rows": len(rows)}


def write_round_docs(w):
    """One document per reviewed round: packet + verdict + reviewer reasoning."""
    folder = Path(w.store.root) / "reports" / "legacy-review" / "rounds"
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for row in w.store.all("legacy_review"):
        tid = row["task"]
        if not w.store.exists("task", tid):
            continue
        lines = ["# Legacy review: " + tid, "",
                 "- verdict: **" + row["verdict"] + "**",
                 "- reviewer: " + str(row["reviewer"]),
                 "- reviewed at: " + str(row["at"]),
                 "- review document: " + str(row["artifact"]),
                 "- evidence level: unknown (re-review does not upgrade)",
                 "", "## Reviewer reasoning", "", row["notes"], "", "## Round under review", "",
                 packet(w, tid)]
        path = folder / (tid + ".md")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(str(path))
    return written


def summary(w):
    result = report(w)
    rows = {row["task"]: row for row in w.store.all("legacy_review")}
    lines = ["# Legacy re-review summary", "",
             "- completed rounds: " + str(result["completed"]),
             "- reviewable (frozen return.json): " + str(result["reviewable"]),
             "- reviewed: " + str(result["reviewed"]),
             "- pass: " + str(len(result["pass"])),
             "- fail: " + str(len(result["fail"])),
             "- unsure: " + str(len(result["unsure"])),
             "- pending: " + str(len(result["pending"])),
             "", "## Verified results (pass)", ""]
    lines += ["- " + tid + " :: " + rows[tid]["notes"] for tid in result["pass"]] or ["- none yet"]
    lines += ["", "## Rejected (fail)", ""]
    lines += ["- " + tid + " — " + rows[tid]["notes"] for tid in result["fail"]] or ["- none"]
    lines += ["", "## Unresolved (unsure): what is missing", ""]
    lines += ["- " + tid + " — " + rows[tid]["notes"] for tid in result["unsure"]] or ["- none"]
    reproduced = [tid for tid in result["unsure"] if str(rows[tid]["notes"]).lower().startswith("reproduced")]
    lines += ["", "## Reproduced but unsealed (would pass under a relaxed seal criterion)", ""]
    lines += ["- " + tid + " :: " + rows[tid]["notes"] for tid in reproduced] or ["- none"]
    groups = {}
    for tid in result["unsure"]:
        groups.setdefault(_classify(rows[tid]["notes"]), []).append(tid)
    lines += ["", "## Unsure by missing piece", ""]
    for name, tids in sorted(groups.items(), key=lambda item: -len(item[1])):
        lines.append("- " + name + ": " + str(len(tids)))
    findings = Path(w.store.root) / "reports" / "legacy-review" / "FINDINGS.md"
    if findings.is_file():
        lines += ["", "## Systemic findings", ""] + findings.read_text(encoding="utf-8").splitlines()
    lines += ["", "## No frozen claim", "",
              "- " + str(len(result["unreviewable"])) + " completed rounds carry no return.json (phase steps)"]
    path = Path(w.store.root) / "reports" / "legacy-review" / "SUMMARY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(path), "pass": len(result["pass"]), "fail": len(result["fail"]),
            "unsure": len(result["unsure"]), "pending": len(result["pending"])}


def report(w):
    rows = {row["task"]: row for row in w.store.all("legacy_review")
            if w.store.exists("task", row["task"]) and not row.get("retracted")}
    done = reviewable(w)
    completed = [t for t in w.store.all("task") if t.get("status") == "completed"]
    unreviewable = [t["id"] for t in completed if _return_of(w.store.root, t["id"]) is None]
    passed = [t["id"] for t in done if rows.get(t["id"], {}).get("verdict") == "pass"]
    failed = [t["id"] for t in done if rows.get(t["id"], {}).get("verdict") == "fail"]
    unsure = [t["id"] for t in done if rows.get(t["id"], {}).get("verdict") == "unsure"]
    pending = [t["id"] for t in done if t["id"] not in rows]
    return {"completed": len(completed), "reviewable": len(done), "unreviewable": unreviewable,
            "reviewed": len(rows), "pass": passed, "fail": failed, "unsure": unsure, "pending": pending}
