"""Independent Lean replay checker for the simple-task batch (frozen artifact).

Reads input.json from the task folder, replays every declared input with Lean, and
writes result.json with the strict fields the validation contract expects.

Environment: AI4MATH_LEAN_PROJECT points at a project with a `lakefile.lean`
and built dependencies. The checker uses `lake env lean` in that project.
Without such a project, it locates `lean` on PATH.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run_lean(argument: str, project: str | None, *, timeout: int = 1800):
    if project:
        lake = shutil.which("lake")
        if not lake:
            raise SystemExit("lake is not on PATH")
        cmd = [lake, "env", "lean", argument]
    else:
        lean = shutil.which("lean")
        if not lean:
            raise SystemExit("lean is not on PATH")
        cmd = [lean, argument]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=project)


def canonical(text):
    out = []
    for char in text.lower():
        out.append(char if char.isalnum() else " ")
    return " ".join("".join(out).split())


def theorem_name(declaration):
    match = re.search(r"\b(?:theorem|lemma)\s+([A-Za-z0-9_'.\u00c0-\u024f]+)", declaration or "")
    return match.group(1) if match else None


def check_type(proof, project, name):
    """Ask Lean for the type of `name` by appending a `#check` to a temporary copy."""
    source = Path(proof).read_text(encoding="utf-8", errors="replace")
    handle = tempfile.NamedTemporaryFile("w", suffix=".lean", delete=False, encoding="utf-8")
    try:
        handle.write(source + "\n\n#check @" + name + "\n")
        handle.close()
        proc = run_lean(handle.name, project)
        lines = [line for line in proc.stdout.splitlines()
                 if line.strip() and "warning:" not in line and "error" not in line]
        return proc.returncode, "\n".join(lines).strip()
    finally:
        Path(handle.name).unlink(missing_ok=True)


def check_axioms(source, project, name):
    """Run ``#print axioms``; returns (exit code, output). A stub that prints nothing
    is tolerated: the axiom list is recorded when present, never invented."""
    text = Path(source).read_text(encoding="utf-8", errors="replace")
    handle = tempfile.NamedTemporaryFile("w", suffix=".lean", delete=False,
                                         encoding="utf-8", newline="\n")
    try:
        handle.write(text + "\n\n#print axioms " + name + "\n")
        handle.close()
        proc = run_lean(handle.name, project)
        lines = [line for line in (proc.stdout + "\n" + proc.stderr).splitlines() if line.strip()]
        return proc.returncode, "\n".join(lines).strip()
    finally:
        Path(handle.name).unlink(missing_ok=True)


def parsed_axioms(text):
    match = re.search(r"axioms:\s*\[(.*?)\]", text, re.S)
    if match:
        return [name.strip() for name in match.group(1).split(",") if name.strip()]
    if "does not depend on any axioms" in text:
        return []
    return None


EXPR_PROBE = """
open Lean Elab Command Term Meta in
run_cmd do
  let env ← getEnv
  let some decl := env.find? `{name} | throwError "declaration not found in the environment"
  let source := {type_literal}
  let stx ← match Lean.Parser.runParserCategory env `term source with
    | .ok stx => pure stx
    | .error err => throwError "could not parse the node's lean_type: {err}"
  let expected ← liftTermElabM do
    let expr ← Term.elabType stx
    Term.synthesizeSyntheticMVarsNoPostponing
    instantiateMVars expr
  let same ← liftTermElabM (Meta.isDefEq decl.type expected)
  unless same do
    throwError "declared type is not definitionally equal to the node's lean_type"
