"""Pinterest API v5 client.

OAuth 2.0 with automatic token refresh.
Docs: https://developers.pinterest.com/docs/api/v5/
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .config import (
    PINTEREST_BASE,
    PINTEREST_SANDBOX_BASE,
    REFRESH_WARN_DAYS,
    SANDBOX_TOKEN,
    TOKEN_FILE,
    ensure_token_dir,
)
from .config import (
    PINTEREST_TOKEN_URL as PINTEREST_AUTH,
)

logger = logging.getLogger(__name__)


class PinterestAPIError(RuntimeError):
    """A Pinterest API call returned an error status.

    httpx's raise_for_status() discards the response body, but Pinterest puts
    the useful part there (``code`` and ``message``). Under Trial access the
    body is often the only signal explaining why a call was rejected, so it is
    preserved here.
    """

    def __init__(self, status_code: int, method: str, path: str, body: str) -> None:
        self.status_code = status_code
        self.method = method
        self.path = path
        self.body = body
        super().__init__(f"HTTP {status_code} on {method} {path}: {body}")


class TrialAccessError(PinterestAPIError):
    """A call was refused because the app has Trial access, not Standard.

    Pinterest signals these refusals in the response body rather than by
    status code alone, and the status codes are inconsistent (401 for
    pin_edit, 403 for pin creation). Raising a distinct type keeps the
    "apply for Standard access" advice in one place instead of leaving an
    agent to interpret a raw HTTP error.
    """

    def __init__(self, err: PinterestAPIError, feature: str, remedy: str) -> None:
        self.feature = feature
        RuntimeError.__init__(
            self,
            f"{feature} is not available to this app under Pinterest Trial access. "
            f"{remedy} Pinterest returned HTTP {err.status_code} on {err.method} "
            f"{err.path}: {err.body}",
        )
        self.status_code = err.status_code
        self.method = err.method
        self.path = err.path
        self.body = err.body


# Substrings Pinterest uses to name a Trial-versus-Standard feature gate.
_PIN_EDIT_GATE = "pin_edit"
_PIN_CREATE_GATE = "may not create Pins in production"


# Rate limit: 10 pins/minute
_PIN_RATE_LIMIT = 10
_PIN_RATE_WINDOW = 60.0


class PinterestClient:
    """Pinterest API v5 client with OAuth 2.0 and token refresh."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        sandbox: bool = False,
    ) -> None:
        self.sandbox = sandbox
        self.client_id = client_id or os.environ.get("PINTEREST_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("PINTEREST_CLIENT_SECRET", "")
        self._access_token = access_token or os.environ.get("PINTEREST_ACCESS_TOKEN")
        self._refresh_token = refresh_token or os.environ.get("PINTEREST_REFRESH_TOKEN")
        self._token_expiry: float = 0
        self._refresh_token_expiry: float | None = None
        self._pin_timestamps: list[float] = []
        self._http = httpx.AsyncClient(
            timeout=30.0,
            base_url=PINTEREST_SANDBOX_BASE if sandbox else PINTEREST_BASE,
            headers={"User-Agent": "pinterest-mcp/0.1.0"},
        )
        if sandbox:
            # The sandbox rejects production OAuth tokens outright, so it uses
            # its own short-lived token and never touches the token file.
            self._access_token = access_token or SANDBOX_TOKEN
            self._token_expiry = float("inf") if self._access_token else 0
            if not self._access_token:
                logger.warning(
                    "Sandbox mode requested but PINTEREST_SANDBOX_TOKEN is not set. "
                    "Generate one in the Pinterest app Configure tab (Sandbox "
                    "environment). Sandbox tokens expire after 24 hours."
                )
        elif not self._access_token and TOKEN_FILE.exists():
            self._load_token_file()

    def _load_token_file(self) -> None:
        try:
            data = json.loads(TOKEN_FILE.read_text())
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            self._token_expiry = data.get("expiry", 0)
            self._refresh_token_expiry = data.get("refresh_token_expiry")
            logger.info("Loaded Pinterest token from %s", TOKEN_FILE)
            self._warn_if_refresh_window_closing()
        except Exception as e:  # noqa: BLE001 - a bad token file must not crash startup
            logger.warning("Could not load token file: %s", e)

    def _warn_if_refresh_window_closing(self) -> None:
        """Warn when the 60-day continuous refresh window is nearly closed.

        Pinterest refresh tokens rotate on every use and stay valid
        indefinitely only if used inside the window. Once it closes, the only
        recovery is a full browser OAuth run.
        """
        if not self._refresh_token_expiry:
            logger.warning(
                "Token file has no refresh_token_expiry recorded. The 60-day "
                "refresh window cannot be tracked. Re-run pinterest-mcp-auth "
                "to record it."
            )
            return
        days_left = (self._refresh_token_expiry - time.time()) / 86400
        if days_left <= 0:
            logger.error(
                "Pinterest refresh token EXPIRED %.0f days ago. Run "
                "pinterest-mcp-auth to re-authorize.",
                -days_left,
            )
        elif days_left < REFRESH_WARN_DAYS:
            logger.warning(
                "Pinterest refresh token expires in %.0f days. Run "
                "pinterest-mcp-auth before then or access is lost.",
                days_left,
            )

    def _save_token_file(self) -> None:
        path = ensure_token_dir()
        path.write_text(
            json.dumps(
                {
                    "access_token": self._access_token,
                    "refresh_token": self._refresh_token,
                    "expiry": self._token_expiry,
                    "refresh_token_expiry": self._refresh_token_expiry,
                    "updated_at": time.time(),
                },
                indent=2,
            )
        )

    async def _ensure_token(self) -> str:
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token
        if self.sandbox:
            raise RuntimeError(
                "No sandbox token. Set PINTEREST_SANDBOX_TOKEN in .env. Generate "
                "it in the Pinterest app Configure tab with environment set to "
                "Sandbox. Sandbox tokens expire after 24 hours and cannot be "
                "refreshed through the OAuth flow."
            )
        if self._refresh_token:
            await self._refresh()
            return self._access_token  # type: ignore[return-value]
        raise RuntimeError("No Pinterest access token. Run `pinterest-mcp-auth` to authenticate.")

    async def _refresh(self) -> None:
        resp = await self._http.post(
            PINTEREST_AUTH,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
            auth=(self.client_id, self.client_secret),
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        now = time.time()
        self._token_expiry = now + data.get("expires_in", 3600)
        if data.get("refresh_token_expires_in") is not None:
            self._refresh_token_expiry = now + data["refresh_token_expires_in"]
        self._save_token_file()
        logger.info("Pinterest token refreshed")
        self._warn_if_refresh_window_closing()

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        token = await self._ensure_token()
        resp = await self._http.request(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )
        if resp.status_code >= 400:
            raise PinterestAPIError(resp.status_code, method, path, resp.text)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    async def _rate_limited_pin(self, pin_data: dict[str, Any]) -> dict[str, Any]:
        """Create a pin with rate limiting (10/min)."""
        now = time.time()
        self._pin_timestamps = [t for t in self._pin_timestamps if now - t < _PIN_RATE_WINDOW]
        if len(self._pin_timestamps) >= _PIN_RATE_LIMIT:
            sleep_for = _PIN_RATE_WINDOW - (now - self._pin_timestamps[0])
            logger.info("Rate limit: sleeping %.1fs", sleep_for)
            await asyncio.sleep(sleep_for)
        result = await self._request("POST", "/pins", json=pin_data)
        self._pin_timestamps.append(time.time())
        return result

    # ------------------------------------------------------------------
    # Pins
    # ------------------------------------------------------------------

    async def create_pin(
        self,
        board_id: str,
        title: str,
        description: str,
        image_url: str | None = None,
        image_path: str | None = None,
        link: str | None = None,
        alt_text: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create a Pinterest pin.

        Either ``image_url`` (remote) or ``image_path`` (local file) must be
        supplied. A pin cannot be created without an image. ``image_path``
        takes precedence if both are given (file is base64-encoded and sent
        directly, avoiding the need for a public CDN URL).

        Trial access: BLOCKED in production. VERIFIED on 2026-09-07, a real
        call returned:

            HTTP 403 {"code":29,"message":"Apps with Trial access may not
            create Pins in production https://api.pinterest.com - use API
            Sandbox https://api-sandbox.pinterest.com instead."}

        This fails loudly. It does NOT silently create an invisible pin, so
        the documented "Sandbox entities" warning does not apply here.

        Upstream's docstring claimed Pinterest has no sandbox environment.
        That is wrong: the sandbox is https://api-sandbox.pinterest.com and
        POST /pins is x-sandbox: enabled there. Construct the client with
        sandbox=True and a PINTEREST_SANDBOX_TOKEN to exercise pin creation.

        ``dry_run=True`` validates inputs and returns the payload without
        calling the API at all.
        """
        if not image_url and not image_path:
            raise ValueError("Either image_url or image_path must be provided")

        if image_path:
            img_bytes = Path(image_path).read_bytes()
            img_b64 = base64.b64encode(img_bytes).decode()
            content_type = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
            media_source: dict[str, Any] = {
                "source_type": "image_base64",
                "content_type": content_type,
                "data": img_b64,
            }
        else:
            media_source = {"source_type": "image_url", "url": image_url}

        payload: dict[str, Any] = {
            "board_id": board_id,
            "title": title,
            "description": description,
            "media_source": media_source,
        }
        if link:
            payload["link"] = link
        if alt_text:
            payload["alt_text"] = alt_text

        if dry_run:
            return {
                "dry_run": True,
                "status": "validated",
                "board_id": board_id,
                "title": title,
                "image_source": image_path or image_url,
                "link": link,
            }

        try:
            return await self._rate_limited_pin(payload)
        except PinterestAPIError as err:
            if _PIN_CREATE_GATE in err.body:
                raise TrialAccessError(
                    err,
                    feature="Creating pins in production",
                    remedy=(
                        "Standard access is required. No pin was created. To create "
                        "pins before then, use the sandbox host (sandbox=True) with a "
                        "PINTEREST_SANDBOX_TOKEN."
                    ),
                ) from err
            raise

    async def update_pin(
        self,
        pin_id: str,
        title: str | None = None,
        description: str | None = None,
        link: str | None = None,
        board_id: str | None = None,
    ) -> dict[str, Any]:
        """Edit an existing pin's title, description, link or board.

        Endpoint: PATCH /pins/{pin_id}.
        Scopes: boards:read, boards:write, pins:read, pins:write.

        Trial access: BLOCKED. VERIFIED on 2026-09-07 with a no-op call that
        wrote a pin's own description back to itself:

            HTTP 401 {"code":3,"message":"Your application does not have
            access to this restricted feature: pin_edit"}

        pins:write WAS granted, so this is a Trial-versus-Standard feature
        gate, not a scope problem. Nothing was modified. Use sandbox=True to
        exercise this call, or apply for Standard access.
        """
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if link is not None:
            payload["link"] = link
        if board_id is not None:
            payload["board_id"] = board_id
        try:
            return await self._request("PATCH", f"/pins/{pin_id}", json=payload)
        except PinterestAPIError as err:
            if _PIN_EDIT_GATE in err.body:
                raise TrialAccessError(
                    err,
                    feature="Editing pins (pin_edit)",
                    remedy=(
                        "Standard access is required; the pins:write scope alone is not "
                        "enough and was already granted. Nothing was modified. To "
                        "exercise pin edits before then, use the sandbox host "
                        "(sandbox=True)."
                    ),
                ) from err
            raise

    async def delete_pin(self, pin_id: str) -> dict[str, Any]:
        """Delete a pin permanently. This cannot be undone.

        Endpoint: DELETE /pins/{pin_id}.

        Trial access: UNTESTED against production. The only way to test it
        there is to destroy a real pin, since Trial blocks pin creation so
        no throwaway pin can be made. Given that create and edit are both
        gated, expect this to be blocked too, but that is an expectation,
        not a result. DELETE /pins/{pin_id} is x-sandbox: enabled, so test
        it with sandbox=True.
        """
        return await self._request("DELETE", f"/pins/{pin_id}")

    async def get_pin_analytics(
        self,
        pin_id: str,
        start_date: str,
        end_date: str,
        metrics: list[str] | None = None,
    ) -> dict[str, Any]:
        """Impressions, saves and clicks for a single pin over a date range.

        Endpoint: GET /pins/{pin_id}/analytics. Scopes: boards:read, pins:read.

        Trial access: VERIFIED WORKING on 2026-09-07. Returns real daily
        metrics with data_status READY.

        Endpoint is x-sandbox: disabled, so production only. The related
        multi-pin endpoint GET /pins/analytics is blocked under Trial with
        HTTP 401 code 3, "restricted feature".
        """
        if metrics is None:
            metrics = ["IMPRESSION", "SAVE", "PIN_CLICK", "OUTBOUND_CLICK", "ENGAGEMENT"]
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "metric_types": ",".join(metrics),
            "app_types": "ALL",
        }
        return await self._request("GET", f"/pins/{pin_id}/analytics", params=params)

    async def bulk_create_pins(
        self,
        board_id: str,
        pins: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Create multiple pins, respecting the 10/min rate limit.

        Each pin dict must include either ``image_url`` or ``image_path``.
        """
        results = []
        for pin in pins:
            result = await self.create_pin(
                board_id=board_id,
                title=pin.get("title", ""),
                description=pin.get("description", ""),
                image_url=pin.get("image_url"),
                image_path=pin.get("image_path"),
                link=pin.get("link"),
                alt_text=pin.get("alt_text"),
                dry_run=dry_run,
            )
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Boards
    # ------------------------------------------------------------------

    async def list_boards(self, privacy: str = "ALL") -> list[dict[str, Any]]:
        data = await self._request("GET", "/boards", params={"privacy": privacy})
        return data.get("items", [])

    async def create_board(
        self,
        name: str,
        description: str = "",
        privacy: str = "PUBLIC",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/boards",
            json={"name": name, "description": description, "privacy": privacy},
        )

    async def get_board_pins(self, board_id: str, page_size: int = 25) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", f"/boards/{board_id}/pins", params={"page_size": page_size}
        )
        return data.get("items", [])

    async def update_board(
        self,
        board_id: str,
        name: str | None = None,
        description: str | None = None,
        privacy: str | None = None,
    ) -> dict[str, Any]:
        """Rename a board, rewrite its description, or change its privacy.

        Endpoint: PATCH /boards/{board_id}. Scopes: boards:read, boards:write.
        Only the fields you pass are changed.

        Trial access: VERIFIED WORKING for name and description on
        2026-09-07. Both persisted and the rename appeared on the public
        profile in an unauthenticated fetch, so board edits are NOT
        sandboxed.

        One exception: privacy="SECRET" returns HTTP 403 code 29 with the
        current scope set. privacy="PUBLIC" succeeds. The most likely cause
        is the missing boards:write_secret scope rather than a Trial limit,
        but that is unproven.
        """
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if privacy is not None:
            payload["privacy"] = privacy
        if not payload:
            raise ValueError("Pass at least one of name, description, privacy")
        return await self._request("PATCH", f"/boards/{board_id}", json=payload)

    async def delete_board(self, board_id: str) -> dict[str, Any]:
        """Delete a board and every pin on it. This cannot be undone.

        Endpoint: DELETE /boards/{board_id}. Scopes: boards:read, boards:write.

        Trial access: VERIFIED WORKING on 2026-09-07. Returned 204, and a
        follow-up read returned {"code":40,"message":"Board not found."}.
        The board also disappeared from the public profile.
        """
        return await self._request("DELETE", f"/boards/{board_id}")

    async def get_board(self, board_id: str) -> dict[str, Any]:
        """Fetch one board by ID.

        Endpoint: GET /boards/{board_id}. Scope: boards:read.
        Trial access: VERIFIED WORKING on 2026-09-07.
        """
        return await self._request("GET", f"/boards/{board_id}")

    async def list_board_sections(self, board_id: str, page_size: int = 25) -> list[dict[str, Any]]:
        """List the sections of a board.

        Endpoint: GET /boards/{board_id}/sections. Scope: boards:read.
        Trial access: VERIFIED WORKING on 2026-09-07.
        """
        data = await self._request(
            "GET", f"/boards/{board_id}/sections", params={"page_size": page_size}
        )
        return data.get("items", [])

    async def create_board_section(self, board_id: str, name: str) -> dict[str, Any]:
        """Create a section within a board.

        Endpoint: POST /boards/{board_id}/sections. Scopes: boards:read, boards:write.

        Trial access: VERIFIED WORKING on 2026-09-07. This is a board-side
        write that Trial permits, unlike anything pin-side.
        """
        return await self._request("POST", f"/boards/{board_id}/sections", json={"name": name})

    async def delete_board_section(self, board_id: str, section_id: str) -> dict[str, Any]:
        """Delete a board section.

        Endpoint: DELETE /boards/{board_id}/sections/{section_id}.
        Trial access: VERIFIED WORKING on 2026-09-07.
        """
        return await self._request("DELETE", f"/boards/{board_id}/sections/{section_id}")

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_account_info(self) -> dict[str, Any]:
        """Fetch the authenticated account's profile and counts.

        Endpoint: GET /user_account. Scope: user_accounts:read.
        Returns username, business name, follower/board/pin counts and
        monthly_views.

        Trial access: VERIFIED WORKING on 2026-09-07, returning real
        production data, not sandbox data.
        """
        return await self._request("GET", "/user_account")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search_pins(self, query: str, page_size: int = 25) -> list[dict[str, Any]]:
        """Search the ACCOUNT'S OWN pins by keyword, not public Pinterest.

        Endpoint: GET /search/pins (operationId search_user_pins/list).
        Scopes: boards:read, boards:read_secret, pins:read, pins:read_secret.
        The two _secret scopes are mandatory here even for public pins.

        Trial access: VERIFIED WORKING on 2026-09-07.

        The name is misleading: this does not search Pinterest at large, it
        searches the authenticated user's pins. x-sandbox: disabled, so
        production only.
        """
        data = await self._request(
            "GET",
            "/search/pins",
            params={"query": query, "page_size": page_size},
        )
        return data.get("items", [])

    # ------------------------------------------------------------------
    # Analytics (account-level)
    # ------------------------------------------------------------------

    async def get_account_analytics(
        self,
        start_date: str,
        end_date: str,
        metrics: list[str] | None = None,
    ) -> dict[str, Any]:
        if metrics is None:
            metrics = ["IMPRESSION", "SAVE", "OUTBOUND_CLICK", "PIN_CLICK", "ENGAGEMENT"]
        return await self._request(
            "GET",
            "/user_account/analytics",
            params={
                "start_date": start_date,
                "end_date": end_date,
                "metric_types": ",".join(metrics),
            },
        )

    async def get_trending(
        self,
        region: str = "US",
        trend_type: str = "growing",
        interests: list[str] | None = None,
        include_keywords: list[str] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List top trending keywords for a region.

        Endpoint: GET /trends/keywords/{region}/top/{trend_type}
        Scope: user_accounts:read

        Upstream called ``GET /trends/keywords`` with ``interests`` and
        ``region`` as query parameters. That path does not exist in the v5
        spec, so the upstream version returned 404. Region and trend type are
        path segments.

        ``trend_type`` is one of: growing, monthly, yearly, seasonal.

        Under Trial access: UNTESTED. This reads Pinterest-wide trend data
        rather than account-owned objects, so sandboxing of the caller's own
        content should not apply, but that has not been confirmed by a call.
        """
        params: dict[str, Any] = {"limit": limit}
        if interests:
            params["interests"] = interests
        if include_keywords:
            params["include_keywords"] = include_keywords
        return await self._request(
            "GET",
            f"/trends/keywords/{region}/top/{trend_type}",
            params=params,
        )

    async def get_pin(self, pin_id: str) -> dict[str, Any]:
        """Fetch one pin by ID.

        Endpoint: GET /pins/{pin_id}. Scopes: boards:read, pins:read.
        Trial access: VERIFIED WORKING on 2026-09-07.
        """
        return await self._request("GET", f"/pins/{pin_id}")

    async def list_pins(
        self,
        page_size: int = 25,
        bookmark: str | None = None,
        fetch_all: bool = False,
        pin_metrics: bool = False,
    ) -> dict[str, Any]:
        """List pins on the account, with pagination.

        Endpoint: GET /pins. Scopes: boards:read, pins:read.

        Returns {"items": [...], "bookmark": str | None}. Pass the bookmark
        back to get the next page, or set fetch_all=True to walk every page
        and return the combined list with bookmark=None.

        Trial access: VERIFIED WORKING on 2026-09-07, returning real
        production pins.
        """
        params: dict[str, Any] = {"page_size": page_size}
        if pin_metrics:
            params["pin_metrics"] = True
        if not fetch_all:
            if bookmark:
                params["bookmark"] = bookmark
            return await self._request("GET", "/pins", params=params)

        items: list[dict[str, Any]] = []
        cursor = bookmark
        while True:
            if cursor:
                params["bookmark"] = cursor
            page = await self._request("GET", "/pins", params=params)
            items.extend(page.get("items", []))
            cursor = page.get("bookmark")
            if not cursor:
                break
        return {"items": items, "bookmark": None}

    # ------------------------------------------------------------------
    # Keyword research
    # ------------------------------------------------------------------

    async def get_suggested_keywords(self, term: str, limit: int = 10) -> list[str]:
        """Get Pinterest's suggested search terms for a seed term.

        Endpoint: GET /terms/suggested. Scope: ads:read.

        Trial access: reachable and returns HTTP 200, VERIFIED on
        2026-09-07. Be warned that the data is thin: for "printable
        calendar", "calendar", "chore chart" and "graph paper" it returned
        only the input term itself and nothing else. Treat an unhelpful
        result as normal rather than as a bug. get_trending is the more
        useful keyword tool for this account.
        """
        data = await self._request("GET", "/terms/suggested", params={"term": term, "limit": limit})
        return data if isinstance(data, list) else data.get("items", [])

    async def get_related_keywords(self, terms: list[str]) -> dict[str, Any]:
        """Get terms Pinterest considers related to the given terms.

        Endpoint: GET /terms/related. Scope: ads:read.

        Trial access: reachable and returns HTTP 200, VERIFIED on
        2026-09-07, but returned related_term_count: 0 for "printable
        calendar". Same caveat as get_suggested_keywords.
        """
        return await self._request("GET", "/terms/related", params={"terms": terms})

    # ------------------------------------------------------------------
    # Top pins analytics
    # ------------------------------------------------------------------

    async def get_top_pins_analytics(
        self,
        start_date: str,
        end_date: str,
        sort_by: str = "IMPRESSION",
        metrics: list[str] | None = None,
        num_of_pins: int = 10,
    ) -> dict[str, Any]:
        """Rank the account's pins by a metric over a date range.

        Endpoint: GET /user_account/analytics/top_pins.
        Scopes: pins:read, user_accounts:read.
        sort_by is one of ENGAGEMENT, SAVE, IMPRESSION, OUTBOUND_CLICK, PIN_CLICK.

        Trial access: VERIFIED WORKING on 2026-09-07, returning real
        per-pin impressions. This was expected to be inert under Trial and
        is not. It is the most useful analytics call available, because it
        says which specific pins are earning impressions.

        Note this endpoint is x-sandbox: disabled, so it works ONLY against
        production, not the sandbox host.
        """
        if metrics is None:
            metrics = [sort_by]
        return await self._request(
            "GET",
            "/user_account/analytics/top_pins",
            params={
                "start_date": start_date,
                "end_date": end_date,
                "sort_by": sort_by,
                "metric_types": ",".join(metrics),
                "num_of_pins": num_of_pins,
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()
