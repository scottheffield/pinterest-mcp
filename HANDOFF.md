# Handoff notes

State as of 2026-09-07. Read this before touching the repo.

## Where things stand

Branch `phase-0-trial-fixes`, working tree clean, **nothing pushed**.
Phases 0, 1 and 2 are complete, plus a follow-up pass that closed the review
defects listed under "What changed in the follow-up pass" below. `main` is
untouched at upstream's `c78c1f7`.

Account is exactly as found: **8 boards, 27 pins**. Every test artifact created
during probing was deleted and verified gone.

## Do this first in a new session

```bash
cd C:\Users\scott\Results-Oriented\pinterest-mcp
uv run pytest -q            # 77 tests, should pass
uv run ruff check src tests scripts
uv run python -c "import asyncio; from pinterest_mcp import server; print(len(asyncio.run(server.list_tools())))"   # 23
```

`tests/test_tool_registry.py` is the one that matters when adding a tool. It
fails if a tool is advertised without a dispatch branch, dispatched without
being advertised, advertised without a client method, or described without a
Trial-access status.

Do **not** use the system Python. It is 3.10 and the project needs 3.11+. The
venv is uv-managed CPython 3.12.12.

## Traps that have already cost a session

1. **Check `git log` before acting on a session brief.** A brief describing
   phases 1 and 2 as not started arrived after both were committed. Read the
   log and the code first, then work out what is genuinely open. The brief
   describes intent, the repo describes state.

2. **Line endings are mixed and there is no `.gitattributes`.**
   `src/pinterest_mcp/*.py` and `docs/trial-access-findings.md` are **CRLF** in
   git. `tests/*.py`, `README.md` and `HANDOFF.md` are **LF**. `core.autocrlf`
   is `false`. Editing a CRLF file with a script that writes `newline="\n"`
   rewrites every line and turns a 60-line change into a 1500-line diff. If a
   diffstat looks absurd, this is why. Check with
   `git show HEAD:<file> | file -` against `file <file>` before committing.

3. **Never let a smoke test write real pin content.** A draft of the stdio
   smoke test called `update_pin` with a new title on a real pin id. The Trial
   gate would have blocked it, but the test must not depend on that. Use a pin
   id that does not exist, such as `0`, so nothing real can change whatever the
   API decides to do.

4. **Reads are free enough to verify with, writes are not.** The Trial ceiling
   is 1000 reads and 300 writes per day. Re-running the read probes to confirm
   a claim costs nothing that matters. Prefer that over trusting a status
   written down by an earlier session.

## The one thing to understand about this API

The dividing line under Trial access is **boards versus pins**, not
sandbox versus production.

- Board operations all work, and they are genuinely public
- Pin **reads** and all **analytics** work, on real production data
- Pin **writes** are gated behind Standard access and fail loudly

Full evidence with raw responses: [docs/trial-access-findings.md](docs/trial-access-findings.md).
Per-tool status table: [README.md](README.md).

Two hosts exist. Upstream's claim that Pinterest has no sandbox is wrong:
Pinterest's own 403 on a production pin create names
`https://api-sandbox.pinterest.com`. Production does analytics and search but
blocks pin writes. The sandbox is expected to be the mirror image, pin CRUD
but no analytics or search, and that expectation is read from endpoint
metadata, NOT from a call. No sandbox call has ever succeeded here. See open
item 3.

## Credentials

- `.env` in the repo root holds client ID and secret. **Gitignored. Never commit it.**
- Token lives at `C:\Users\scott\.config\pinterest\token.json`, outside the repo.
- Access token expires **2026-10-07** (30 days from 2026-09-07).
- Refresh window closes **2026-11-06** (60 days). The client warns under 14 days.
  If that window closes, the only recovery is a full browser re-auth.
- Scan every staged diff for secrets before committing. Every commit so far was
  scanned.

## What changed in the follow-up pass

All verified: 77 tests pass, `ruff check` and `ruff format --check` are clean,
and the server was driven end to end over real MCP stdio through the console
script the README config points at.

1. **`dry_run_pin` is gone.** It had a `call_tool` branch and no `list_tools`
   entry, so no client could ever reach it. Deleted rather than advertised,
   because `create_pin` already takes a `dry_run` flag. `tests/test_tool_registry.py`
   now fails if the two sets ever diverge again, in either direction.
2. **Pin gates return an explanation, not a raw HTTP error.** `update_pin` and
   `create_pin` translate the `pin_edit` 401 and the production-create 403 into
   a `TrialAccessError` naming Standard access and stating that nothing was
   changed. Status, path and body are preserved on the exception. Unrelated
   errors, a 404 for instance, pass through untouched.
3. **Eleven inherited tool descriptions carried no Trial status**, and two were
   false: `search_pins` claimed to search public Pinterest, and `create_pin`
   repeated upstream's "Pinterest has no sandbox" claim. All 23 now state their
   verified status, enforced by a test.
4. **`docs/trial-access-findings.md` was stale.** It still listed pin creation
   and both analytics endpoints as Untested, while the README described them as
   settled. Added probe e (the production pin-create 403 and the two-host
   split) and probe f (analytics re-verified live today).
5. **The README overstated the sandbox.** Its two-host table marked sandbox pin
   CRUD as working. No sandbox call has ever succeeded. That column is now
   marked expected rather than verified, with the caveat spelled out.

One new observation from the stdio smoke test: `PATCH /pins/0`, a pin id that
does not exist, still returns the `pin_edit` 401 rather than a 404. The Trial
gate is evaluated before the resource lookup.

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
| `tests/test_phase1_tools.py` | New. Phase 1 tools plus the Trial-gate translation, with verbatim live error bodies |
| `tests/test_tool_registry.py` | New. Guards client method / list_tools / call_tool parity and Trial-status coverage |
