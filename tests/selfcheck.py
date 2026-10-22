"""Offline self-checks: translation, token store, request shaping."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402


def test_anthropic_roundtrip():
    req = {
        "model": "tagflow-code-27b",
        "max_tokens": 64,
        "system": [{"type": "text", "text": "be brief"}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
    }
    oai = anthropic.anthropic_to_openai(req)
    assert oai["model"] == "tagflow-code-27b"
    assert oai["messages"][0] == {"role": "system", "content": "be brief"}
    assert oai["messages"][1] == {"role": "user", "content": "hi"}
    assert oai["tools"][0]["function"]["name"] == "f"
    assert oai["stream"] is True


def test_non_stream_translation():
    sse = (b'data: {"id":"chatcmpl-1","choices":[{"delta":{"role":"assistant","content":"PO"}}]}\n\n'
           b'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"NG"},"finish_reason":"stop"}]}\n\n'
           b'data: [DONE]\n\n')
    out = anthropic.anthropic_response(sse, "m")
    assert out["content"] == [{"type": "text", "text": "PONG"}], out
    assert out["stop_reason"] == "end_turn"


def test_stream_translation():
    sse = (b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
           b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')
    events = list(anthropic.anthropic_stream_response(sse, "m"))
    assert events[0].startswith("event: message_start")
    assert any("text_delta" in e for e in events)
    assert events[-1].startswith("event: message_stop")


def test_token_store_roundtrip():
    import auth
    old = auth.STORE
    with tempfile.TemporaryDirectory() as d:
        auth.STORE = os.path.join(d, "auth.json")
        try:
            assert auth.load() is None
            auth.save({"uid": "u1", "refreshToken": "r1"}, "google")
            got = auth.load()
            assert got["uid"] == "u1" and got["provider"] == "google"
        finally:
            auth.STORE = old


def test_client_headers():
    import client
    h = client._headers("tok", {"x": "1"})
    assert h["authorization"] == "Bearer tok"
    assert h["user-agent"].startswith("ADCode/"), "Cloudflare blocks the default Python UA"
    assert h["x"] == "1"


def test_sanitize_drops_unsupported_fields():
    import server
    body = {
        "model": "adcode/heabsy-qwen38",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"},
                                                  {"type": "image_url", "image_url": {"url": "x"}}]}],
        "max_tokens": 32, "temperature": 0.7, "top_p": 1, "user": "u",
        "response_format": {"type": "text"}, "stop": None, "n": 1,
    }
    out = server.sanitize(body)
    assert out["model"] == "heabsy-qwen38", "router prefixes must be stripped"
    assert out["messages"] == [{"role": "user", "content": "hi"}], out["messages"]
    assert set(out) == {"model", "messages", "max_tokens"}, out


def test_sanitize_keeps_tools():
    import server
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    out = server.sanitize({"model": "m", "messages": [{"role": "user", "content": "x"}],
                           "tools": tools, "tool_choice": "auto"})
    assert out["tools"] == tools and "tool_choice" not in out


def test_client_connection_is_per_thread():
    import threading
    import client
    seen = []

    def grab():
        try:
            seen.append(id(client._conn()))
        finally:
            client._drop()

    t = threading.Thread(target=grab)
    t.start()
    t.join()
    c = client._conn()
    assert seen and seen[0] != id(c), "each thread must get its own upstream connection"
    client._drop()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("all checks passed")