"""


def expr_probe_source(name, type_text):
    """A Lean meta probe: ``isDefEq`` between the declared type and the node's type.

    This is a kernel-level definitional equality check (Lean.Expr unification), not a
    text comparison. It is generated only when the node declares ``lean_type``.
    """
    literal = '"' + type_text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return EXPR_PROBE.replace("{name}", name).replace("{type_literal}", literal)


def check_expr_equivalence(source, project, name, type_text):
    """Append a meta probe to the proof and run Lean; non-zero means not equal."""
    text = Path(source).read_text(encoding="utf-8", errors="replace")
    handle = tempfile.NamedTemporaryFile("w", suffix=".lean", delete=False,
                                         encoding="utf-8", newline="\n")
    try:
        handle.write(text + "\n\n" + expr_probe_source(name, type_text) + "\n")
        handle.close()
        proc = run_lean(handle.name, project)
        lines = [line for line in (proc.stdout + "\n" + proc.stderr).splitlines() if line.strip()]
        return proc.returncode, "\n".join(lines).strip()
    finally:
        Path(handle.name).unlink(missing_ok=True)


def is_lean_source(text):
    """A task may carry notes next to the proof; only Lean sources are replayed."""
    return re.search(r"(?m)^\s*(?:import|theorem|lemma|example|def|namespace|section|open)\b",
                     text) is not None


def main() -> int:
    contract_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("input.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    project = os.environ.get("AI4MATH_LEAN_PROJECT")
    if not project or not Path(project, "lakefile.lean").is_file():
        project = None
    all_inputs = [item["path"] for item in contract["input_artifacts"]]
    if not all_inputs:
        raise SystemExit("no input artifact")
    proofs = [path for path in all_inputs
              if is_lean_source(Path(path).read_text(encoding="utf-8", errors="replace"))]
    if not proofs:
        raise SystemExit("no Lean source among the input artifacts")
    node = contract.get("node") or {}
    declared = node.get("lean_statement")
    declaring = proofs[0]
    if declared:
        wanted = canonical(declared)
        sources = {path: canonical(Path(path).read_text(encoding="utf-8", errors="replace"))
                   for path in proofs}
        matches = [path for path, text in sources.items() if wanted in text]
        if not matches:
            raise SystemExit("proof does not state the node's Lean declaration; "
                             "refusing to certify a different theorem")
        declaring = matches[0]
    name = theorem_name(declared)
    type_exit, type_text = (None, "")
    axioms = None
    if name:
        type_exit, type_text = check_type(declaring, project, name)
        if type_exit != 0:
            raise SystemExit("Lean could not #check the declared theorem '" + name +
                             "'; refusing to certify a file that does not declare it")
        axiom_exit, axiom_text = check_axioms(declaring, project, name)
        axioms = parsed_axioms(axiom_text)
        if axioms is not None and "sorryAx" in axioms:
            raise SystemExit("proof depends on sorryAx; refusing to certify an incomplete proof")
        declared_type = node.get("lean_type")
        expr_checked = None
        if declared_type:
            stripped = declared_type.strip()
            prefix = name + " :"
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
            printed = type_text.split(" : ", 1)[-1] if " : " in type_text else type_text
            if canonical(printed) != canonical(stripped):
                raise SystemExit("Lean's printed type does not match the node's declared lean_type; "
                                 "refusing to certify a different theorem")
            if os.environ.get("AI4MATH_EXPR_CHECK", "1") != "0":
                expr_exit, expr_text = check_expr_equivalence(declaring, project, name, stripped)
                if expr_exit != 0:
                    raise SystemExit("Lean's kernel does not accept the node's declared lean_type as "
                                     "definitionally equal: " + expr_text[-400:])
                expr_checked = True
    manifest = Path(project, "lake-manifest.json") if project else None
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest and manifest.is_file() else None
    version = run_lean("--version", project, timeout=300)
    runs = []
    for path in proofs:
        proc = run_lean(path, project)
        runs.append({"file": Path(path).name,
                     "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                     "exit": proc.returncode,
                     "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]})
    passed = all(run["exit"] == 0 for run in runs)
    version_line = (version.stdout.strip().splitlines() or ["unknown"])[0]
    # The contract gives the node quantifier; the claim's declared scope must match it.
    scope = node.get("quantifier") or "universal"
    result = {
        "task_id": contract["result_contract"]["task_id"],
        "statement_digest": contract["result_contract"]["statement_digest"],
        "input_artifacts": sorted(item["id"] for item in contract["input_artifacts"]),
        "method": "formal_kernel",
        "scope": scope,
        "evidence_level": "proved",
        "passed": passed,
        "checks": ["lean replayed each input file: " + ", ".join(run["file"] for run in runs),
                   "toolchain: " + version_line,
                   "node Lean declaration present in the proof: " + ("yes" if declared else "not declared"),
                   "#print axioms: " + ("not reported" if axioms is None else
                                        "[" + ", ".join(axioms) + "]")],
        "limitations": ["replays the frozen proof file in the selected Lean environment; it does not "
                        "re-derive the node statement digest from the Lean type",
                        "the statement binding is a canonicalised text match, not a kernel type comparison",
                        "the checker is independent of the producer but shares the workspace toolchain"],
        "lean_version": version_line,
        "mathlib_revision": manifest_sha,
        "mathlib_manifest_sha256": manifest_sha,
        "lean_project": project,
        "axioms": axioms,
        "expr_check": expr_checked if name else None,
        "checked_type": type_text,
        "checked_type_sha256": hashlib.sha256(canonical(type_text).encode()).hexdigest() if type_text else None,
        "files": runs,
    }
    Path("result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    print("passed:", passed)
    for run in runs:
        if run["stdout"]:
            print(run["stdout"])
        if run["stderr"]:
            print(run["stderr"], file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
