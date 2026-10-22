"""Upstream client for adcode.bluethenics.com.

Cloudflare in front of the API rejects the stock Python user-agent (error 1010),
so every request carries the desktop app's UA. Non-stream calls reuse one
keep-alive connection per thread; streams get their own connection.
"""
import http.client
import json
import threading
import time
import urllib.parse

UA = "ADCode/2.4.0"
HOST = "adcode.bluethenics.com"
TIMEOUT = 300
_local = threading.local()


def _headers(token, extra=None):
    h = {"user-agent": UA, "accept": "application/json", "accept-encoding": "identity"}
    if token:
        h["authorization"] = "Bearer " + token
    if extra:
        h.update(extra)
    return h


def _conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = http.client.HTTPSConnection(HOST, timeout=TIMEOUT)
        _local.conn = c
    return c


def _drop():
    c = getattr(_local, "conn", None)
    _local.conn = None
    if c is not None:
        try:
            c.close()
        except Exception:
            pass


def request(method, path, body=None, token=None, extra_headers=None, retries=2):
    """Buffered request -> (status, headers dict, bytes). Retries 5xx/429."""
    hdrs = _headers(token, extra_headers)
    if body is not None:
        hdrs["content-type"] = "application/json"
    last = None
    for attempt in range(retries + 1):
        try:
            c = _conn()
            c.request(method, path, body=body, headers=hdrs)
            r = c.getresponse()
            data = r.read()
            if r.status >= 500 or r.status == 429:
                last = (r.status, dict(r.getheaders()), data)
                if attempt < retries:
                    _drop()
                    time.sleep(0.8 * (attempt + 1))
                    continue
                return last
            return r.status, dict(r.getheaders()), data
        except (http.client.HTTPException, OSError) as e:
            _drop()
            last = (502, {}, json.dumps({"error": {"message": str(e)}}).encode())
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    return last


def stream(path, body, token, extra_headers=None):
    """Open a streaming POST. Returns (connection, response); close both when done."""
    hdrs = _headers(token, extra_headers)
    hdrs["content-type"] = "application/json"
    hdrs["accept"] = "text/event-stream"
    c = http.client.HTTPSConnection(HOST, timeout=TIMEOUT)
    c.request("POST", path, body=body, headers=hdrs)
    return c, c.getresponse()


def json_get(path, token=None):
    status, _, data = request("GET", path, token=token)
    try:
        return status, json.loads(data)
    except ValueError:
        return status, None
