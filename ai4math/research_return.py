"""Structural evidence for continuing past a limited first research probe."""

from .store import ResearchError


POLICY = "target_progress_v1"
DEDUCTION_POLICY = "target_progress_v2"
POLICIES = (POLICY, DEDUCTION_POLICY)
CHECKPOINT = "probe-review.json"
RETURN = "return.json"


def enabled(task):
    return task.get("codex_workflow", {}).get("research_stop_policy") in POLICIES


def deduction_first(task):
    return task.get("codex_workflow", {}).get("research_stop_policy") == DEDUCTION_POLICY


def _text(row, keys):
    if any(not isinstance(row.get(key), str) or not row[key].strip() for key in keys):
        raise ResearchError("nonempty research fields required: " + ", ".join(keys))


def artifact_names(values):
    if (not isinstance(values, list) or not 1 <= len(values) <= 98
            or any(not isinstance(name, str) or not name or name in {CHECKPOINT, RETURN} for name in values)
            or len(values) != len(set(values))):
        raise ResearchError("provide unique evidence paths relative to return/, excluding the broker's JSON records")
    return values


def check_probe(review):
    if not isinstance(review, dict) or set(review) != {
        "first_probe", "target_disposition", "target_relation", "next_attempt",
    }:
        raise ResearchError("probe_review needs first_probe, target_disposition, target_relation and next_attempt")
    _text(review, ("target_relation",))
    probe = review["first_probe"]
    if not isinstance(probe, dict) or set(probe) != {"test", "outcome", "scope", "artifacts"}:
        raise ResearchError("first_probe needs test, outcome, scope and evidence paths")
    _text(probe, ("test", "outcome", "scope"))
    artifact_names(probe["artifacts"])
    attempt = review["next_attempt"]
    if review["target_disposition"] == "candidate_answer" and attempt is None:
        return
    if (review["target_disposition"] != "limited" or not isinstance(attempt, dict)
            or set(attempt) != {"kind", "mechanism", "test", "different_from_probe"}
            or attempt["kind"] not in ("deduction", "counterexample", "route_obstruction")):
        raise ResearchError("a limited probe needs a target-directed deduction, counterexample or route obstruction attempt")
    _text(attempt, ("mechanism", "test", "different_from_probe"))


def check_return(review, record, *, deductive=False):
    fields = {
        "summary", "scope", "suggested_evidence_level", "open_obligations", "next_step", "stop_reason", "follow_up",
    }
    if deductive:
        fields.add("source_checks")
    if not isinstance(record, dict) or set(record) != fields:
        raise ResearchError("return_record fields differ from the research continuation contract")
    _text(record, ("summary", "scope", "next_step"))
    if record["suggested_evidence_level"] not in ("unknown", "finite_support"):
        raise ResearchError("producer handoff may suggest unknown or finite_support")
    stop = record["stop_reason"]
    obligations = record["open_obligations"]
    if (stop not in ("candidate_answer", "blocked") or not isinstance(obligations, list)
            or any(not isinstance(item, str) or not item.strip() for item in obligations)
            or (stop == "blocked" and not obligations)):
        raise ResearchError("state a candidate answer or name the unresolved obligation that blocks the route")
    follow = record["follow_up"]
    if review["target_disposition"] == "candidate_answer":
        if (stop != "candidate_answer" and not deductive) or follow is not None:
            raise ResearchError("a direct candidate answer needs consistent stop_reason and null follow_up")
    else:
        if not isinstance(follow, dict) or set(follow) != {"result", "scope", "artifacts"}:
            raise ResearchError("limited first probe requires an executed follow_up with result, scope and evidence paths")
        _text(follow, ("result", "scope"))
        artifact_names(follow["artifacts"])
    if deductive:
        check_sources(record["source_checks"])
        if stop == "candidate_answer" and any(row["passage_id"] is None for row in record["source_checks"]):
            raise ResearchError("unread load-bearing sources must leave the handoff conditional and blocked")


def check_derivation(row):
    if not isinstance(row, dict) or set(row) != {"gap", "deduction", "next_decision"}:
        raise ResearchError("derivation needs gap, deduction and next_decision")
    _text(row, ("gap", "deduction", "next_decision"))


def check_sources(rows):
    if not isinstance(rows, list) or len(rows) > 64:
        raise ResearchError("source_checks must be a list of load-bearing dependencies")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"theorem", "use_point", "assumptions", "passage_id", "limitation"}:
            raise ResearchError("source check needs theorem, use point, assumptions, passage ID and limitation")
        _text(row, ("theorem", "use_point", "limitation"))
        assumptions = row["assumptions"]
        if not isinstance(assumptions, dict) or not assumptions:
            raise ResearchError("map each cited hypothesis to the argument's reason or unresolved condition")
        if any(not isinstance(key, str) or not key.strip() for key in assumptions):
            raise ResearchError("source hypothesis names must be nonempty text")
        _text(assumptions, assumptions)
        passage = row["passage_id"]
        if passage is not None and (not isinstance(passage, str) or len(passage) != 64
                                    or any(c not in "0123456789abcdef" for c in passage)):
            raise ResearchError("source passage ID must be its SHA-256 or null")


