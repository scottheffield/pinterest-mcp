# Handoff notes

State as of 2026-09-07. Read this before touching the repo.

## Where things stand

Branch `phase-0-trial-fixes`, 7 commits, working tree clean, **nothing pushed**.
Phases 0, 1 and 2 are complete. `main` is untouched at upstream's `c78c1f7`.

Account is exactly as found: **8 boards, 27 pins**. Every test artifact created
during probing was deleted and verified gone.

## Do this first in a new session

```bash
cd C:\Users\scott\Results-Oriented\pinterest-mcp
.venv\Scripts\python.exe -m pytest -q          # 14 tests, should pass
.venv\Scripts\python.exe -c "import asyncio; from pinterest_mcp import server; print(len(asyncio.run(server.list_tools())))"   # 23
```

Do **not** use the system Python. It is 3.10 and the project needs 3.11+. The
venv is uv-managed CPython 3.12.12.

## The one thing to understand about this API

The dividing line under Trial access is **boards versus pins**, not
sandbox versus production.

- Board operations all work, and they are genuinely public
- Pin **reads** and all **analytics** work, on real production data
- Pin **writes** are gated behind Standard access and fail loudly

Full evidence with raw responses: [docs/trial-access-findings.md](docs/trial-access-findings.md).
Per-tool status table: [README.md](README.md).

Two hosts exist and you need both. Upstream's claim that Pinterest has no
sandbox is wrong. `https://api-sandbox.pinterest.com` does pin CRUD but has
analytics and search disabled; production does analytics but blocks pin writes.

## Credentials

- `.env` in the repo root holds client ID and secret. **Gitignored. Never commit it.**
- Token lives at `C:\Users\scott\.config\pinterest\token.json`, outside the repo.
- Access token expires **2026-10-07** (30 days from 2026-09-07).
- Refresh window closes **2026-11-06** (60 days). The client warns under 14 days.
  If that window closes, the only recovery is a full browser re-auth.
- Scan every staged diff for secrets before committing. Every commit so far was
  scanned.

## Open items, in priority order

1. **Push the branch and open a PR.** Not done, deliberately, since nothing was
   pushed without asking.

2. **`delete_pin` is untested and cannot be safely tested in production.** Trial
   blocks pin creation, so no throwaway pin can be made, and the only production
   test destroys a real pin. An attempt was blocked by the safety classifier.
   Test it against the sandbox instead.

3. **Sandbox path is built but never exercised.** `PinterestClient(sandbox=True)`
   and `PINTEREST_SANDBOX_TOKEN` are wired and unit-safe, but no sandbox token
   was ever issued, so **no sandbox call has ever succeeded**. To finish it:
   Scott generates a token in the Pinterest app Configure tab with environment
   set to Sandbox, puts it in `.env`, then run pin CRUD against it. Sandbox
   tokens last 24 hours. Mark it Verified only after a real call.

4. **`update_board` with `privacy="SECRET"` returns 403.** Cause is most likely
   the missing `boards:write_secret` scope, not a Trial limit. **Unproven.**
   Confirming costs a full re-auth, and public boards are what this account
   wants anyway, so this was left alone.

5. **Standard access application.** Pin writes are the payoff. Pinterest wants a
   screen recording of an agent driving a real OAuth flow, and terminal
   recordings are explicitly accepted, so recording a `pinterest-mcp-auth` run
   plus a few tool calls is enough.

## Business findings, outside the original brief

Found while probing. Not acted on.

- **24 of 27 pins link to `theniftyapp.com`, only 3 to `thehandypages.com`.**
  The old domain 301s to the new one, and all 27 links were checked and return
  200, so nothing is broken. It is an extra redirect hop and split attribution.
  Fixing it needs `update_pin`, which Trial blocks.
- **All 27 pins have `alt_text: null`; 4 have an empty `title`.** Free SEO
  ground on a search engine. Also needs `update_pin`.
- **`SAVE` is 0 across 30 days against 516 impressions.** Impressions without
  saves points at creative or keyword fit, not reach.
- `get_top_pins_analytics` is the highest-value call available today. It names
  which specific pins earn impressions. Top pin had 129 impressions of 516.

## Conventions used here

- No em dashes in anything written for Scott.
- Never mark a tool Verified without a real call. Untested means untested.
- Every tool docstring and MCP description states its Trial behaviour, so an
  agent is not misled by a gated call.
- Probe scripts print raw responses. No inference presented as result.

## Files worth knowing

| Path | What |
|------|------|
| `src/pinterest_mcp/config.py` | New. Single source for token path, scopes, both hosts |
| `src/pinterest_mcp/client.py` | 23 tools' worth of methods, `PinterestAPIError`, sandbox mode |
| `src/pinterest_mcp/auth.py` | Rewritten on stdlib `http.server`, validates OAuth state |
| `scripts/probe_trial_access.py` | Staged access probes, raw output only |
| `docs/trial-access-findings.md` | Raw evidence for every claim in the README |
| `tests/test_token_storage.py` | New. Token path, refresh window, warning thresholds |
