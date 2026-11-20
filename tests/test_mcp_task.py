"""The MCP task server exposes the same worker tool the Codex driver injects."""
import json
import subprocess
import sys

import pytest

from ai4math.mcp_task import TaskServer
from test_codex_monitored_contract import dispatch, setup


def running_task(tmp_path):
    w, payload = setup(tmp_path)
    w.apply("task.add", payload)
    dispatch(w)
    return w


def call(server, rid, action, **payload):
    reply = server.handle({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                           "params": {"name": "ai4math_task",
                                      "arguments": {"action": action, "payload": payload}}})
    return reply


def test_initialize_and_tools_list(tmp_path):
    w = running_task(tmp_path)
    try:
        server = TaskServer(w.store.root, "managed", str(tmp_path))
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert init["result"]["protocolVersion"] == "2024-11-05"
        assert init["result"]["capabilities"] == {"tools": {}}
        listing = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert [tool["name"] for tool in listing["result"]["tools"]] == ["ai4math_task"]
    finally:
        w.store.close()


def test_contract_call_reaches_the_worker_tools(tmp_path):
    w = running_task(tmp_path)
    try:
        server = TaskServer(w.store.root, "managed", str(tmp_path))
        reply = call(server, 3, "contract")
        assert reply["result"]["isError"] is False
        body = json.loads(reply["result"]["content"][0]["text"])
        assert isinstance(body, dict) and body
        capture = w.store.root / "tasks/managed/live/tools/00000/result.json"
        assert capture.is_file()
    finally:
        w.store.close()


def test_phase_rejects_actions_outside_the_stage(tmp_path):
    w = running_task(tmp_path)
    try:
        server = TaskServer(w.store.root, "managed", str(tmp_path))
        reply = call(server, 4, "handoff", paths=["return.json"])
        assert reply["result"]["isError"] is True
        assert "not available" in reply["result"]["content"][0]["text"] or "needs" in reply["result"]["content"][0]["text"]
    finally:
        w.store.close()


def test_unknown_method_is_an_error(tmp_path):
    w = running_task(tmp_path)
    try:
        server = TaskServer(w.store.root, "managed", str(tmp_path))
        reply = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/nope"})
        assert reply["error"]["code"] == -32601
    finally:
        w.store.close()


def test_stdio_loop_answers_in_order(tmp_path):
    w = running_task(tmp_path)
    try:
        request = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n"
        result = subprocess.run(
            [sys.executable, "-m", "ai4math", "--workspace", str(w.store.root), "mcp-task",
             "--task", "managed", "--library", str(tmp_path)],
            input=request, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert [row["id"] for row in replies] == [1, 2]
        assert replies[0]["result"]["serverInfo"]["name"] == "ai4math-task"
    finally:
        w.store.close()
