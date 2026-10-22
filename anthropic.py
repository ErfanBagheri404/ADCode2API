"""Anthropic Messages API <-> OpenAI chat completions translation."""
import json


def anthropic_to_openai(ar):
    payload = {"model": ar.get("model", ""), "stream": True}
    if "max_tokens" in ar:
        payload["max_tokens"] = ar["max_tokens"]

    messages = []
    sys = ar.get("system")
    if sys:
        if isinstance(sys, str):
            messages.append({"role": "system", "content": sys})
        elif isinstance(sys, list):
            messages.append({"role": "system",
                             "content": "\n".join(b.get("text", "") for b in sys if b.get("type") == "text")})

    for m in ar.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            for blk in content:
                if blk.get("type") == "text":
                    messages.append({"role": role, "content": blk["text"]})
                elif blk.get("type") == "tool_use":
                    messages.append({"role": "assistant", "content": None,
                                     "tool_calls": [{"id": blk.get("id", ""), "type": "function",
                                                     "function": {"name": blk["name"],
                                                                  "arguments": json.dumps(blk.get("input", {}))}}]})
                elif blk.get("type") == "tool_result":
                    c = blk.get("content", "")
                    messages.append({"role": "tool", "tool_call_id": blk.get("tool_use_id", ""),
                                     "content": c if isinstance(c, str) else json.dumps(c)})
    payload["messages"] = messages

    tools = ar.get("tools")
    if tools:
        payload["tools"] = [{"type": "function",
                             "function": {"name": t.get("name", ""),
                                          "description": t.get("description", ""),
                                          "parameters": t.get("input_schema")}} for t in tools]
    return payload


def _chunks(rbody):
    for line in rbody.decode("utf-8", "replace").split("\n"):
        if not line.startswith("data: ") or line[6:] == "[DONE]":
            continue
        try:
            yield json.loads(line[6:])
        except ValueError:
            continue


def anthropic_response(rbody, model):
    text, calls, finish, msg_id = [], [], "end_turn", ""
    for c in _chunks(rbody):
        msg_id = c.get("id") or msg_id
        for ch in c.get("choices", []):
            d = ch.get("delta", {})
            if d.get("content"):
                text.append(d["content"])
            for tc in d.get("tool_calls", []):
                if tc.get("id"):
                    calls.append({"id": tc["id"], "name": tc["function"].get("name", ""),
                                  "input": tc["function"].get("arguments", "")})
                elif calls:
                    calls[-1]["input"] += tc["function"].get("arguments", "")
            if ch.get("finish_reason"):
                finish = {"tool_calls": "tool_use", "length": "max_tokens"}.get(ch["finish_reason"], "end_turn")

    content = []
    if text:
        content.append({"type": "text", "text": "".join(text)})
    for i, tc in enumerate(calls):
        try:
            args = json.loads(tc["input"] or "{}")
        except ValueError:
            args = {}
        content.append({"type": "tool_use", "id": f"toolu_{i}_{msg_id}", "name": tc["name"], "input": args})
    return {"id": msg_id or "msg_adcode", "type": "message", "role": "assistant", "content": content,
            "model": model, "stop_reason": finish,
            "usage": {"input_tokens": 1, "output_tokens": max(1, len("".join(text)) // 4)}}


def anthropic_stream_response(rbody, model):
    idx, in_tool = 0, False
    yield _sse("message_start", {"type": "message_start", "message": {
        "id": "msg_adcode", "type": "message", "role": "assistant", "content": [],
        "model": model, "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 0}}})
    for c in _chunks(rbody):
        for ch in c.get("choices", []):
            d = ch.get("delta", {})
            if d.get("content"):
                if in_tool:
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
                    idx += 1
                    in_tool = False
                yield _sse("content_block_delta", {"type": "content_block_delta", "index": idx,
                                                   "delta": {"type": "text_delta", "text": d["content"]}})
            for tc in d.get("tool_calls", []):
                if tc.get("id"):
                    if in_tool:
                        yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
                        idx += 1
                    in_tool = True
                    yield _sse("content_block_start", {"type": "content_block_start", "index": idx,
                                                       "content_block": {"type": "tool_use", "id": tc["id"],
                                                                         "name": tc["function"].get("name", ""),
                                                                         "input": {}}})
                args = tc["function"].get("arguments", "")
                if args and in_tool:
                    yield _sse("content_block_delta", {"type": "content_block_delta", "index": idx,
                                                       "delta": {"type": "input_json_delta", "partial_json": args}})
            if ch.get("finish_reason"):
                if in_tool:
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
                    in_tool = False
                yield _sse("message_delta", {"type": "message_delta",
                                             "delta": {"stop_reason": {"tool_calls": "tool_use",
                                                                       "length": "max_tokens"}.get(ch["finish_reason"], "end_turn")},
                                             "usage": {"output_tokens": 1}})
                yield _sse("message_stop", {"type": "message_stop"})


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
