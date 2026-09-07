"""OAuth 2.0 authorization flow for Pinterest.

Run once to get your access + refresh tokens:
    pinterest-mcp-auth

The callback listener uses the standard library's ``http.server``. Upstream
used aiohttp, which was never declared in pyproject.toml, so a clean install
died on ImportError the first time anyone ran this command.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .config import (
    PINTEREST_AUTH_URL,
    PINTEREST_TOKEN_URL,
    REDIRECT_URI,
    SCOPES,
    ensure_token_dir,
)

_CALLBACK_PATH = urlparse(REDIRECT_URI).path or "/callback"
_CALLBACK_HOST = urlparse(REDIRECT_URI).hostname or "localhost"
_CALLBACK_PORT = urlparse(REDIRECT_URI).port or 8089

_PAGE_OK = b"""<!doctype html><meta charset="utf-8">
<title>Pinterest authorized</title>
<body style="font-family:system-ui;padding:3rem;max-width:32rem">
<h2>Authorized</h2>
<p>Token exchange is running in your terminal. You can close this tab.</p>
</body>"""

_PAGE_ERR = b"""<!doctype html><meta charset="utf-8">
<title>Pinterest authorization failed</title>
<body style="font-family:system-ui;padding:3rem;max-width:32rem">
<h2>Authorization failed</h2>
<p>No authorization code was returned. Check the terminal for details.</p>
</body>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Single-shot handler that captures ?code= and ?state= from the redirect."""

    code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # stdlib handler naming
        parsed = urlparse(self.path)
        if parsed.path != _CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        _CallbackHandler.code = (params.get("code") or [None])[0]
        _CallbackHandler.state = (params.get("state") or [None])[0]
        _CallbackHandler.error = (params.get("error") or [None])[0]

        body = _PAGE_OK if _CallbackHandler.code else _PAGE_ERR
        self.send_response(200 if _CallbackHandler.code else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Silence the default stderr access log."""


def _wait_for_callback(expected_state: str) -> str:
    """Serve exactly one callback request and return the authorization code."""
    _CallbackHandler.code = None
    _CallbackHandler.state = None
    _CallbackHandler.error = None

    server = HTTPServer((_CALLBACK_HOST, _CALLBACK_PORT), _CallbackHandler)
    try:
        # handle_request() blocks until one request is served. A stray favicon
        # or a 404 probe would consume it, so loop until we see the callback.
        while _CallbackHandler.code is None and _CallbackHandler.error is None:
            server.handle_request()
    finally:
        server.server_close()

    if _CallbackHandler.error:
        raise RuntimeError(f"Pinterest returned an error: {_CallbackHandler.error}")
    if _CallbackHandler.state != expected_state:
        raise RuntimeError(
            "OAuth state mismatch. Expected "
            f"{expected_state!r}, got {_CallbackHandler.state!r}. Aborting."
        )
    assert _CallbackHandler.code is not None
    return _CallbackHandler.code


def save_token(token_data: dict) -> None:
    """Persist a token response, including the refresh-token expiry window.

    Upstream stored only the access-token expiry, so the 60-day continuous
    refresh window was invisible until after it had already closed.
    """
    now = time.time()
    path = ensure_token_dir()
    payload = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "expiry": now + token_data.get("expires_in", 3600),
        "refresh_token_expiry": (
            now + token_data["refresh_token_expires_in"]
            if token_data.get("refresh_token_expires_in") is not None
            else None
        ),
        "scope": token_data.get("scope"),
        "updated_at": now,
    }
    path.write_text(json.dumps(payload, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def run_auth_flow() -> None:
    """Entrypoint for the `pinterest-mcp-auth` CLI command."""
    client_id = os.environ.get("PINTEREST_CLIENT_ID") or input("Pinterest Client ID: ").strip()
    client_secret = (
        os.environ.get("PINTEREST_CLIENT_SECRET") or input("Pinterest Client Secret: ").strip()
    )

    state = secrets.token_urlsafe(16)
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    auth_url = f"{PINTEREST_AUTH_URL}?{urlencode(params)}"

    print(f"Redirect URI (must match your app settings exactly): {REDIRECT_URI}")
    print(f"Scopes requested: {SCOPES}")
    print(f"Listening on http://{_CALLBACK_HOST}:{_CALLBACK_PORT}{_CALLBACK_PATH}")
    print(f"\nOpening browser:\n{auth_url}\n")
    webbrowser.open(auth_url)

    code = _wait_for_callback(state)

    resp = httpx.post(
        PINTEREST_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        auth=(client_id, client_secret),
        timeout=30.0,
    )
    if resp.status_code >= 400:
        print(f"Token exchange failed: HTTP {resp.status_code}")
        print(resp.text)
        resp.raise_for_status()

    token_data = resp.json()
    save_token(token_data)

    from .config import TOKEN_FILE

    print(f"Token saved to {TOKEN_FILE}")
    print(f"Granted scopes: {token_data.get('scope')}")
    rt_exp = token_data.get("refresh_token_expires_in")
    if rt_exp:
        print(f"Refresh token window: {rt_exp / 86400:.0f} days")
    else:
        print("Refresh token window: not reported by Pinterest in this response")


if __name__ == "__main__":
    run_auth_flow()
