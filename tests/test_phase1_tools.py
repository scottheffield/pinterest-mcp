"""Tests for the tools added in phase 1 and for the Trial-access gates.

All HTTP calls are mocked, no real Pinterest traffic. Error bodies are the
verbatim strings the live API returned during the probes recorded in
docs/trial-access-findings.md, so these tests break if the translation of a
real gate ever stops matching.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pinterest_mcp.client import PinterestAPIError, PinterestClient, TrialAccessError


@pytest.fixture
def client() -> PinterestClient:
    c = PinterestClient(access_token="fake-token")
    c._token_expiry = float("inf")  # never expires in tests
    return c


def _mock_resp(data) -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.content = b"{}"
    m.json = MagicMock(return_value=data)
    return m


def _error_resp(status: int, body: str) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.text = body
    m.content = body.encode()
    return m


# ---------------------------------------------------------------------------
# Trial-access gates: pin writes must explain themselves, not leak raw HTTP
# ---------------------------------------------------------------------------

PIN_EDIT_BODY = (
    '{"code":3,"message":"Your application does not have access to this '
    'restricted feature: pin_edit"}'
)

PIN_CREATE_BODY = (
    '{"code":29,"message":"Apps with Trial access may not create Pins in '
    "production https://api.pinterest.com - use API Sandbox "
    'https://api-sandbox.pinterest.com instead."}'
)


@pytest.mark.asyncio
async def test_update_pin_translates_the_pin_edit_gate(client: PinterestClient):
    with (
        patch.object(
            client._http,
            "request",
            new_callable=AsyncMock,
            return_value=_error_resp(401, PIN_EDIT_BODY),
        ),
        pytest.raises(TrialAccessError) as excinfo,
    ):
        await client.update_pin(pin_id="pin_001", description="new copy")

    message = str(excinfo.value)
    assert "Trial access" in message
    assert "Standard access is required" in message
    assert "Nothing was modified" in message
    assert "pin_edit" in message  # the raw body is preserved, not swallowed
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_update_pin_passes_through_unrelated_errors(client: PinterestClient):
    """A 404 is not a Trial gate and must not be relabelled as one."""
    body = '{"code":40,"message":"Pin not found."}'
    with (
        patch.object(
            client._http, "request", new_callable=AsyncMock, return_value=_error_resp(404, body)
        ),
        pytest.raises(PinterestAPIError) as excinfo,
    ):
        await client.update_pin(pin_id="nope", title="x")

    assert not isinstance(excinfo.value, TrialAccessError)
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_create_pin_translates_the_production_create_gate(client: PinterestClient):
    with (
        patch.object(
            client._http,
            "request",
            new_callable=AsyncMock,
            return_value=_error_resp(403, PIN_CREATE_BODY),
        ),
        pytest.raises(TrialAccessError) as excinfo,
    ):
        await client.create_pin(
            board_id="b1",
            title="t",
            description="d",
            image_url="https://example.com/x.jpg",
        )

    message = str(excinfo.value)
    assert "Standard access is required" in message
    assert "No pin was created" in message
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_create_pin_dry_run_makes_no_request(client: PinterestClient):
    with patch.object(client._http, "request", new_callable=AsyncMock) as req:
        result = await client.create_pin(
            board_id="b1",
            title="t",
            description="d",
            image_url="https://example.com/x.jpg",
            dry_run=True,
        )
    req.assert_not_awaited()
    assert result["dry_run"] is True


# ---------------------------------------------------------------------------
# Board writes: the operations the probes proved work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_board_sends_only_the_supplied_fields(client: PinterestClient):
    with patch.object(
        client._http,
        "request",
        new_callable=AsyncMock,
        return_value=_mock_resp({"id": "b1", "name": "Printable Calendars"}),
    ) as req:
        result = await client.update_board(board_id="b1", name="Printable Calendars")

    assert req.await_args.args == ("PATCH", "/boards/b1")
    assert req.await_args.kwargs["json"] == {"name": "Printable Calendars"}
    assert result["name"] == "Printable Calendars"


@pytest.mark.asyncio
async def test_update_board_rejects_an_empty_update(client: PinterestClient):
    with pytest.raises(ValueError, match="at least one"):
        await client.update_board(board_id="b1")


@pytest.mark.asyncio
async def test_delete_board_handles_204_with_no_body(client: PinterestClient):
    resp = MagicMock()
    resp.status_code = 204
    resp.content = b""
    with patch.object(client._http, "request", new_callable=AsyncMock, return_value=resp) as req:
        result = await client.delete_board("b1")

    assert req.await_args.args == ("DELETE", "/boards/b1")
    assert result == {}


# ---------------------------------------------------------------------------
# Reads added in phase 1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pin(client: PinterestClient):
    with patch.object(
        client._http,
        "request",
        new_callable=AsyncMock,
        return_value=_mock_resp({"id": "p1", "alt_text": None}),
    ) as req:
        pin = await client.get_pin("p1")

    assert req.await_args.args == ("GET", "/pins/p1")
    assert pin["id"] == "p1"


@pytest.mark.asyncio
async def test_get_account_info(client: PinterestClient):
    with patch.object(
        client._http,
        "request",
        new_callable=AsyncMock,
        return_value=_mock_resp({"username": "TheHandyPages", "pin_count": 27}),
    ) as req:
        account = await client.get_account_info()

    assert req.await_args.args == ("GET", "/user_account")
    assert account["username"] == "TheHandyPages"


@pytest.mark.asyncio
async def test_list_pins_single_page_returns_the_bookmark(client: PinterestClient):
    page = {"items": [{"id": "p1"}], "bookmark": "cursor-2"}
    with patch.object(
        client._http, "request", new_callable=AsyncMock, return_value=_mock_resp(page)
    ):
        result = await client.list_pins()

    assert result["bookmark"] == "cursor-2"
    assert len(result["items"]) == 1


@pytest.mark.asyncio
async def test_list_pins_fetch_all_walks_every_page(client: PinterestClient):
    pages = [
        _mock_resp({"items": [{"id": "p1"}], "bookmark": "c2"}),
        _mock_resp({"items": [{"id": "p2"}], "bookmark": "c3"}),
        _mock_resp({"items": [{"id": "p3"}], "bookmark": None}),
    ]
    with patch.object(client._http, "request", new_callable=AsyncMock, side_effect=pages) as req:
        result = await client.list_pins(fetch_all=True)

    assert req.await_count == 3
    assert [p["id"] for p in result["items"]] == ["p1", "p2", "p3"]
    assert result["bookmark"] is None


# ---------------------------------------------------------------------------
# Keyword research and top-pin analytics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_suggested_keywords_accepts_a_bare_list(client: PinterestClient):
    """The live endpoint returns a bare JSON array, not an items envelope."""
    with patch.object(
        client._http,
        "request",
        new_callable=AsyncMock,
        return_value=_mock_resp(["printable calendar"]),
    ):
        terms = await client.get_suggested_keywords("printable calendar")

    assert terms == ["printable calendar"]


@pytest.mark.asyncio
async def test_get_related_keywords(client: PinterestClient):
    with patch.object(
        client._http,
        "request",
        new_callable=AsyncMock,
        return_value=_mock_resp({"related_terms": [], "related_term_count": 0}),
    ) as req:
        result = await client.get_related_keywords(["printable calendar"])

    assert req.await_args.args == ("GET", "/terms/related")
    assert result["related_term_count"] == 0


@pytest.mark.asyncio
async def test_get_top_pins_analytics_defaults_metrics_to_sort_by(client: PinterestClient):
    with patch.object(
        client._http,
        "request",
        new_callable=AsyncMock,
        return_value=_mock_resp({"pins": [{"pin_id": "p1", "IMPRESSION": 129}]}),
    ) as req:
        result = await client.get_top_pins_analytics(start_date="2026-08-08", end_date="2026-09-07")

    params = req.await_args.kwargs["params"]
    assert params["sort_by"] == "IMPRESSION"
    assert result["pins"][0]["IMPRESSION"] == 129