def check_bound_return(task, record, checkpoint, hashes):
    from .external_deadline import bindings

    if (not isinstance(checkpoint, dict) or checkpoint.get("task_id") != task["id"]
            or checkpoint.get("contract_sha256") != task["contract_artifact"]):
        raise ResearchError("first probe review must bind the frozen task")
    review = checkpoint.get("review")
    check_probe(review)
    names = record.get("artifacts") if isinstance(record, dict) else None
    if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
            or sorted(names) != sorted(hashes)
            or any(record.get(key) != value for key, value in bindings(task).items())
            or record.get("probe_review_sha256") != hashes.get(CHECKPOINT)
            or record.get("first_probe") != review["first_probe"]):
        raise ResearchError("research return must bind the review, original inputs and complete evidence set")
    probe_names = review["first_probe"]["artifacts"]
    probe_hashes = checkpoint.get("probe_artifacts")
    if (not set(probe_names) <= set(hashes)
            or probe_hashes != {name: hashes[name] for name in probe_names}):
        raise ResearchError("first probe evidence missing or changed after review")
    fields = ("summary", "scope", "suggested_evidence_level", "open_obligations", "next_step", "stop_reason", "follow_up")
    deductive = deduction_first(task)
    if deductive:
        fields += ("source_checks",)
    check_return(review, {key: record.get(key) for key in fields}, deductive=deductive)
    follow = record["follow_up"]
    fresh = record.get("follow_up_written_after_review")
    if follow and (not set(follow["artifacts"]) <= set(hashes) or fresh not in follow["artifacts"]
                   or hashes[fresh] in probe_hashes.values()):
        raise ResearchError("follow-up must include new evidence written after probe_review")
    if deductive:
        if review["target_disposition"] == "limited":
            check_derivation(record.get("derivation"))
        passages = record.get("source_passages")
        expected = {row["passage_id"] for row in record["source_checks"] if row["passage_id"] is not None}
        if not isinstance(passages, dict) or set(passages) != expected:
            raise ResearchError("research return must bind the cited original passages")
        for aid, source in passages.items():
            if not isinstance(source, dict) or source.get("passage_id") != aid:
                raise ResearchError("source passage receipt has an inconsistent ID")
            _text(source, ("title", "url", "version", "locator", "source_id", "document", "raw_artifact", "origin"))


def check_seal(task, hashes, load, load_passage):
    if not {CHECKPOINT, RETURN} <= set(hashes):
        raise ResearchError("research continuation seal requires probe-review.json and return.json")
    evidence = {name: sha for name, sha in hashes.items() if name != RETURN}
    record = load(RETURN)
    check_bound_return(task, record, load(CHECKPOINT), evidence)
    if deduction_first(task):
        for aid, source in record["source_passages"].items():
            archived = load_passage(aid)
            if (not isinstance(archived, dict)
                    or any(archived.get(field) != source[field] for field in ("locator", "source_id", "document"))):
                raise ResearchError("source check disagrees with the archived passage provenance")


def contract(*, deductive=False):
    result = {
        "probe_review": {
            "first_probe": {"test": "actual test", "outcome": "observed result", "scope": "exact coverage",
                            "artifacts": ["return-relative evidence path"]},
            "target_disposition": "limited | candidate_answer",
            "target_relation": "covered obligation or first remaining load-bearing gap",
            "next_attempt": {"kind": "deduction | counterexample | route_obstruction", "mechanism": "new mechanism",
                             "test": "first discriminating step", "different_from_probe": "what makes it different"},
        },
        "handoff": {
            "paths": ["return-relative evidence path"],
            "return_record": {"summary": "result", "scope": "verified coverage and assumptions",
                              "suggested_evidence_level": "unknown | finite_support",
                              "open_obligations": ["remaining obligation"], "next_step": "next action or review",
                              "stop_reason": "candidate_answer | blocked",
                              "follow_up": {"result": "observed outcome", "scope": "assumptions and coverage",
                                            "artifacts": ["new broker-written evidence path"]}},
        },
        "instruction": "Review the first probe before handoff. A direct candidate answer needs a complete argument "
                       "or a counterexample for the exact locked target. Set next_attempt and "
                       "follow_up to null. Otherwise attempt the recorded follow-up on the original gap and write "
                       "its result or concrete obstruction with the broker. Enlarging the same finite scan alone "
                       "does not address an unresolved universal obligation. Paths are relative to return/ without "
                       "the return/ prefix. The broker writes return.json and probe-review.json. "
                       "A candidate answer always goes to independent validation.",
    }
    if deductive:
        result["progress_derivation"] = {"gap": "exact remaining obligation", "deduction": "actual inference or obstruction",
                                         "next_decision": "what this changes and how to test it"}
        result["handoff"]["return_record"]["source_checks"] = [{
            "theorem": "external result actually used", "use_point": "where the proof depends on it",
            "assumptions": {"hypothesis in original": "reason it applies or what remains unknown"},
            "passage_id": "ID returned by reuse_passage, or null when unread", "limitation": "scope and unresolved checks",
        }]
        result["instruction"] += (
            " After a limited probe, put an actual deduction or concrete mathematical obstruction in progress.derivation "
            "before running more code. For any later run, state what each outcome changes in the argument. "
            "Test a minimal decisive witness before expanding a search. Use run.output_path to archive raw tool "
            "output and read.if_sha256 to verify unchanged files without rereading their contents. "
            "Search by the missing theorem or mechanism. When the corpus yields no new passage and a request "
            "is pending, continue the conditional argument instead of repeatedly polling for delivery. "
            "For every load-bearing external theorem, identify the exact step and map its hypotheses. Cite the original "
            "via reuse_passage or record it as unread with a conditional scope. Use [] for a self-contained argument."
        )
    return result
