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
