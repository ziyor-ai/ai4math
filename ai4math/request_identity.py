"""Frozen local request identities and fresh environments, not an OS sandbox."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .store import ResearchError, digest, file_hash, identifier

PROFILE = "fresh_local_v1"
POLICY = {"profile": PROFILE, "python_flags": ["-I", "-B"], "cache": "fresh_per_request_no_reuse",
          "environment": "minimal_allowlist_v1", "os_access_control": False,
          "network_blocked": False, "provider_session_created": False}


def workspace_identity(store):
    project = store.get("project", "main")
    return digest({"root": str(store.root), "project": {k: project[k] for k in ("id", "created_at", "schema_version")}})


def build_identity(store, task, context):
    if not isinstance(context, dict) or set(context) != {"trial_id", "arm", "request_id"}:
        raise ResearchError("request context must contain trial_id, arm and request_id")
    for key in ("trial_id", "request_id"):
        identifier(context[key])
    if context["arm"] not in {"A", "B", "C"}:
        raise ResearchError("request arm must be A/B/C")
    checker = task.get("validator_artifact")
    program = checker if task["role"] == "validate" else task.get("program_artifact")
    if not program:
        raise ResearchError("fresh local task needs a frozen program artifact")
    store.verify_blob(program)
    expected = [sys.executable, "{validator}" if task["role"] == "validate" else "{program}", "{input}"]
    if task["argv"] != expected:
        raise ResearchError("fresh local argv must use current Python, frozen program and task input")
    if program in task["inputs"]:
        raise ResearchError("program and public input artifacts must differ")
    payload = {"schema": "local-request-identity-v1", **context,
               "workspace_sha256": workspace_identity(store), "task_id": task["id"], "route_id": task["route"], "role": task["role"],
               "statement_digest": task["node_snapshot"]["statement_digest"],
               "input_artifacts": sorted(task["inputs"]), "program_artifact": program,
               "validator_artifact": checker, "method": task.get("method"), "method_review": task.get("method_review"),
               "argv": task["argv"], "interpreter_sha256": file_hash(Path(sys.executable)),
               "timeout_seconds": task["timeout_seconds"], "output_limit_bytes": task["output_limit_bytes"],
               "execution_policy_sha256": digest(POLICY)}
    return {**payload, "sha256": digest(payload)}


def verify_identity(store, task):
    identity = task["request_identity"]
    context = {k: identity[k] for k in ("trial_id", "arm", "request_id")}
    if build_identity(store, task, context) != identity:
        raise ResearchError("frozen request identity differs from task, program or interpreter")
    if store.get("request_identity", identity["request_id"]) != identity:
        raise ResearchError("request registry differs from task identity")
    return identity


def create_environment(store, task, folder):
    """Exclusive runtime directory means export/restart cannot adopt old caches."""
    identity = verify_identity(store, task)
    runtime = store.inside(folder / "runtime")
    try:
        runtime.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ResearchError("fresh runtime directory already exists; inspect it and use a new task id") from exc
    dirs = {}
    for name in ("home", "tmp", "cache", "config", "data"):
        path = runtime / name
        path.mkdir()
        dirs[name] = str(path)
    # All executable paths are absolute. Never inherit host PATH, HOME,
    # PYTHONPATH, provider keys or model/agent configuration variables.
    env = {"PATH": str(Path(sys.executable).parent), "HOME": dirs["home"], "USERPROFILE": dirs["home"],
           "TEMP": dirs["tmp"], "TMP": dirs["tmp"], "TMPDIR": dirs["tmp"],
           "APPDATA": dirs["config"], "LOCALAPPDATA": dirs["data"],
           "XDG_CACHE_HOME": dirs["cache"], "XDG_CONFIG_HOME": dirs["config"], "XDG_DATA_HOME": dirs["data"],
           "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
           "AI4MATH_REQUEST_ID": identity["request_id"], "AI4MATH_TRIAL_ID": identity["trial_id"],
           "AI4MATH_ARM": identity["arm"], "AI4MATH_REQUEST_SHA256": identity["sha256"]}
    if os.name == "nt":
        # SystemRoot is required by some Windows DLLs; it is not a user profile.
        system = next((v for k, v in os.environ.items() if k.upper() == "SYSTEMROOT"), None)
        if not system:
            raise ResearchError("Windows SystemRoot missing; no inherited-environment fallback")
        env.update(SystemRoot=system, WINDIR=system)
        env["PATH"] += os.pathsep + str(Path(system) / "System32")
    else:
        env.update(LANG="C.UTF-8", LC_ALL="C.UTF-8")
    return env, {"schema": "local-environment-receipt-v1", "request_identity_sha256": identity["sha256"],
                 "policy": POLICY, "environment": env, "environment_sha256": digest(env),
                 "directories": dirs, "study_isolation_ready": False,
                 "limitations": ["same OS user can access other absolute paths", "no filesystem ACL or network sandbox", "no CPU/memory OS limits", "no provider request or model revision attestation"]}


def result_binding(task):
    if task.get("execution_profile") != PROFILE:
        return {}
    identity = task["request_identity"]
    return {"request_identity_sha256": identity["sha256"], **{k: identity[k] for k in ("request_id", "trial_id", "arm")}}
