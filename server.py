"""HTTP server: OpenAI-compatible surface over ADCode's included-AI backend."""
import http.server
import json
import time

import auth
import client
from anthropic import anthropic_response, anthropic_stream_response, anthropic_to_openai

CHAT_PATH = "/v1/ai/tagflow/chat/completions"
CONFIG_PATH = "/v1/ai/tagflow/config"
_models_cache = {"at": 0.0, "data": None}

# The included-AI gateway validates an allowlist and 400s the whole request
# ("Send a supported chat request.") when it sees anything else, so drop the
# fields it doesn't know instead of forwarding them.
ALLOWED_FIELDS = ("model", "messages", "max_tokens", "max_completion_tokens",
                  "stream", "stream_options", "tools", "reasoning_effort")


def _flatten(content):
    """Gateway only reads plain-string content; join text parts, drop the rest."""
    if isinstance(content, str) or content is None:
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, str):
                parts.append(blk)
            elif isinstance(blk, dict) and blk.get("type") in (None, "text", "input_text"):
                parts.append(blk.get("text") or "")
        return "".join(parts)
    return str(content)


def sanitize(req):
    """Shape an OpenAI chat body into what the gateway accepts."""
    out = {k: req[k] for k in ALLOWED_FIELDS if k in req}
    model = out.get("model") or ""
    if "/" in model:  # a router may prefix the id ("adcode/heabsy-qwen38")
        model = model.rsplit("/", 1)[-1]
    out["model"] = model
    out["messages"] = [{**{k: v for k, v in m.items() if k in ("role", "content", "tool_calls",
                                                               "tool_call_id", "name")},
                        "content": _flatten(m.get("content"))}
                       for m in req.get("messages", []) if isinstance(m, dict)]
    return out


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ADCode2API"
    cfg = None

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def _dispatch(self, method):
        if self.path.startswith("/healthz"):
            self._json(200, {"status": "ok"})
            return
        if self.cfg.api_key and self.path.startswith("/v1/") and not self._authorized():
            self._json(401, {"error": {"message": "invalid API key", "type": "authentication_error"}})
            return
        body = self._read_body()
        try:
            if self.path.startswith("/v1/models"):
                self._models()
            elif self.path.startswith("/v1/chat/completions"):
                self._chat(body)
            elif self.path.startswith("/v1/messages"):
                self._anthropic(body)
            else:
                self._passthrough(method, body)
        except RuntimeError as e:  # auth problems
            self._json(401, {"error": {"message": str(e), "type": "authentication_error"}})

    # ---- handlers ------------------------------------------------------

    def _models(self):
        now = time.time()
        if _models_cache["data"] and now - _models_cache["at"] < 300:
            self._json(200, _models_cache["data"])
            return
        status, cfg = client.json_get(CONFIG_PATH)
        if status != 200 or not isinstance(cfg, dict):
            self._json(502, {"error": {"message": f"model list unavailable (HTTP {status})",
                                       "type": "proxy_error"}})
            return
        data = [{"id": m["id"], "object": "model", "owned_by": "adcode",
                 "context_window": m.get("contextWindow"), "max_output": m.get("maxOutput")}
                for m in cfg.get("models", []) if m.get("id")]
        out = {"object": "list", "data": data}
        _models_cache.update({"at": now, "data": out})
        self._json(200, out)

    def _chat(self, body):
        if body is None:
            self._json(400, {"error": {"message": "empty body", "type": "invalid_request_error"}})
            return
        try:
            req = json.loads(body)
        except ValueError as e:
            self._json(400, {"error": {"message": f"bad json: {e}", "type": "invalid_request_error"}})
            return
        token = auth.get_token()
        up = sanitize(req)
        if req.get("stream"):
            conn, resp = client.stream(CHAT_PATH, json.dumps(up).encode(), token)
            try:
                if resp.status != 200:
                    self._json(resp.status, {"error": {"message": resp.read().decode("utf-8", "replace")[:2000],
                                                       "type": "upstream_error"}})
                    return
                self._relay_stream(resp)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            return
        # upstream defaults to streaming when the flag is absent
        up["stream"] = False
        status, _, data = client.request("POST", CHAT_PATH, body=json.dumps(up).encode(), token=token)
        self._raw(status, data, "application/json")

    def _anthropic(self, body):
        try:
            ar = json.loads(body or b"{}")
        except ValueError as e:
            self._json(400, {"error": {"message": f"bad json: {e}", "type": "invalid_request_error"}})
            return
        oai = anthropic_to_openai(ar)
        oai["stream"] = True
        token = auth.get_token()
        status, _, data = client.request("POST", CHAT_PATH,
                                         body=json.dumps(oai).encode(), token=token)
        if status != 200:
            self._json(status, {"error": {"message": data.decode("utf-8", "replace")[:2000],
                                          "type": "upstream_error"}})
            return
        model = ar.get("model", "")
        if ar.get("stream"):
            self._sse(anthropic_stream_response(data, model))
        else:
            self._json(200, anthropic_response(data, model))

    def _passthrough(self, method, body):
        # forward the caller's own credentials when present (e.g. the desktop app
        # pointed at this proxy); otherwise use the proxy's token
        auth_hdr = self.headers.get("Authorization")
        token = None if auth_hdr else auth.get_token()
        extra = {"authorization": auth_hdr} if auth_hdr else None
        status, headers, data = client.request(method, self.path, body=body, token=token,
                                                extra_headers=extra)
        ctype = next((v for k, v in headers.items() if k.lower() == "content-type"), "application/json")
        self._raw(status, data, ctype)

    # ---- plumbing ------------------------------------------------------

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else None

    def _authorized(self):
        h = self.headers.get("Authorization", "")
        if h.lower().startswith("bearer ") and h[7:].strip() == self.cfg.api_key:
            return True
        return self.headers.get("x-api-key", "") == self.cfg.api_key

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, status, obj):
        self._raw(status, json.dumps(obj).encode(), "application/json")

    def _raw(self, status, data, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(data)
        except (ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def _relay_stream(self, resp):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self._cors()
        self.end_headers()
        try:
            while True:
                chunk = resp.read1(4096)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def _sse(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self._cors()
        self.end_headers()
        for ev in events:
            data = ev.encode()
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def log_message(self, fmt, *args):
        pass


def serve(cfg):
    Handler.cfg = cfg
    srv = http.server.ThreadingHTTPServer((cfg.bind, cfg.port), Handler)
    print(f"  listening on http://{cfg.bind}:{cfg.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  shutting down")
        srv.server_close()
