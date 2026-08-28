#!/usr/bin/env python3
"""Protocol stub for the persisted-fork answerer test."""

import json
import sys


turn = 0
fork = 0


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    message = json.loads(raw)
    if "id" not in message:
        continue
    request_id = message["id"]
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        send({"id": request_id, "result": {"userAgent": "fake-app-server"}})
    elif method == "model/list":
        send({"id": request_id, "result": {"data": [
            {"id": "gpt-test-sol", "model": "gpt-test-sol", "displayName": "Test Sol",
             "defaultReasoningEffort": "medium", "isDefault": True,
             "supportedReasoningEfforts": [
                 {"reasoningEffort": "low", "description": "fast"},
                 {"reasoningEffort": "medium", "description": "balanced"},
                 {"reasoningEffort": "high", "description": "deep"}]},
            {"id": "gpt-test-terra", "model": "gpt-test-terra", "displayName": "Test Terra",
             "defaultReasoningEffort": "low", "isDefault": False,
             "supportedReasoningEfforts": [{"reasoningEffort": "low"}]},
        ], "nextCursor": None}})
    elif method == "thread/fork":
        fork += 1
        parent = params["threadId"]
        child = "fork-child-%s" % fork
        send({"id": request_id, "result": {"thread": {
            "id": child,
            "sessionId": parent,
            "forkedFromId": parent,
        }}})
        send({"method": "thread/started", "params": {"thread": {"id": child}}})
    elif method == "thread/resume":
        send({"id": request_id, "result": {"thread": {"id": params["threadId"]}}})
    elif method == "thread/name/set":
        send({"id": request_id, "result": {}})
    elif method == "turn/start":
        turn += 1
        turn_id = "turn-%s" % turn
        text = "Forked answer %s from inherited task context. [%s/%s]" % (
            turn, params.get("model") or "default", params.get("effort") or "default")
        send({"id": request_id, "result": {"turn": {
            "id": turn_id, "status": "inProgress", "items": [], "error": None,
        }}})
        send({"method": "item/completed", "params": {
            "threadId": params["threadId"],
            "turnId": turn_id,
            "item": {"type": "agentMessage", "id": "item-%s" % turn,
                     "text": text, "phase": "final_answer"},
        }})
        send({"method": "turn/completed", "params": {
            "threadId": params["threadId"],
            "turn": {"id": turn_id, "status": "completed", "items": [], "error": None},
        }})
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "unknown method"}})
