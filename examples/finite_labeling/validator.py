"""Independent exact proof for P_2^4: distinct positive labels give M>=2."""
import json
import math
import sys
from pathlib import Path

contract = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert len(contract["input_artifacts"]) == 1
candidate = json.loads(Path(contract["input_artifacts"][0]["path"]).read_text(encoding="utf-8"))
# Fixed statement and domain; a changed problem needs a changed validator.
assert contract["node"]["statement"] == "The minimum coprime labeling number of P_2^4 is 2."
assert contract["node"]["domain"] == "Two distinct positive integer labels; adjacent vertices must have gcd 1."
assert contract["node"]["quantifier"] == "finite"
assert candidate["n"] == 2 and candidate["minimum"] == 2
labels = candidate["labels"]
assert len(labels) == 2 and all(type(a) is int and a > 0 for a in labels)
assert len(set(labels)) == 2 and math.gcd(*labels) == 1 and max(labels) == 2
# Independent necessary lower bound: only one positive integer is <=1, so
# two DISTINCT positive labels cannot have maximum <=1. This closes optimality.
assert len(range(1, 2)) < 2
result = dict(contract["result_contract"])
result.update(method="finite_certificate", scope="finite", evidence_level="proved", passed=True,
              checks=["positive distinct labels", "gcd(1,2)=1", "maximum label 2", "capacity below 2 is 1 < 2"],
              limitations=["Only the fixed graph P_2^4; no statement about other n or all k.",
                           "Trusts Python integer arithmetic and this explicitly supplied checker."])
Path("result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print("Witness and independent necessary lower bound checked for n=2.")
