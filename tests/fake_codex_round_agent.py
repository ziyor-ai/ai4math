"""Offline App Server transcript for the round-bound managed driver test."""

import json
from pathlib import Path
import sys

from fake_worker_protocol import literature, prepare_return, send


def transcript():
    thread = "offline-thread"
    contract = None
    turn_count = 0
    call_count = 0
    for line in sys.stdin:
        request = json.loads(line)
        method = request["method"]
        if method == "initialized":
            continue
        rid = request["id"]
        if method == "initialize":
            send({"id": rid, "result": {}})
        elif method == "thread/start":
            send({"id": rid, "result": {"thread": {"id": thread}}})
        elif method == "turn/start":
            turn_count += 1
            turn = f"offline-turn-{turn_count}"
            send({"id": rid, "result": {"turn": {"id": turn}}})
            send({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": turn}}})

            def call(action, **payload):
                nonlocal call_count
                call_count += 1
                call_id = f"offline-call-{call_count}"
                event = {"threadId": thread, "turnId": turn,
                         "item": {"type": "dynamicToolCall", "id": call_id}}
                send({"method": "item/started", "params": event})
                send({"id": call_id, "method": "item/tool/call", "params": {
                    "threadId": thread, "turnId": turn, "callId": call_id, "tool": "ai4math_task",
                    "arguments": {"action": action, "payload": payload}}})
                reply = json.loads(sys.stdin.readline())
                assert reply["id"] == call_id
                result = reply["result"]
                assert result["success"], result["contentItems"]
                send({"method": "item/completed", "params": event})
                return json.loads(result["contentItems"][0]["text"])

            if turn_count == 1:
                contract = literature(call)
            else:
                assert turn_count == 2 and contract is not None
                prepare_return(call, contract)
                assert call("handoff", paths=["probe.txt", "return.json"])["prepared"]
            send({"method": "turn/completed", "params": {"threadId": thread,
                  "turn": {"id": turn, "status": "completed"}}})
        else:
            raise AssertionError(f"Unexpected App Server request {method}")


def run_worker(root, task, library):
    fixture = Path(__file__).resolve()
    sys.path.insert(0, str(fixture.parents[1]))
    from ai4math import codex_runner

    app_server = codex_runner.AppServer

    def start_fixture(argv, cwd, live, limit):
        return app_server([sys.executable, "-X", "utf8", "-B", str(fixture)], cwd, live, limit)

    codex_runner.AppServer = start_fixture
    return codex_runner.worker(root, task, str(fixture), library)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        sys.exit(run_worker(*sys.argv[2:]))
    transcript()
