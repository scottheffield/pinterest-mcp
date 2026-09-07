"""Pinterest MCP server entry point."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .client import PinterestClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Server("pinterest-mcp")
_client: PinterestClient | None = None


def _get_client() -> PinterestClient:
    global _client
    if _client is None:
        _client = PinterestClient()
    return _client


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="create_pin",
            description=(
                "Create a new Pinterest pin. An image is always required — provide either "
                "image_url (remote URL) or image_path (local file path). At least one must "
                "be supplied; image_path takes precedence if both are given. "
                "Use dry_run=true to validate without posting (useful during development — "
                "note Pinterest has no sandbox; dry_run prevents any real API call)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "board_id": {"type": "string", "description": "Target board ID"},
                    "title": {"type": "string", "description": "Pin title"},
                    "description": {
                        "type": "string",
                        "description": "Pin description (include keywords)",
                    },
                    "image_url": {
                        "type": "string",
                        "description": "Publicly accessible image URL (mutually exclusive with image_path)",
                    },
                    "image_path": {
                        "type": "string",
                        "description": "Absolute local path to JPEG/PNG image (mutually exclusive with image_url)",
                    },
                    "link": {
                        "type": "string",
                        "description": "Destination URL (e.g. Cults3D listing)",
                    },
                    "alt_text": {"type": "string", "description": "Alt text for accessibility"},
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, validate and return payload without posting. Pinterest has no sandbox — use this for testing.",
                        "default": False,
                    },
                },
                "required": ["board_id", "title", "description"],
                "oneOf": [
                    {"required": ["image_url"]},
                    {"required": ["image_path"]},
                ],
            },
        ),
        types.Tool(
            name="update_pin",
            description="Update metadata on an existing pin.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pin_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "link": {"type": "string"},
                    "board_id": {"type": "string"},
                },
                "required": ["pin_id"],
            },
        ),
        types.Tool(
            name="delete_pin",
            description="Delete a pin.",
            inputSchema={
                "type": "object",
                "properties": {"pin_id": {"type": "string"}},
                "required": ["pin_id"],
            },
        ),
        types.Tool(
            name="get_pin_analytics",
            description="Get analytics for a pin: impressions, saves, link clicks, engagement.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pin_id": {"type": "string"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Metrics to fetch (default: IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK)",
                    },
                },
                "required": ["pin_id", "start_date", "end_date"],
            },
        ),
        types.Tool(
            name="list_boards",
            description="List your Pinterest boards.",
            inputSchema={
                "type": "object",
                "properties": {
                    "privacy": {
                        "type": "string",
                        "enum": ["ALL", "PUBLIC", "SECRET"],
                        "default": "ALL",
                    }
                },
            },
        ),
        types.Tool(
            name="create_board",
            description="Create a new Pinterest board.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "privacy": {
                        "type": "string",
                        "enum": ["PUBLIC", "SECRET"],
                        "default": "PUBLIC",
                    },
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="get_board_pins",
            description="List all pins on a specific board.",
            inputSchema={
                "type": "object",
                "properties": {
                    "board_id": {"type": "string"},
                    "page_size": {"type": "integer", "default": 25},
                },
                "required": ["board_id"],
            },
        ),
        types.Tool(
            name="search_pins",
            description="Search public Pinterest pins by keyword. Useful for trend research.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "page_size": {"type": "integer", "default": 25},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_account_analytics",
            description="Get account-level Pinterest analytics: impressions, saves, clicks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "metrics": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        types.Tool(
            name="bulk_create_pins",
            description="Create multiple pins on a board. Automatically rate-limited to 10/min. Each pin must include either image_url or image_path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "board_id": {"type": "string"},
                    "dry_run": {
                        "type": "boolean",
                        "description": "Validate all pins without posting. Pinterest has no sandbox — use this for testing.",
                        "default": False,
                    },
                    "pins": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "image_url": {"type": "string", "description": "Remote image URL"},
                                "image_path": {
                                    "type": "string",
                                    "description": "Local image file path",
                                },
                                "link": {"type": "string"},
                                "alt_text": {"type": "string"},
                            },
                            "required": ["title", "description"],
                            "oneOf": [
                                {"required": ["image_url"]},
                                {"required": ["image_path"]},
                            ],
                        },
                    },
                },
                "required": ["board_id", "pins"],
            },
        ),
        types.Tool(
            name="get_trending",
            description=(
                "List top trending keywords for a region. Scope: user_accounts:read. "
                "trend_type is one of growing, monthly, yearly, seasonal. "
                "Trial access: UNTESTED."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "default": "US",
                        "enum": [
                            "US",
                            "CA",
                            "DE",
                            "FR",
                            "ES",
                            "IT",
                            "DE+AT+CH",
                            "GB+IE",
                            "IT+ES+PT+GR+MT",
                            "PL+RO+HU+SK+CZ",
                            "SE+DK+FI+NO",
                            "NL+BE+LU",
                            "AR",
                            "BR",
                            "CO",
                            "MX",
                            "MX+AR+CO+CL",
                            "AU+NZ",
                        ],
                    },
                    "trend_type": {
                        "type": "string",
                        "default": "growing",
                        "enum": ["growing", "monthly", "yearly", "seasonal"],
                    },
                    "interests": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional interest category filter",
                    },
                    "include_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Only return trends containing these keywords",
                    },
                    "limit": {"type": "integer", "default": 50},
                },
            },
        ),
        types.Tool(
            name="update_board",
            description=(
                "Rename a board, rewrite its description, or change privacy. Only "
                "fields you pass are changed. TRIAL ACCESS: VERIFIED WORKING for "
                "name and description; edits appear on the public profile. "
                "privacy=SECRET returns 403 with current scopes; PUBLIC works."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "board_id": {"type": "string"},
                    "name": {"type": "string", "description": "New board name"},
                    "description": {
                        "type": "string",
                        "description": "Keyword-led board description",
                    },
                    "privacy": {"type": "string", "enum": ["PUBLIC", "SECRET"]},
                },
                "required": ["board_id"],
            },
        ),
        types.Tool(
            name="delete_board",
            description=(
                "Delete a board and every pin on it. Cannot be undone. "
                "TRIAL ACCESS: VERIFIED WORKING."
            ),
            inputSchema={
                "type": "object",
                "properties": {"board_id": {"type": "string"}},
                "required": ["board_id"],
            },
        ),
        types.Tool(
            name="get_board",
            description="Fetch one board by ID. TRIAL ACCESS: VERIFIED WORKING.",
            inputSchema={
                "type": "object",
                "properties": {"board_id": {"type": "string"}},
                "required": ["board_id"],
            },
        ),
        types.Tool(
            name="list_board_sections",
            description="List sections of a board. TRIAL ACCESS: VERIFIED WORKING.",
            inputSchema={
                "type": "object",
                "properties": {
                    "board_id": {"type": "string"},
                    "page_size": {"type": "integer", "default": 25},
                },
                "required": ["board_id"],
            },
        ),
        types.Tool(
            name="create_board_section",
            description=(
                "Create a section within a board. TRIAL ACCESS: VERIFIED WORKING. "
                "One of the few writes Trial permits."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "board_id": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["board_id", "name"],
            },
        ),
        types.Tool(
            name="delete_board_section",
            description="Delete a board section. TRIAL ACCESS: VERIFIED WORKING.",
            inputSchema={
                "type": "object",
                "properties": {
                    "board_id": {"type": "string"},
                    "section_id": {"type": "string"},
                },
                "required": ["board_id", "section_id"],
            },
        ),
        types.Tool(
            name="get_account_info",
            description=(
                "Account profile and counts: username, business name, followers, "
                "board and pin counts, monthly_views. TRIAL ACCESS: VERIFIED "
                "WORKING, returns real production data."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="get_pin",
            description="Fetch one pin by ID. TRIAL ACCESS: VERIFIED WORKING.",
            inputSchema={
                "type": "object",
                "properties": {"pin_id": {"type": "string"}},
                "required": ["pin_id"],
            },
        ),
        types.Tool(
            name="list_pins",
            description=(
                "List pins on the account, paginated. Returns items plus a bookmark; "
                "pass fetch_all=true to walk every page. TRIAL ACCESS: VERIFIED WORKING."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer", "default": 25},
                    "bookmark": {
                        "type": "string",
                        "description": "Cursor from a previous call",
                    },
                    "fetch_all": {"type": "boolean", "default": False},
                    "pin_metrics": {"type": "boolean", "default": False},
                },
            },
        ),
        types.Tool(
            name="get_suggested_keywords",
            description=(
                "Pinterest's suggested search terms for a seed term. Scope ads:read. "
                "TRIAL ACCESS: reachable and returns 200, but data is THIN: it "
                "returned only the input term for several tested seeds. An unhelpful "
                "result is normal, not a bug. Prefer get_trending."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["term"],
            },
        ),
        types.Tool(
            name="get_related_keywords",
            description=(
                "Terms Pinterest considers related to the given terms. Scope ads:read. "
                "TRIAL ACCESS: reachable, returns 200, but returned zero related terms "
                "for the seed tested. Same caveat as get_suggested_keywords."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "terms": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["terms"],
            },
        ),
        types.Tool(
            name="get_top_pins_analytics",
            description=(
                "Rank the account pins by a metric over a date range. TRIAL ACCESS: "
                "VERIFIED WORKING, returns real per-pin impressions. This was expected "
                "to be inert under Trial and is not. Most useful analytics call "
                "available: it says which specific pins earn impressions. Dates are "
                "YYYY-MM-DD. Production only, not available in sandbox."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "sort_by": {
                        "type": "string",
                        "default": "IMPRESSION",
                        "enum": [
                            "ENGAGEMENT",
                            "SAVE",
                            "IMPRESSION",
                            "OUTBOUND_CLICK",
                            "PIN_CLICK",
                        ],
                    },
                    "metrics": {"type": "array", "items": {"type": "string"}},
                    "num_of_pins": {"type": "integer", "default": 10},
                },
                "required": ["start_date", "end_date"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    client = _get_client()
    try:
        if name == "create_pin":
            result = await client.create_pin(**arguments)
        elif name == "dry_run_pin":
            result = await client.create_pin(dry_run=True, **arguments)
        elif name == "update_pin":
            result = await client.update_pin(**arguments)
        elif name == "delete_pin":
            result = await client.delete_pin(arguments["pin_id"])
        elif name == "get_pin_analytics":
            result = await client.get_pin_analytics(**arguments)
        elif name == "list_boards":
            result = await client.list_boards(privacy=arguments.get("privacy", "ALL"))
        elif name == "create_board":
            result = await client.create_board(**arguments)
        elif name == "get_board_pins":
            result = await client.get_board_pins(
                board_id=arguments["board_id"],
                page_size=arguments.get("page_size", 25),
            )
        elif name == "search_pins":
            result = await client.search_pins(
                query=arguments["query"],
                page_size=arguments.get("page_size", 25),
            )
        elif name == "get_account_analytics":
            result = await client.get_account_analytics(**arguments)
        elif name == "bulk_create_pins":
            result = await client.bulk_create_pins(
                board_id=arguments["board_id"],
                pins=arguments["pins"],
                dry_run=arguments.get("dry_run", False),
            )
        elif name == "get_trending":
            result = await client.get_trending(
                region=arguments.get("region", "US"),
                trend_type=arguments.get("trend_type", "growing"),
                interests=arguments.get("interests"),
                include_keywords=arguments.get("include_keywords"),
                limit=arguments.get("limit", 50),
            )
        elif name == "update_board":
            result = await client.update_board(**arguments)
        elif name == "delete_board":
            result = await client.delete_board(arguments["board_id"])
        elif name == "get_board":
            result = await client.get_board(arguments["board_id"])
        elif name == "list_board_sections":
            result = await client.list_board_sections(
                board_id=arguments["board_id"],
                page_size=arguments.get("page_size", 25),
            )
        elif name == "create_board_section":
            result = await client.create_board_section(
                board_id=arguments["board_id"], name=arguments["name"]
            )
        elif name == "delete_board_section":
            result = await client.delete_board_section(
                board_id=arguments["board_id"], section_id=arguments["section_id"]
            )
        elif name == "get_account_info":
            result = await client.get_account_info()
        elif name == "get_pin":
            result = await client.get_pin(arguments["pin_id"])
        elif name == "list_pins":
            result = await client.list_pins(
                page_size=arguments.get("page_size", 25),
                bookmark=arguments.get("bookmark"),
                fetch_all=arguments.get("fetch_all", False),
                pin_metrics=arguments.get("pin_metrics", False),
            )
        elif name == "get_suggested_keywords":
            result = await client.get_suggested_keywords(
                term=arguments["term"], limit=arguments.get("limit", 10)
            )
        elif name == "get_related_keywords":
            result = await client.get_related_keywords(terms=arguments["terms"])
        elif name == "get_top_pins_analytics":
            result = await client.get_top_pins_analytics(
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
                sort_by=arguments.get("sort_by", "IMPRESSION"),
                metrics=arguments.get("metrics"),
                num_of_pins=arguments.get("num_of_pins", 10),
            )
        else:
            raise ValueError(f"Unknown tool: {name}")
    except Exception as exc:  # noqa: BLE001
        return [types.TextContent(type="text", text=f"Error: {exc}")]

    return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
