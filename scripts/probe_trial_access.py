"""Probe what Pinterest Trial access actually permits, against the real account.

Staged so the board-visibility check can happen in a logged-out browser
between create and delete.

    python scripts/probe_trial_access.py read
    python scripts/probe_trial_access.py board-create
    ... check the public profile in a logged-out browser ...
    python scripts/probe_trial_access.py board-delete <board_id>
    python scripts/probe_trial_access.py analytics

Every response is printed raw. Nothing here is inferred.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import random
import sys

from pinterest_mcp.client import PinterestAPIError, PinterestClient


def show(label: str, payload: object) -> None:
    print(f"\n----- {label} -----")
    print(json.dumps(payload, indent=2, default=str)[:4000])


async def _call(label: str, coro) -> object | None:
    try:
        result = await coro
    except PinterestAPIError as exc:
        print(f"\n----- {label} -----")
        print(f"FAILED  HTTP {exc.status_code}  {exc.method} {exc.path}")
        print(exc.body[:2000])
        return None
    except Exception as exc:  # noqa: BLE001 - probe script, report anything
        print(f"\n----- {label} -----")
        print(f"FAILED  {type(exc).__name__}: {exc}")
        return None
    show(label, result)
    return result


async def probe_read() -> None:
    c = PinterestClient()
    try:
        await _call("GET /user_account", c._request("GET", "/user_account"))
        boards = await _call("GET /boards", c._request("GET", "/boards", params={"page_size": 25}))
        await _call("GET /pins", c._request("GET", "/pins", params={"page_size": 25}))
        if isinstance(boards, dict) and boards.get("items"):
            bid = boards["items"][0]["id"]
            await _call(
                f"GET /boards/{bid}/pins",
                c._request("GET", f"/boards/{bid}/pins", params={"page_size": 10}),
            )
    finally:
        await c.aclose()


async def probe_board_create() -> None:
    c = PinterestClient()
    name = f"zz-trial-probe-{random.randint(1000, 9999)}"
    try:
        created = await _call(
            f"POST /boards (name={name})",
            c.create_board(
                name=name,
                description="Temporary probe board. Delete me.",
                privacy="PUBLIC",
            ),
        )
        if isinstance(created, dict) and created.get("id"):
            print(f"\nBoard id: {created['id']}")
            print("Now open the public profile in a LOGGED-OUT browser and look for:")
            print(f"  {name}")
            print(f"Then run: python scripts/probe_trial_access.py board-delete {created['id']}")
    finally:
        await c.aclose()


async def probe_board_delete(board_id: str) -> None:
    c = PinterestClient()
    try:
        await _call(
            f"GET /boards/{board_id} (before delete)", c._request("GET", f"/boards/{board_id}")
        )
        await _call(f"DELETE /boards/{board_id}", c._request("DELETE", f"/boards/{board_id}"))
        await _call(
            f"GET /boards/{board_id} (after delete)", c._request("GET", f"/boards/{board_id}")
        )
    finally:
        await c.aclose()


async def probe_analytics() -> None:
    c = PinterestClient()
    end = dt.datetime.now(tz=dt.UTC).date()
    start = end - dt.timedelta(days=29)
    try:
        await _call(
            f"GET /user_account/analytics {start}..{end}",
            c.get_account_analytics(start_date=str(start), end_date=str(end)),
        )
    finally:
        await c.aclose()


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "read":
        asyncio.run(probe_read())
    elif cmd == "board-create":
        asyncio.run(probe_board_create())
    elif cmd == "board-delete":
        asyncio.run(probe_board_delete(sys.argv[2]))
    elif cmd == "analytics":
        asyncio.run(probe_analytics())
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
