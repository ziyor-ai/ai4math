"""Transport-neutral fixtures for the managed worker lifecycle."""

import json
import sys


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def read_artifact(call, artifact_id):
    parts = []
    offset = 0
    while offset is not None:
        page = call("read", artifact_id=artifact_id, offset=offset)
        parts.append(page["text"])
        offset = page["next_offset"]
    return "".join(parts)


def literature(call):
    contract = call("contract")
    read_artifact(call, contract["prompt_artifact_id"])
    for artifact_id in contract["input_ids"]:
        read_artifact(call, artifact_id)
    call("search", query="offline fixture")
    call("literature_ready", sources=[], limitations="Offline fixture only",
         exploration_plan="Run the deterministic probe")
    return contract


def prepare_return(call, contract):
    probe = call("run", argv=["python", "-c", "print('Offline fixture')"], output_path="probe.txt")
    record = {**contract["return_contract"]["bindings"], "contract_sha256": contract["contract_sha256"],
              "artifacts": ["probe.txt"], "artifact_sha256": {"probe.txt": probe["sha256"]},
              "summary": "Offline broker probe", "scope": "Fixture only", "suggested_evidence_level": "unknown",
              "first_probe": {"test": "Print a fixed marker", "outcome": "Marker observed", "scope": "Fixture",
                              "artifacts": ["probe.txt"]},
              "open_obligations": ["No mathematical assertion"], "next_step": "Coordinator review"}
    call("write", path="return.json", text=json.dumps(record))
