"""Boilerplate guard for round designs (Batch 8.1, frozen baseline §5.1).

The seven strings below were the canned text every round used to submit as its own
"mechanism", "falsifier" and "bridge". They are now registered here, hashed, and
rejected verbatim: a round has to state a design in its own words, and a batch has
to spread over structurally different families.
"""
from .store import ResearchError, digest

FROZEN = (
    "row-level comparison on the frozen field",
    "compare the frozen rows on the actual pressure field",
    "no row pays the frozen scale",
    "one admissible datum closes the residual",
    "row-level signed datum",
    "read rows, compare data, publish a probe",
    "both rows fail the scale",
)
REQUIRED = ("mechanism", "proposed_tool", "falsifier", "representation", "bridge", "plan")
MIN_CHARS = 20


def canonical(text):
    if not isinstance(text, str):
        return ""
    out = []
    for char in text.lower():
        out.append(char if char.isalnum() else " ")
    return " ".join("".join(out).split())


FROZEN_CANONICAL = frozenset(canonical(text) for text in FROZEN)


def registry():
    return {"count": len(FROZEN), "sha256": digest(sorted(FROZEN_CANONICAL))}


def check_design(design):
    """Reject a design that is empty, canned, or a renamed copy of another field."""
    if not isinstance(design, dict):
        raise ResearchError("a round needs an explicit design object; there is no default mechanism")
    for key in REQUIRED:
        value = design.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ResearchError("round design needs a nonempty " + key)
        if len(canonical(value).replace(" ", "")) < 8:
            raise ResearchError("round design field is too short to be a real design: " + key)
        if canonical(value) in FROZEN_CANONICAL:
            raise ResearchError("round design repeats registered boilerplate in " + key)
    seen = {}
    for key in REQUIRED:
        form = canonical(design[key])
        if form in seen:
            raise ResearchError("round design reuses the same text for " + seen[form] + " and " + key)
        seen[form] = key
    return design


def check_families(specs):
    """A batch must spread over at least two structural families, not two card ids."""
    families = []
    for spec in specs:
        family = spec.get("family")
        if not isinstance(family, str) or not family.strip():
            raise ResearchError("every round in a batch needs a structural family")
        families.append(canonical(family))
    if len(specs) > 1 and len(set(families)) < 2:
        raise ResearchError("parallel rounds need at least two structurally distinct families")
    return families
