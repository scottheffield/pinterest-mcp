# pinterest-mcp

An [MCP](https://modelcontextprotocol.io) server for the [Pinterest API v5](https://developers.pinterest.com/docs/api/v5/).

Fork of [clugtu/pinterest-mcp](https://github.com/clugtu/pinterest-mcp), rebuilt
around one question the upstream project never answered: **what actually works
under Trial access?**

Every status in this README came from a real call against a real Pinterest
Business account on 2026-09-07. Nothing is marked working because the code looks
right. Full transcripts, with raw request and response bodies, are in
[docs/trial-access-findings.md](docs/trial-access-findings.md).

**The sample is one account.** A single small Business account with Trial
access, 8 boards and 27 pins. Access tiers, granted scopes and account type all
change what Pinterest allows, so treat this as a worked example with its
evidence attached rather than as a specification. Where a result probably turns
on this account's particulars, the text says so.

---

## The Trial-access reality

Pinterest gates apps into Trial and Standard tiers. Most of what you read about
Trial, including Pinterest's own summary, is misleading in practice.

**What the docs say:** pins and boards created under Trial are "only visible to
their creator as Sandbox entities."

**What actually happens:**

| Claim | Reality |
|---|---|
| Created objects are invisible sandbox entities | **False for boards.** A board created via the API appeared on the public profile in an unauthenticated fetch |
| Creates silently no-op | **False.** Pin creation returns a loud `403` naming the sandbox host |
| Reads are sandboxed | **False.** Reads return real production boards, pins and analytics |
| Analytics is unavailable | **False.** Account, per-pin, and top-pin analytics all return real data |

The real dividing line is not sandbox-versus-production. It is **boards versus
pins**. Board operations work. Pin writes are gated behind Standard access.

### Two hosts, and you need both

Upstream's `create_pin` docstring says "Pinterest has no sandbox environment."
That is wrong. Attempting a pin create in production returns:

```json
HTTP 403 {"code":29,"message":"Apps with Trial access may not create Pins in production
https://api.pinterest.com - use API Sandbox https://api-sandbox.pinterest.com instead."}
```

The hosts are complementary, not interchangeable:

| | Production (`api.pinterest.com`) | Sandbox (`api-sandbox.pinterest.com`) |
|---|---|---|
| Reads | ✅ Real data | ❓ Isolated test data |
| Board CRUD | ✅ Works | ❓ Expected to work |
| **Pin CRUD** | ❌ **Blocked under Trial** | ❓ **Expected to work** |
| **Analytics** | ✅ **Works** | ❌ **`x-sandbox: disabled`** |
| Search | ✅ Works | ❌ `x-sandbox: disabled` |
| Auth | OAuth token, 30 days | Separate token, **24 hours** |

The sandbox rejects production OAuth tokens outright (`401 code 2`). Generate a
sandbox token in your app's Configure tab with the environment set to Sandbox,
then put it in `.env` as `PINTEREST_SANDBOX_TOKEN`.

> **The sandbox column is not verified.** Two things about it were observed
> directly: the production `403` quoted above names the sandbox host, and the
> sandbox rejects a production token with `401 code 2`. Everything else in that
> column is read from Pinterest's endpoint metadata. **No sandbox call has ever
> succeeded against this app**, because no sandbox token has ever been issued.
> `PinterestClient(sandbox=True)` is wired, and **no call has ever been made
> through it**. Mark it verified only after a real one.

To route the server at the sandbox host, set both of these before starting it:

```bash
PINTEREST_SANDBOX=1
PINTEREST_SANDBOX_TOKEN=<token from the app's Configure tab, Sandbox environment>
```

In library use, `PinterestClient(sandbox=True)` does the same thing.

---

## Tool status

Verified means it was called against the live account and returned data.
Untested means exactly that. Nothing is listed as working on inspection alone.

### Boards and account: all verified working

| Tool | Status | Notes |
|---|---|---|
| `list_boards` | ✅ Verified | Real production boards |
| `get_board` | ✅ Verified | |
| `create_board` | ✅ Verified | Board appeared on the public profile, logged out |
| `update_board` | ✅ Verified | Rename and description both propagate publicly |
| `delete_board` | ✅ Verified | Follow-up read returned `{"code":40,"message":"Board not found."}` |
| `get_board_pins` | ✅ Verified | |
| `list_board_sections` | ✅ Verified | |
| `create_board_section` | ✅ Verified | |
| `delete_board_section` | ✅ Verified | |
| `get_account_info` | ✅ Verified | Real profile and counts |

One exception: `update_board` with `privacy="SECRET"` returns `403 code 29`.
`privacy="PUBLIC"` succeeds, and a `name`-only write succeeds immediately after
the failure, so the field is writable and only `SECRET` is refused. Most likely
the missing `boards:write_secret` scope rather than a Trial limit. **Unproven.**

### Reads and analytics: all verified working

| Tool | Status | Notes |
|---|---|---|
| `get_pin` | ✅ Verified | |
| `list_pins` | ✅ Verified | Bookmark pagination, `fetch_all` walks every page |
| `search_pins` | ✅ Verified | Searches **your own** pins, not public Pinterest |
| `get_account_analytics` | ✅ Verified | Real metrics, `data_status: READY` |
| `get_pin_analytics` | ✅ Verified | Real daily metrics |
| `get_top_pins_analytics` | ✅ Verified | Real per-pin impressions. Expected to be inert under Trial; it is not |
| `get_trending` | ✅ Verified | Real trend data with weekly time series |

`get_top_pins_analytics` is the most useful call available on Trial, because it
names which specific pins earn impressions.

### Keyword research: reachable, but thin

| Tool | Status | Notes |
|---|---|---|
| `get_suggested_keywords` | ⚠️ Verified reachable | HTTP 200, but returned **only the input term** for every seed tested |
| `get_related_keywords` | ⚠️ Verified reachable | HTTP 200, `related_term_count: 0` |

Both need the `ads:read` scope. They do not error, they just return very little.
An unhelpful result is normal here, not a bug. `get_trending` is the more useful
keyword tool.

The seeds tested were all in one niche (printables: calendars, chore charts,
graph paper). Whether these endpoints are thin in general or thin for this
subject matter is **not established**. Try your own seeds before concluding
they are useless.

### Pin writes: blocked under Trial

| Tool | Status | Evidence |
|---|---|---|
| `create_pin` | ❌ Blocked | `403 code 29`, "may not create Pins in production" |
| `bulk_create_pins` | ❌ Blocked | Same, it wraps `create_pin` |
| `update_pin` | ❌ Blocked | `401 code 3`, "restricted feature: `pin_edit`" |
| `delete_pin` | ❓ Untested | See below |

`pins:write` **was granted**. These are Trial-versus-Standard feature gates, not
scope problems. Both fail loudly rather than silently sandboxing.

`create_pin` and `update_pin` translate their gate responses into a
`TrialAccessError` that names Standard access as the requirement and states
that nothing was created or modified, instead of surfacing a raw HTTP error.
The original status, path and response body are preserved on the exception.
Any other error, a `404` for instance, passes through unchanged rather than
being mislabelled as a Trial limit. Use `create_pin(dry_run=true)` to validate
a payload without calling the API at all.

`delete_pin` is untested because the only way to test it in production is to
destroy a real pin, and Trial blocks pin creation so no throwaway pin can be
made. Both are `x-sandbox: enabled`, so test them against the sandbox host.

---

## What changed from upstream

Nine defects. Four were found by reading, three by running the server, and
two by auditing what the tool list actually exposes:

1. **`auth.py` imported `aiohttp`, which was never a declared dependency.** A
   clean install died on `ImportError`. Rewritten on stdlib `http.server`; the
   dependency is gone rather than added. The listener now also validates the
   OAuth `state` parameter, which upstream generated and then ignored.
2. **The token file path was relative and declared twice**, in `auth.py` and
   `client.py`, so a live credential landed wherever the MCP client happened to
   launch. Now one absolute path in `config.py`.
3. **The refresh window was never recorded.** Only the access-token expiry was
   stored, so the 60-day continuous refresh window was invisible until after it
   closed. Now stored, with a warning under 14 days and an error once expired.
4. **The server did not start.** `mcp>=1.0.0` resolves to 2.x, which removed the
   `@server.list_tools()` decorator this server is built on. Pinned `mcp<2`.
5. **`get_trending` called a path that does not exist.** Upstream used
   `GET /trends/keywords?region=US`. The real endpoint is
   `/trends/keywords/{region}/top/{trend_type}` with both as path segments.
6. **`.env` was never loaded.** `python-dotenv` was a declared dependency with
   zero call sites. The client reads the client ID and secret from the
   environment to refresh the token, so access would have died at day 30 with an
   opaque 401.
7. **API error bodies were discarded.** `raise_for_status()` throws away the
   response body, which is where Pinterest puts the useful part. Errors now
   raise `PinterestAPIError` carrying status, method, path and body. Every Trial
   finding in this README depends on that change.

8. **`dry_run_pin` was handled but never advertised.** `call_tool` had a
   branch for it and `list_tools` did not, so no client could ever reach it.
   The branch is deleted rather than advertised, because `create_pin` already
   takes a `dry_run` flag and a second tool for it was redundant. A test now
   asserts that the set of tools in `list_tools` and the set dispatched in
   `call_tool` are identical in both directions, so this class of bug cannot
   come back quietly.
9. **Eleven inherited tool descriptions stated no Trial status**, and two
   stated something false: `search_pins` claimed to search public Pinterest,
   and `create_pin` repeated upstream's claim that Pinterest has no sandbox.
   Every tool description now carries its verified Trial status, and a test
   enforces that.

Plus 12 new tools and sandbox host support.

---

## Scopes

Settled by reading the `security` block of each endpoint in Pinterest's own
[OpenAPI spec](https://github.com/pinterest/api-description) (v5.28.0), not from
blog posts. Changing scopes later means re-running the whole browser flow.

| Scope | Why |
|---|---|
| `boards:read` | Board reads. Also required by every pin endpoint and every board write |
| `boards:write` | Create, update, delete boards and sections |
| `pins:read` | Pin reads, pin analytics, top-pin analytics |
| `pins:write` | Pin writes. Granted, but gated by Trial |
| `user_accounts:read` | Account info, account analytics, **and trends** |
| `ads:read` | `/terms/suggested` and `/terms/related`. Keyword research sits behind an ads scope |
| `boards:read_secret` | Required by `/search/pins`, even for public pins |
| `pins:read_secret` | Required by `/search/pins` |

The last three are what upstream was missing. Override with `PINTEREST_SCOPES`
if your app is not approved for one of them.

---

## Token storage

```
~/.config/pinterest/token.json
```

`C:\Users\<you>\.config\pinterest\token.json` on Windows. Outside the repo,
created on demand, `chmod 600` where supported. Override with
`PINTEREST_TOKEN_FILE`.

Stored fields: `access_token`, `refresh_token`, `expiry`,
`refresh_token_expiry`, `scope`, `updated_at`. The file is created mode `0600`
whether it is written by the auth flow or by a later refresh.

Setting `PINTEREST_ACCESS_TOKEN` takes precedence and suppresses reading this
file, so unset it to go back to the stored token and automatic refresh.

### Environment variables

| Variable | Effect |
|---|---|
| `PINTEREST_CLIENT_ID`, `PINTEREST_CLIENT_SECRET` | Required. Also needed after first auth, to refresh the token |
| `PINTEREST_TOKEN_FILE` | Override the token path |
| `PINTEREST_SCOPES` | Override the requested scopes |
| `PINTEREST_REDIRECT_URI` | Override the OAuth callback |
| `PINTEREST_SANDBOX` | `1` routes the server at the sandbox host. Untested |
| `PINTEREST_SANDBOX_TOKEN` | The sandbox's own 24-hour token |
| `PINTEREST_DOTENV` | Load this `.env` instead of searching |
| `PINTEREST_ACCESS_TOKEN`, `PINTEREST_REFRESH_TOKEN` | Supply a token directly, bypassing the auth flow |

`.env` is searched for in the working directory, then the repo root, then
`~/.config/pinterest/.env`, and every one found is loaded, earlier files
winning. An MCP client chooses the working directory it launches the server
from, which is often another project entirely, so do not rely on the first
location alone.

Access tokens last 30 days. Refresh tokens are the continuous type: a 60-day
window that rotates on every use and is refreshable indefinitely as long as it
is used inside the window. Once that window closes the only recovery is a full
browser re-auth, which is why the client warns at 14 days.

---

## Setup

Requires **Python 3.11 or newer**. If your system Python is older, `pip
install` will refuse; [uv](https://docs.astral.sh/uv/) fetches a suitable
interpreter for you, which is why it is the documented path here.

```bash
git clone https://github.com/scottheffield/pinterest-mcp
cd pinterest-mcp
uv venv --python 3.12
uv pip install -e ".[dev]"
cp .env.template .env      # then fill in client ID and secret
```

You need a Pinterest app of your own, from the
[developer console](https://developers.pinterest.com/apps/). Put its client ID
and secret in `.env`. The app starts on Trial access, which is what this README
documents.

Register this redirect URI in your Pinterest app settings, character for
character, port included:

```
http://localhost:8089/callback
```

Then run the flow. It opens a browser and writes the token:

```bash
pinterest-mcp-auth
```

### MCP client config

Point the command at the console script inside the venv you just created.

macOS and Linux:

```json
{
  "mcpServers": {
    "pinterest": {
      "command": "/path/to/pinterest-mcp/.venv/bin/pinterest-mcp"
    }
  }
}
```

Windows:

```json
{
  "mcpServers": {
    "pinterest": {
      "command": "C:\\path\\to\\pinterest-mcp\\.venv\\Scripts\\pinterest-mcp.exe"
    }
  }
}
```

Credentials come from `.env` and the token file, so they do not belong in this
config. Upstream's README put the access token in this block as an environment
variable; do not do that.

---

## Rate limits

Trial: 1000 reads/day and 300 writes/day per app. Standard: 1000/min and
100/min. At single-account scale the Trial ceiling is not a practical
constraint. `bulk_create_pins` still throttles to 10 pins/minute.

---

## Applying for Standard access

Pin writes are the main thing Standard unlocks. Pinterest requires a **screen
recording of an agent driving the integration through a real OAuth flow** as
part of the application, and **terminal recordings are explicitly accepted**, so
a recorded `pinterest-mcp-auth` run followed by a few tool calls satisfies it.
There is no need to build a GUI for this.

---

## Development

```bash
uv pip install -e ".[dev]"
pre-commit install
pytest
```

`scripts/probe_trial_access.py` re-runs the access probes in stages, so the
board-visibility check can happen in a logged-out browser between the create and
the delete. It prints raw responses only.

The suite is offline; every HTTP call is mocked. `tests/test_tool_registry.py`
is the one to keep green when adding a tool: it fails if a tool is advertised
without a dispatch branch, dispatched without being advertised, advertised
without a client method, or described without a Trial-access status.

---

## License

MIT, see [LICENSE](LICENSE). Upstream: [clugtu/pinterest-mcp](https://github.com/clugtu/pinterest-mcp).
