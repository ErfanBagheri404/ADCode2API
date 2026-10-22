"""Firebase auth for ADCode's backend, owned by the proxy.

Identity = {uid, refreshToken}. A fresh idToken is minted from the refresh token
via securetoken.googleapis.com with the app's public Firebase API key.
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FIREBASE_KEY = os.environ.get("ADCODE_FIREBASE_API_KEY",
                              "AIzaSyATZzX5Fw2HjR34VxB3UQWTmljprA5uXqE")
GOOGLE_CLIENT_ID = os.environ.get(
    "ADCODE_GOOGLE_CLIENT_ID",
    "345488063416-60i3dqfj1j8itg9bio2itshmcbq7e3mh.apps.googleusercontent.com")
GITHUB_CLIENT_ID = os.environ.get("ADCODE_GITHUB_CLIENT_ID", "Ov23lienZUTAiNKAM0UV")
TOKEN_URL = "https://securetoken.googleapis.com/v1/token"
IDP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp"

APP_IDENTITY = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Packages", "bluethenics.ADCode_v2n27feg9hw00",
    "LocalCache", "Roaming", "@adcode", "desktop", "ads", "ads", "identity.json")

STORE = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ADCode2API", "auth.json")


def _post(url, data, ctype="application/json"):
    req = urllib.request.Request(url, data=data, headers={"content-type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # transport
        return 0, str(e)


def _q(url):
    return f"{url}?key={urllib.parse.quote(FIREBASE_KEY)}"


def load():
    try:
        with open(STORE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("refreshToken"):
            return d
    except (OSError, ValueError):
        pass
    return None


def save(identity, provider=None):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    identity = dict(identity)
    if provider:
        identity["provider"] = provider
    identity["savedAt"] = int(time.time())
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(identity, f)
    return identity


def import_from_app():
    """Adopt the desktop app's Google-linked identity (no browser needed)."""
    try:
        with open(APP_IDENTITY, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not d.get("refreshToken"):
        return None
    return save({"uid": d.get("uid"), "refreshToken": d["refreshToken"]}, "app-import")


def refresh(identity):
    """Exchange a refresh token for a fresh id token. Returns (idToken, identity)."""
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": identity["refreshToken"],
    }).encode()
    status, text = _post(_q(TOKEN_URL), body, "application/x-www-form-urlencoded")
    if status != 200:
        raise RuntimeError(f"token refresh failed: HTTP {status} {text[:200]}")
    d = json.loads(text)
    ident = {"uid": d.get("user_id") or identity.get("uid"),
             "refreshToken": d.get("refresh_token") or identity["refreshToken"],
             "idToken": d["id_token"],
             "expiresAt": time.time() + float(d.get("expires_in") or 3600)}
    return ident


_cache = {"idToken": None, "expiresAt": 0.0, "identity": None}


def get_token(force=False):
    """Valid id token for the stored identity (refreshed 60s before expiry)."""
    if not force and _cache["idToken"] and time.time() < _cache["expiresAt"] - 60:
        return _cache["idToken"]
    ident = _cache["identity"] or load()
    if ident is None:
        ident = import_from_app()
    if ident is None:
        raise RuntimeError("not logged in - run: python main.py --login")
    fresh = refresh(ident)
    ident.pop("idToken", None)
    ident.update({"idToken": fresh["idToken"], "expiresAt": fresh["expiresAt"]})
    _cache.update(ident, identity=ident)
    save(ident, ident.get("provider"))
    return fresh["idToken"]


def _sign_in_with_idp(field, credential, provider):
    post_body = f"{field}={urllib.parse.quote(credential, safe='')}&providerId={provider}"
    payload = {"postBody": post_body, "requestUri": "http://localhost",
               "returnIdpCredential": True, "returnSecureToken": True}
    status, text = _post(_q(IDP_URL), json.dumps(payload).encode())
    if status != 200:
        raise RuntimeError(f"firebase sign-in failed: HTTP {status} {text[:300]}")
    d = json.loads(text)
    if not d.get("refreshToken"):
        raise RuntimeError(f"firebase sign-in returned no refresh token: {text[:300]}")
    return save({"uid": d.get("localId"), "refreshToken": d["refreshToken"]}, provider)


def login_google():
    """PKCE loopback flow with the app's public desktop client id."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    import webbrowser

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    box = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] != state:
                self.send_response(400); self.end_headers(); return
            box.update(q)
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>ADCode2API: sign-in received. Close this tab.</h2>")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    url = ("https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"http://127.0.0.1:{port}",
        "response_type": "code",
        "scope": "openid email profile",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "prompt": "select_account",
    }))
    print("\n  Open this URL and approve sign-in:\n\n  " + url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    t.join(timeout=300)
    srv.server_close()
    if "code" not in box:
        raise RuntimeError("sign-in was not completed")
    status, text = _post("https://oauth2.googleapis.com/token", urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "code": box["code"][0],
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": f"http://127.0.0.1:{port}",
    }).encode(), "application/x-www-form-urlencoded")
    if status != 200:
        raise RuntimeError(f"google token exchange failed: HTTP {status} {text[:300]}")
    id_token = json.loads(text)["id_token"]
    return _sign_in_with_idp("id_token", id_token, "google.com")


def login_github():
    """GitHub device flow, then Firebase sign-in with the GitHub access token."""
    status, text = _post("https://github.com/login/device/code", urllib.parse.urlencode({
        "client_id": GITHUB_CLIENT_ID, "scope": "read:user user:email",
    }).encode(), "application/x-www-form-urlencoded")
    if status != 200:
        raise RuntimeError(f"github device flow failed: HTTP {status} {text[:200]}")
    d = json.loads(text)
    print(f"\n  Open {d['verification_uri']} and enter code: {d['user_code']}\n")
    interval = int(d.get("interval") or 5)
    deadline = time.time() + int(d.get("expires_in") or 300)
    access = None
    while time.time() < deadline:
        time.sleep(interval)
        status, text = _post("https://github.com/login/oauth/access_token", urllib.parse.urlencode({
            "client_id": GITHUB_CLIENT_ID, "device_code": d["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }).encode(), "application/x-www-form-urlencoded")
        try:
            body = json.loads(text)
        except ValueError:
            continue
        if body.get("access_token"):
            access = body["access_token"]
            break
        if body.get("error") not in ("authorization_pending", "slow_down"):
            raise RuntimeError(f"github sign-in failed: {body.get('error')}")
    if not access:
        raise RuntimeError("github sign-in timed out")
    return _sign_in_with_idp("access_token", access, "github.com")


def login(provider="google"):
    identity = login_google() if provider == "google" else login_github()
    _cache.update({"idToken": None, "expiresAt": 0.0, "identity": identity})
    print(f"  signed in: uid={identity.get('uid')} ({provider})")
    print(f"  token store: {STORE}")
    return identity


def status():
    ident = load()
    if ident is None:
        return {"loggedIn": False}
    out = {"loggedIn": True, "uid": ident.get("uid"),
           "provider": ident.get("provider"), "store": STORE}
    try:
        out["idTokenOk"] = bool(get_token())
    except Exception as e:
        out["idTokenOk"] = False
        out["error"] = str(e)
    return out


if __name__ == "__main__":
    print(json.dumps(status(), indent=2))
    sys.exit(0)
