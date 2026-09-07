"""Tests for token handling, refresh safety and host selection.

These cover the defects found in review of the phase 0-2 branch. Each test
names the failure it prevents, because several of them are silent: a lost
refresh token or a world-readable credential does not announce itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pinterest_mcp import server
from pinterest_mcp.client import PinterestAPIError, PinterestClient


def _mock_resp(data, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.content = b"{}"
    m.text = json.dumps(data)
    m.json = MagicMock(return_value=data)
    return m


# ---------------------------------------------------------------------------
# A token supplied directly must actually be usable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_constructor_access_token_is_usable():
    """Regression: the token was accepted and then reported as missing.

    _token_expiry stayed at 0, so _ensure_token treated a freshly supplied
    token as already expired and raised "No Pinterest access token".
    """
    client = PinterestClient(access_token="supplied-token")
    assert await client._ensure_token() == "supplied-token"


@pytest.mark.asyncio
async def test_env_access_token_is_usable(monkeypatch):
    """.env.template documents PINTEREST_ACCESS_TOKEN, so it has to work."""
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN", "env-token")
    client = PinterestClient()
    assert await client._ensure_token() == "env-token"


@pytest.mark.asyncio
async def test_no_token_anywhere_still_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("PINTEREST_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("PINTEREST_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("pinterest_mcp.client.TOKEN_FILE", tmp_path / "absent.json")
    client = PinterestClient()
    with pytest.raises(RuntimeError, match="No Pinterest access token"):
        await client._ensure_token()


# ---------------------------------------------------------------------------
# Refresh must not race, and must not swallow the error body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_calls_refresh_only_once(tmp_path, monkeypatch):
    """Regression: a rotating refresh token could be spent twice.

    Pinterest invalidates the old refresh token on every use. Two concurrent
    tool calls on an expired access token both refreshed, and the loser wrote
    back a token Pinterest had already killed, costing a full re-auth.
    """
    monkeypatch.setattr("pinterest_mcp.client.TOKEN_FILE", tmp_path / "token.json")
    monkeypatch.setattr("pinterest_mcp.client.ensure_token_dir", lambda: tmp_path / "token.json")

    client = PinterestClient(access_token=None, refresh_token="rt-original")
    client._access_token = "expired"
    client._token_expiry = 0

    calls = []

    async def fake_post(*args, **kwargs):
        calls.append(kwargs["data"]["refresh_token"])
        await asyncio.sleep(0)  # force a scheduling point mid-refresh
        return _mock_resp(
            {
                "access_token": "at-new",
                "refresh_token": "rt-new",
                "expires_in": 2592000,
                "refresh_token_expires_in": 5184000,
            }
        )

    with patch.object(client._http, "post", new=AsyncMock(side_effect=fake_post)):
        results = await asyncio.gather(*(client._ensure_token() for _ in range(5)))

    assert len(calls) == 1, f"refreshed {len(calls)} times, should be exactly 1"
    assert calls == ["rt-original"]
    assert set(results) == {"at-new"}
    assert client._refresh_token == "rt-new"


@pytest.mark.asyncio
async def test_refresh_failure_preserves_the_response_body(tmp_path, monkeypatch):
    """A closed refresh window and a bad client secret must be tellable apart."""
    monkeypatch.setattr("pinterest_mcp.client.TOKEN_FILE", tmp_path / "token.json")
    client = PinterestClient(refresh_token="rt-dead")
    client._access_token = None
    client._token_expiry = 0

    body = '{"code":2,"message":"Authentication failed: refresh token expired"}'
    resp = MagicMock(status_code=401, text=body)

    with (
        patch.object(client._http, "post", new=AsyncMock(return_value=resp)),
        pytest.raises(PinterestAPIError) as excinfo,
    ):
        await client._ensure_token()

    assert "refresh token expired" in excinfo.value.body
    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# A token file written by a refresh must be as private as one written by auth
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_refresh_created_token_file_is_owner_only(tmp_path, monkeypatch):
    target = tmp_path / "token.json"
    monkeypatch.setattr("pinterest_mcp.client.TOKEN_FILE", target)
    monkeypatch.setattr("pinterest_mcp.client.ensure_token_dir", lambda: target)

    client = PinterestClient(access_token="at")
    client._refresh_token = "rt"
    client._save_token_file()

    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600, f"token file is {oct(mode)}, expected 0o600"


# ---------------------------------------------------------------------------
# Host selection has to be reachable from the server, not just the library
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_sandbox_env_switch_enabled(monkeypatch, value):
    monkeypatch.setenv("PINTEREST_SANDBOX", value)
    assert server._sandbox_requested() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_sandbox_env_switch_disabled(monkeypatch, value):
    monkeypatch.setenv("PINTEREST_SANDBOX", value)
    assert server._sandbox_requested() is False


def test_sandbox_absent_defaults_to_production(monkeypatch):
    monkeypatch.delenv("PINTEREST_SANDBOX", raising=False)
    assert server._sandbox_requested() is False


def test_sandbox_client_targets_the_sandbox_host(monkeypatch):
    monkeypatch.setenv("PINTEREST_SANDBOX_TOKEN", "sbx-token")
    client = PinterestClient(sandbox=True)
    assert "api-sandbox.pinterest.com" in str(client._http.base_url)
    assert client.sandbox is True


def test_production_client_targets_production(monkeypatch):
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN", "at")
    client = PinterestClient()
    assert "api-sandbox" not in str(client._http.base_url)
    assert "api.pinterest.com" in str(client._http.base_url)


@pytest.mark.asyncio
async def test_sandbox_without_a_token_says_what_to_do(monkeypatch):
    monkeypatch.delenv("PINTEREST_SANDBOX_TOKEN", raising=False)
    monkeypatch.setattr("pinterest_mcp.client.SANDBOX_TOKEN", None)
    client = PinterestClient(sandbox=True)
    with pytest.raises(RuntimeError, match="PINTEREST_SANDBOX_TOKEN"):
        await client._ensure_token()


def test_sandbox_never_reads_the_production_token_file(tmp_path, monkeypatch):
    """The sandbox rejects production tokens, so it must not pick one up."""
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"access_token": "production-token"}))
    monkeypatch.setattr("pinterest_mcp.client.TOKEN_FILE", token_file)
    monkeypatch.setenv("PINTEREST_SANDBOX_TOKEN", "sbx-token")
    monkeypatch.delenv("PINTEREST_ACCESS_TOKEN", raising=False)

    client = PinterestClient(sandbox=True)
    assert client._access_token == "sbx-token"


# ---------------------------------------------------------------------------
# Caller mistakes must not be reported as Trial limitations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_pin_with_no_fields_is_a_caller_error():
    client = PinterestClient(access_token="at")
    with pytest.raises(ValueError, match="at least one"):
        await client.update_pin(pin_id="p1")
