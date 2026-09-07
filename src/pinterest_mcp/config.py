"""Shared configuration for pinterest-mcp.

Single source of truth for the token file location and OAuth scopes.

Both ``auth.py`` (which writes the token) and ``client.py`` (which reads and
refreshes it) import from here. Upstream declared ``TOKEN_FILE`` separately in
both modules as a RELATIVE path, which meant a live OAuth credential was
written to whatever directory the MCP client happened to launch the server
from. This module makes it one absolute path.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------
# python-dotenv was a declared dependency upstream but was never called, so
# .env was silently ignored. That mattered beyond first-run convenience:
# PinterestClient reads PINTEREST_CLIENT_ID and PINTEREST_CLIENT_SECRET from
# the environment to refresh the access token, so without this the refresh
# would fail with an opaque 401 once the 30-day access token expired.
#
# Loaded before any os.environ read below. Real environment variables win
# over .env values (override=False).
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _candidate in (Path.cwd() / ".env", _REPO_ROOT / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)
        break

# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------

#: Absolute path to the stored OAuth token.
#: Defaults to ``~/.config/pinterest/token.json`` which resolves to
#: ``C:\Users\<you>\.config\pinterest\token.json`` on Windows.
#: Override with the ``PINTEREST_TOKEN_FILE`` environment variable.
TOKEN_FILE: Path = Path(
    os.environ.get("PINTEREST_TOKEN_FILE") or Path.home() / ".config" / "pinterest" / "token.json"
).expanduser()


def ensure_token_dir() -> Path:
    """Create the token directory if it does not exist. Returns TOKEN_FILE."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    return TOKEN_FILE


# ---------------------------------------------------------------------------
# OAuth endpoints
# ---------------------------------------------------------------------------

PINTEREST_BASE = "https://api.pinterest.com/v5"
PINTEREST_AUTH_URL = "https://www.pinterest.com/oauth/"
PINTEREST_TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"

#: Must match the redirect URI registered in the Pinterest app settings
#: character for character, including the port and the trailing path.
REDIRECT_URI = os.environ.get("PINTEREST_REDIRECT_URI", "http://localhost:8089/callback")

# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

#: Scopes requested during the OAuth flow. Verified against Pinterest's own
#: OpenAPI spec (github.com/pinterest/api-description, v5.28.0) by reading the
#: ``security`` block of each endpoint this server calls.
#:
#: boards:read         - GET /boards, GET /boards/{id}; also required by every
#:                       pin endpoint and by board writes
#: boards:write        - POST /boards, PATCH /boards/{id}, DELETE /boards/{id}
#: pins:read           - GET /pins, GET /pins/{id}, pin + account top-pin analytics
#: pins:write          - POST /pins, PATCH /pins/{id}, DELETE /pins/{id}
#: user_accounts:read  - GET /user_account, GET /user_account/analytics,
#:                       GET /trends/keywords/{region}/top/{trend_type}
#: ads:read            - GET /terms/suggested, GET /terms/related (keyword research)
#: boards:read_secret  - required by GET /search/pins alongside pins:read_secret
#: pins:read_secret    - required by GET /search/pins
#:
#: Override with PINTEREST_SCOPES if the Pinterest app is not approved for one
#: of these (ads:read is the likely candidate) and the authorize page errors.
SCOPES: str = os.environ.get(
    "PINTEREST_SCOPES",
    "boards:read,boards:write,pins:read,pins:write,user_accounts:read,"
    "ads:read,boards:read_secret,pins:read_secret",
)

# ---------------------------------------------------------------------------
# Refresh-token window
# ---------------------------------------------------------------------------

#: Pinterest continuous refresh tokens are valid for 60 days and rotate on each
#: use. Warn once the remaining window drops below this many days.
REFRESH_WARN_DAYS = 14
