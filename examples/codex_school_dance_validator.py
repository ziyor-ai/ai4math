"""Finite verifier template. Only a frozen, pinned checker is executed, never a producer.

Materialize BOUND after selecting an actually sealed output. This is deliberately
not an algorithm implementation or a general School Dance proof certificate.
"""
import hashlib
import json
import sys
from pathlib import Path

BOUND = None


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError("nonfinite JSON number: " + value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def finite_statement(mechanism):
    if mechanism not in {"augmenting-path", "hall-deficiency", "subset-dp"}:
        raise ValueError("unrecognized mechanism")
    extra = (" Each graph also has a left subset S and its exact neighborhood N(S), "
             "with matching cardinality n - |S| + |N(S)|.") if mechanism == "hall-deficiency" else ""
    return (f"The {mechanism} output table bound to this claim covers the frozen corpus "
            "and contains a maximum matching for each graph." + extra)


def node_payload(corpus_sha, mechanism):
    return {"statement": finite_statement(mechanism),
            "domain": f"Frozen corpus SHA-256 {corpus_sha}.", "quantifier": "finite"}


def node_digest(corpus_sha, mechanism):
    return sha(json.dumps(node_payload(corpus_sha, mechanism), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False).encode())


def assess_output(base_assess, corpus, output, binding):
    mechanism = binding["mechanism"]
    required = {"task_id", "mechanism", "corpus_sha256", "matchings"}
    if mechanism == "hall-deficiency":
        required.add("hall_witnesses")
    if not isinstance(output, dict) or set(output) != required:
        raise ValueError("output fields must match the frozen schema exactly")
    for field, expected in (("task_id", binding["producer_task"]),
                            ("mechanism", mechanism), ("corpus_sha256", binding["corpus_sha256"])):
        if output[field] != expected:
            raise ValueError("output binding mismatch: " + field)
    if not isinstance(output["matchings"], dict):
        raise ValueError("matchings must be an object")
    failures, correct = base_assess(corpus, output)
    if mechanism == "hall-deficiency":
        witnesses = output["hall_witnesses"]
        if not isinstance(witnesses, dict) or set(witnesses) != set(output["matchings"]):
            raise ValueError("Hall witnesses must cover precisely the same case IDs")
        for case in corpus["cases"]:
            witness = witnesses[case["id"]]
            if not isinstance(witness, dict) or set(witness) != {"S", "N"}:
                raise ValueError("Hall witness requires exactly S and N")
            for field, bound in (("S", case["n"]), ("N", case["m"])):
                values = witness[field]
                if (not isinstance(values, list) or any(type(v) is not int or not 1 <= v <= bound for v in values)
                        or len(values) != len(set(values))):
                    raise ValueError("invalid Hall vertex subset: " + field)
            actual_neighbors = {v for u, v in case["edges"] if u in witness["S"]}
            if set(witness["N"]) != actual_neighbors:
                raise ValueError("Hall neighborhood is not exact")
            upper = case["n"] - len(witness["S"]) + len(witness["N"])
            if len(output["matchings"][case["id"]]) != upper:
                raise ValueError("matching does not saturate the Hall upper bound")
    return failures, correct


def verify(contract, binding):
    if contract["task_id"] != binding["validator_task"]:
        raise ValueError("validator task mismatch")
    expected = {binding[k] for k in ("corpus_sha256", "output_sha256", "base_checker_sha256")}
    artifacts = contract["input_artifacts"]
    if len(artifacts) != 3 or {a["id"] for a in artifacts} != expected or len(expected) != 3:
        raise ValueError("validator input set mismatch")
    data = {}
    for artifact in artifacts:
        raw = Path(artifact["path"]).read_bytes()
        if sha(raw) != artifact["id"]:
            raise ValueError("input bytes do not match artifact SHA")
        data[artifact["id"]] = raw
    node = contract["node"]
    expected_node = node_payload(binding["corpus_sha256"], binding["mechanism"])
    if (node["id"] != binding["node"] or any(node.get(k) != v for k, v in expected_node.items())
            or node["statement_digest"] != node_digest(binding["corpus_sha256"], binding["mechanism"])):
        raise ValueError("finite node mismatch")
    # The dependency is the original independent exhaustive checker, explicitly
    # frozen as a validator input. No producer source or package is loaded.
    namespace = {"__name__": "frozen_independent_checker"}
    exec(compile(data[binding["base_checker_sha256"]], "frozen_independent_checker", "exec"), namespace)
    corpus = strict_json(data[binding["corpus_sha256"]])
    output = strict_json(data[binding["output_sha256"]])
    failures, correct = assess_output(namespace["assess"], corpus, output, binding)
    result = {"task_id": contract["task_id"], "input_artifacts": sorted(expected),
              "statement_digest": node["statement_digest"], "method": "exhaustive_search",
              "evidence_level": "proved" if not failures else "unknown", "scope": "finite",
              "passed": not failures, "producer_task": binding["producer_task"],
              "correct_cases": len(correct), "total_cases": len(corpus["cases"]),
              "failed_cases": [case["id"] for case, _, _ in failures],
              "checks": ["Exact content-addressed inputs and finite node", "Independent complete corpus enumeration",
                         "Feasible matching and exhaustive edge-subset optimum",
                         "Exact Hall upper witness" if binding["mechanism"] == "hall-deficiency" else "Matching table only"],
              "trust_basis": "Python finite enumeration; reused frozen independent checker plus separate Hall witness checks. Not a proof assistant kernel.",
              "limitations": ["Only these 37 frozen graphs; not the full CSES domain.",
                              "Output tables do not establish the declared algorithm mechanism or general correctness.",
                              "No claim acceptance, research effectiveness conclusion, or producer provenance certification."]}
    return result


def main():
    if BOUND is None:
        raise ValueError("unbound checker template is not executable validation")
    result = verify(strict_json(Path(sys.argv[1]).read_bytes()), BOUND)
    with Path("result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)


if __name__ == "__main__":
    main()
