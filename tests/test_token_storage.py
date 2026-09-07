"""Tests for token storage, the shared path, and the refresh-token window.

Covers the Phase 0 defect fixes. No real Pinterest traffic; the real token
file at ~/.config/pinterest/token.json is never touched (every test redirects
the path to tmp_path).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pinterest_mcp import auth, config
from pinterest_mcp.client import PinterestClient

DAY = 86400.0


def test_token_path_is_absolute_and_shared() -> None:
    """auth and client must resolve the same absolute token path."""
    assert config.TOKEN_FILE.is_absolute()
    assert config.TOKEN_FILE.name == "token.json"
    from pinterest_mcp import client as client_mod

    assert client_mod.TOKEN_FILE == config.TOKEN_FILE


def test_ensure_token_dir_creates_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "token.json"
    with patch.object(config, "TOKEN_FILE", target):
        assert not target.parent.exists()
        returned = config.ensure_token_dir()
        assert target.parent.is_dir()
        assert returned == target


def test_save_token_records_refresh_window(tmp_path: Path) -> None:
    """The 60-day refresh window must be persisted, not just access expiry."""
    target = tmp_path / "token.json"
    with patch.object(auth, "ensure_token_dir", return_value=target):
        auth.save_token(
            {
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_in": 30 * 24 * 3600,
                "refresh_token_expires_in": 60 * 24 * 3600,
                "scope": "boards:read,boards:write",
            }
        )
    saved = json.loads(target.read_text())
    assert saved["access_token"] == "at-1"
    assert saved["refresh_token"] == "rt-1"
    assert saved["refresh_token_expiry"] is not None
    days = (saved["refresh_token_expiry"] - time.time()) / DAY
    assert 59 < days < 61


def test_save_token_handles_missing_refresh_window(tmp_path: Path) -> None:
    target = tmp_path / "token.json"
    with patch.object(auth, "ensure_token_dir", return_value=target):
        auth.save_token({"access_token": "at", "refresh_token": "rt", "expires_in": 3600})
    assert json.loads(target.read_text())["refresh_token_expiry"] is None


def _write_token(target: Path, refresh_days: float | None) -> None:
    now = time.time()
    target.write_text(
        json.dumps(
            {
                "access_token": "at",
                "refresh_token": "rt",
                "expiry": now + 3600,
                "refresh_token_expiry": None if refresh_days is None else now + refresh_days * DAY,
            }
        )
    )


@pytest.mark.parametrize(
    "days_left,expected_level,needle",
    [
        (45.0, None, None),
        (10.0, logging.WARNING, "expires in"),
        (-3.0, logging.ERROR, "EXPIRED"),
        (None, logging.WARNING, "no refresh_token_expiry"),
    ],
)
def test_refresh_window_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    days_left: float | None,
    expected_level: int | None,
    needle: str | None,
) -> None:
    """Warn under 14 days, error once expired, stay quiet when healthy."""
    target = tmp_path / "token.json"
    _write_token(target, days_left)
    import pinterest_mcp.client as client_mod

    with patch.object(client_mod, "TOKEN_FILE", target), caplog.at_level(logging.INFO):
        PinterestClient()

    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    if expected_level is None:
        assert records == [], f"expected no warning, got {[r.message for r in records]}"
    else:
        assert records, "expected a warning about the refresh window"
        assert records[0].levelno == expected_level
        assert needle is not None and needle in records[0].getMessage()


def test_client_persists_refresh_window_on_refresh(tmp_path: Path) -> None:
    """A token refresh must carry refresh_token_expires_in back to disk."""
    target = tmp_path / "token.json"
    import pinterest_mcp.client as client_mod

    with (
        patch.object(client_mod, "TOKEN_FILE", target),
        patch.object(client_mod, "ensure_token_dir", return_value=target),
    ):
        c = PinterestClient(client_id="id", client_secret="sec", refresh_token="rt-old")

        from unittest.mock import AsyncMock, MagicMock

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(
            return_value={
                "access_token": "at-new",
                "refresh_token": "rt-new",
                "expires_in": 2592000,
                "refresh_token_expires_in": 5184000,
            }
        )
        with patch.object(c._http, "post", new_callable=AsyncMock, return_value=resp):
            import asyncio

            asyncio.run(c._refresh())

    saved = json.loads(target.read_text())
    assert saved["access_token"] == "at-new"
    assert saved["refresh_token"] == "rt-new"
    days = (saved["refresh_token_expiry"] - time.time()) / DAY
    assert 59 < days < 61
