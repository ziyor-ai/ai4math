"""Independent finite checker using edge subsets. It never imports or runs a constructor."""
import hashlib
import itertools
import json
import sys
from pathlib import Path


def matching(edges):
    return all(len({edge[side] for edge in edges}) == len(edges) for side in (0, 1))


def optimum(edges, bound):
    for size in range(bound, -1, -1):
        for subset in itertools.combinations(edges, size):
            if matching(subset):
                return subset
    raise ValueError("empty matching missing")


def corpus_keys():
    expected = {(3, 2, ((1, 1), (1, 2), (2, 1), (3, 1)))}
    for n in range(1, 4):
        for m in range(1, 5 - n):
            edges = tuple(itertools.product(range(1, n + 1), range(1, m + 1)))
            for size in range(1, len(edges) + 1):
                expected.update((n, m, subset) for subset in itertools.combinations(edges, size))
    return expected


def size_key(case):
    return case["n"] + case["m"], len(case["edges"])


def expected_greedy(edges):
    if not edges:
        return []
    first = min(edges)
    remaining = [e for e in edges if e[0] != first[0] and e[1] != first[1]]
    return [first, *expected_greedy(remaining)]


def assess(benchmark, output):
    cases = benchmark["cases"]
    actual_keys = {(c["n"], c["m"], tuple(map(tuple, c["edges"]))) for c in cases}
    if actual_keys != corpus_keys() or len(actual_keys) != len(cases):
        raise ValueError("benchmark differs from the complete declared corpus")
    submitted = output["matchings"]
    ids = {c["id"] for c in cases}
    if len(ids) != len(cases) or set(submitted) != ids:
        raise ValueError("matching table does not cover the corpus exactly")
    failures, correct = [], set()
    for case in cases:
        edges = case["edges"]
        chosen = submitted[case["id"]]
        if (not isinstance(chosen, list) or not all(isinstance(e, list) and len(e) == 2
                and all(type(v) is int for v in e) and e in edges for e in chosen) or not matching(chosen)):
            raise ValueError("invalid matching in " + case["id"])
        best = optimum(edges, min(case["n"], case["m"]))
        if len(chosen) == len(best):
            correct.add(case["id"])
        else:
            failures.append((case, chosen, best))
    return failures, correct


def counterexample(cases, output, failures, hypothesis):
    if any(output["matchings"][c["id"]] != expected_greedy(c["edges"]) for c in cases):
        raise ValueError("outputs do not implement the declared deterministic greedy rule")
    case, chosen, best = min(failures, key=lambda item: (size_key(item[0]), item[0]["id"]))
    return {"hypothesis": hypothesis, "case_id": case["id"],
            "search_domain": "All nonempty simple labeled bipartite graphs with positive part sizes and at most four vertices in total",
            "ordering": "Total vertices, then edge count. Case ID breaks ties among minimal failures.",
            "smaller_cases_checked": sum(size_key(c) < size_key(case) for c in cases), "minimality_checked": True,
            "witness": {"n": case["n"], "m": case["m"], "edges": case["edges"], "greedy": chosen, "larger_matching": best}}


def main():
    contract = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    docs = {a["id"]: json.loads(Path(a["path"]).read_text(encoding="utf-8")) for a in contract["input_artifacts"]}
    benchmark_id, = [key for key, doc in docs.items() if "cases" in doc]
    benchmark = docs[benchmark_id]
    output, = [doc for doc in docs.values() if "matchings" in doc]
    failures, correct = assess(benchmark, output)
    result = {"task_id": contract["task_id"], "input_artifacts": sorted(docs), "scope": "finite",
              "checks": ["Exact complete corpus, feasible matchings, and exhaustive subset optimality"],
              "limitations": ["Only the frozen public sample and complete small graph corpus. The judge limits and general algorithm are outside this certificate."]}
    if "exploration" in contract:
        context = contract["exploration"]
        candidate = context["candidate"]
        result.update(candidate=candidate["id"], candidate_digest=candidate["candidate_digest"],
                      evaluation_protocol_sha256=context["evaluation_protocol"]["sha256"],
                      valid=not failures, metrics={"quality": len(correct)})
        if failures and candidate["mechanism_key"] == "lex-greedy":
            result["counterexample"] = counterexample(benchmark["cases"], output, failures, candidate["hypothesis"])
        if "counterexample" in context:
            case_id = context["counterexample"]["case_id"]
            result["rechecked_cases"] = [case_id] if case_id in correct else []
    else:
        expected = f"Frozen corpus SHA-256 {benchmark_id}."
        statement = "The bound matching table covers the frozen corpus and contains a maximum matching for each graph."
        node = contract["node"]
        # Match the finite proposition, not just the shapes of its inputs.
        bound = node["statement"] == statement and node["domain"] == expected and node["quantifier"] == "finite"
        payload = {"statement": statement, "domain": expected, "quantifier": "finite"}
        sha256 = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        result.update(statement_digest=node["statement_digest"], method="exhaustive_search", evidence_level="proved",
                      passed=bound and node["statement_digest"] == sha256 and not failures)
    Path("result.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
