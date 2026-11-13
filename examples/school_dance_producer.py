"""Candidate matching constructors for the public School Dance development case."""
import json
import sys
from pathlib import Path


def greedy(edges):
    left, right, chosen = set(), set(), []
    for a, b in sorted(edges):
        if a not in left and b not in right:
            chosen.append([a, b])
            left.add(a)
            right.add(b)
    return chosen


def augmenting_matching(n, edges):
    neighbors = {a: [] for a in range(1, n + 1)}
    for a, b in sorted(edges):
        neighbors[a].append(b)
    owner = {}

    def augment(a, seen):
        for b in neighbors[a]:
            if b in seen:
                continue
            seen.add(b)
            if b not in owner or augment(owner[b], seen):
                owner[b] = a
                return True
        return False

    for a in neighbors:
        augment(a, set())
    return sorted([a, b] for b, a in owner.items())


def main():
    contract = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    context = contract["exploration"]
    benchmark_id, = context["evaluation_protocol"]["benchmark_artifacts"]
    paths = {a["id"]: Path(a["path"]) for a in contract["input_artifacts"]}
    benchmark = json.loads(paths[benchmark_id].read_text(encoding="utf-8"))
    strategy = context["candidate"]["mechanism_key"]
    constructors = {"lex-greedy": lambda case: greedy(case["edges"]),
                    "augmenting-path": lambda case: augmenting_matching(case["n"], case["edges"])}
    matchings = {case["id"]: constructors[strategy](case) for case in benchmark["cases"]}
    Path("result.json").write_text(json.dumps({"matchings": matchings}, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
