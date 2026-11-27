"""A stand-in coding agent for the tests: it speaks the Agent Client Protocol on stdin and stdout.

What it does with a prompt depends on the prompt's first word:
  echo TEXT      answers TEXT in two pieces, after a thought
  tool           reports a tool call that edits a file, then completes it
  ask            asks for permission, then says which option was chosen
  plan           sends a plan
  mcp            says which MCP servers the session was opened with
  wait           does nothing until the turn is cancelled
  silent         ends the turn without a word
  die            exits with code 3
"""

import json
import sys

state = {"servers": [], "cancelled": False, "next": 100, "pending": None}


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def update(payload):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s1", "update": payload}})


def say(text, kind="agent_message_chunk"):
    update({"sessionUpdate": kind, "content": {"type": "text", "text": text}})


def done(request_id, reason="end_turn"):
    send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": reason}})


def prompt(request_id, text):
    word, _, rest = text.partition(" ")
    if word == "echo":
        say("thinking about it", "agent_thought_chunk")
        say(rest[: len(rest) // 2])
        say(rest[len(rest) // 2 :])
    elif word == "tool":
        update({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Edit notes.txt", "kind": "edit", "status": "pending"})
        update({
            "sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
            "content": [{"type": "diff", "path": "/tmp/notes.txt", "oldText": "one\ntwo\n", "newText": "one\nthree\n"}],
        })
        say("edited")
    elif word == "ask":
        state["next"] += 1
        state["pending"] = (state["next"], request_id)
        send({"jsonrpc": "2.0", "id": state["next"], "method": "session/request_permission", "params": {
            "sessionId": "s1", "toolCall": {"toolCallId": "t2", "title": "Run rm -rf build"},
            "options": [
                {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                {"optionId": "no", "name": "Reject", "kind": "reject_once"},
            ],
        }})
        return
    elif word == "plan":
        update({"sessionUpdate": "plan", "entries": [
            {"content": "Read the cells", "priority": "high", "status": "completed"},
            {"content": "Fix the loop", "priority": "high", "status": "in_progress"},
        ]})
    elif word == "mcp":
        say(json.dumps(state["servers"]))
    elif word == "wait":
        state["pending"] = (None, request_id)
        return
    elif word == "silent":
        pass
    elif word == "die":
        sys.stderr.write("fake agent: out of luck\n")
        sys.exit(3)
    else:
        say(f"context: {text}")
    done(request_id)


for line in sys.stdin:
    message = json.loads(line)
    method, request_id = message.get("method"), message.get("id")
    params = message.get("params") or {}
    if method == "initialize":
        if "--no-login" in sys.argv:
            send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1, "agentInfo": {"name": "fake"}}})
        else:
            send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1, "agentInfo": {"name": "fake", "title": "Fake Agent"}}})
    elif method == "session/new":
        if "--no-login" in sys.argv:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "Authentication required"}})
        else:
            state["servers"] = params.get("mcpServers")
            send({"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "s1"}})
    elif method == "session/prompt":
        blocks = params.get("prompt") or []
        if "--context" in sys.argv:
            say(" | ".join(block.get("text", "") for block in blocks))
            done(request_id)
        else:
            prompt(request_id, blocks[0].get("text", ""))
    elif method == "session/cancel":
        if state["pending"] is not None:
            done(state["pending"][1], "cancelled")
            state["pending"] = None
    elif method is None and state["pending"] is not None and request_id == state["pending"][0]:
        outcome = (message.get("result") or {}).get("outcome") or {}
        say(f"chose {outcome.get('optionId') or outcome.get('outcome')}")
        done(state["pending"][1])
        state["pending"] = None
    elif request_id is not None:
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
