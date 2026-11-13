"""Independently check exact finite hyperfield witnesses for the diagnosis demo."""

import itertools
import json
from pathlib import Path
import sys


ORIGINAL = "Every null triple of nonzero elements in a hyperfield has a common quadratic witness."
REVISED = "Every such null triple has a common quadratic witness when at least one quadratic sum is a singleton."
PAIRWISE = "In the given five-element hyperfield all null triples of units have pairwise intersecting quadratic sums."
TARGET = "Does every null triple of nonzero hyperfield elements have a null six-term cubic sum?"


def inspect(witness):
    table, triple = witness["addition"], witness["triple"]
    elements = range(5)
    units = range(4)
    zero = 4
    assert len(table) == 5 and all(len(row) == 5 for row in table)
    assert all(entry and set(entry) <= set(elements) for row in table for entry in row)
    assert len(triple) == 3 and all(x in units for x in triple)

    def add(x, y):
        return set(table[x][y])

    def multiply(x, y):
        return zero if zero in (x, y) else x ^ y

    for x, y, z in itertools.product(elements, repeat=3):
        assert add(x, zero) == {x} and add(x, y) == add(y, x)
        assert (zero in add(x, y)) == (x == y)
        assert (z in add(x, y)) == (y in add(x, z))
        assert {c for b in add(x, y) for c in add(b, z)} == {c for b in add(y, z) for c in add(x, b)}
        assert {multiply(x, b) for b in add(y, z)} == add(multiply(x, y), multiply(x, z))

    def quadratic(x, y, z):
        return [add(multiply(x, x), multiply(y, z)), add(multiply(y, y), multiply(x, z)),
                add(multiply(z, z), multiply(x, y))]

    for x, y, z in itertools.product(units, repeat=3):
        if z in add(x, y):
            assert all(a & b for a, b in itertools.combinations(quadratic(x, y, z), 2))
    x, y, z = triple
    assert z in add(x, y)
    sets = quadratic(x, y, z)
    mixed = multiply(x, multiply(y, z))
    total = {zero}
    for term in (x, y, z, mixed, mixed, mixed):
        total = {a for b in total for a in add(b, term)}
    return {"common": bool(set.intersection(*sets)), "singleton": any(len(s) == 1 for s in sets),
            "cubic_null": zero in total}


def main():
    path, = sys.argv[1:]
    contract = json.loads(Path(path).read_text(encoding="utf-8"))
    inputs = contract["input_artifacts"]
    results = [inspect(json.loads(Path(item["path"]).read_text(encoding="utf-8"))) for item in inputs]
    result = {**contract["result_contract"], "scope": "finite", "passed": True,
              "checks": ["Verified all addition, associativity, reversibility and distributivity cases"],
              "limitations": ["Exact finite tables with the Klein four unit group and self-negation"]}
    statement = contract["node"]["statement"]
    if statement == ORIGINAL:
        checked, = results
        assert not checked["common"] and checked["cubic_null"]
        result.update(method="counterexample", evidence_level="disproved")
    elif statement == PAIRWISE:
        checked, = results
        result.update(method="finite_certificate", evidence_level="proved")
    elif statement == REVISED:
        checked, = results
        assert not checked["singleton"]
        result["diagnosis"] = {"kind": "revision_replay", "outcome": "excluded_by_condition",
                               "condition": "At least one quadratic sum is a singleton",
                               "reason": "Each quadratic sum in the old witness has size two."}
        result.update(method="diagnostic_replay", evidence_level="finite_support")
    else:
        assert statement == TARGET and len(results) == 2
        assert results[0]["common"] != results[1]["common"]
        result["diagnosis"] = {"kind": "indistinguishability",
            "witnesses": {item["id"]: {"realization": "actual", "conclusion": row["common"],
                "invariants": {"order": 5, "unit_group": "Klein four", "premise_null": True, "pairwise_overlap": True}}
                for item, row in zip(inputs, results)}}
        result.update(method="diagnostic_comparison", evidence_level="finite_support")
    Path("result.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
