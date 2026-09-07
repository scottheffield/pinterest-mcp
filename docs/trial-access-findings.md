# Trial access: what actually works

Probed against the live TheHandyPages account on 2026-09-07 with an app
approved for **Trial** access, not Standard. Every row below reflects a real
call. Nothing here is inferred from documentation.

Account at time of probe: `TheHandyPages`, BUSINESS, 8 boards, 27 pins,
467 monthly views.

## Headline

Pinterest's Trial documentation says objects created under Trial are "only
visible to their creator as Sandbox entities." **For boards, that did not
happen.** A board created through the API appeared on the public profile in
an unauthenticated fetch, in the same server-rendered board list as the real
boards.

Reads are not sandboxed either. The API returned real production boards,
pins, and analytics.

## Probe a: reads

| Call | Result | Evidence |
|---|---|---|
| `GET /user_account` | Real data | `"username":"TheHandyPages"`, `"monthly_views":467`, `"board_count":8`, `"pin_count":27` |
| `GET /boards` | Real data | 8 production boards including "Free Printable Calendars", "Chore Charts and Kids Routines" |
| `GET /pins` | Real data | 27 production pins with live `thehandypages.com` links |
| `GET /boards/{id}/pins` | Real data | returned pins for the first board |

## Probe b: board write and public visibility

1. `POST /boards` created `zz-trial-probe-6563`, id `1091067515916302315`,
   returned `"privacy": "PUBLIC"`.
2. Unauthenticated `curl` of `https://www.pinterest.com/TheHandyPages/`
   (no cookies, no token, HTTP 200) contained:

   ```json
   "type":"board","collaborator_count":0,"name":"zz-trial-probe-6563","pin_count":0
   ```

   in the same embedded profile payload as the real boards, on a profile with
   `"is_private_profile":false`.
3. `DELETE /boards/{id}` returned 204 with an empty body.
4. `GET /boards/{id}` afterwards returned `{"code":40,"message":"Board not found."}`.
5. Re-fetching the public profile unauthenticated: the probe board was gone,
   the real boards remained.

**Conclusion: create_board and delete_board work on Trial, and the created
board is genuinely public.** This confirms the what-name/pinterest-mcp
trial table for board writes and goes further, since that table did not
establish public visibility.

## Probe c: account analytics

`GET /user_account/analytics` over a 30-day window returned real metrics with
`"data_status": "READY"`:

```json
{"ENGAGEMENT": 21, "IMPRESSION": 516, "PIN_CLICK": 18, "SAVE": 0, "OUTBOUND_CLICK": 5}
```

30 daily rows, 16 of them non-zero. Account analytics is **not** inert under
Trial.

## Extra reads probed

| Call | Scope | Result |
|---|---|---|
| `GET /search/pins` | `pins:read_secret` + `boards:read_secret` | Works. Searches the account's OWN pins, not public Pinterest |
| `GET /terms/suggested` | `ads:read` | HTTP 200 but thin: returns only the input term for "printable calendar", "calendar", "chore chart", "graph paper" |
| `GET /terms/related` | `ads:read` | HTTP 200, `related_term_count: 0` for "printable calendar" |
| `GET /trends/keywords/US/top/growing` | `user_accounts:read` | Works, returns real trend data with weekly time series |

`ads:read` was granted to the Trial app, so the keyword endpoints are
reachable. They return sparse data for this niche rather than failing.

## Probe d: are UPDATES available during Trial?

Probed 2026-09-07 after the create/delete probes. **Board updates work. Pin
updates are hard-blocked.**

### Board update: works

On a throwaway board, `PATCH /boards/{id}`:

| Field written | Result |
|---|---|
| `name` | OK, persisted |
| `description` | OK, persisted |
| `privacy` = `PUBLIC` | OK |
| `privacy` = `SECRET` | **403** `{"code":29,"message":"You are not permitted to access that resource."}` |

The rename reached the public profile. An unauthenticated fetch of
`pinterest.com/TheHandyPages/` showed the new name and no trace of the old
one, so board edits propagate publicly and are not sandboxed.

Isolation of the 403: writing `privacy=PUBLIC` succeeded and a `name`-only
write succeeded immediately after the failure, so the privacy field is
writable and only the `SECRET` value is refused. The most likely cause is the
missing `boards:write_secret` scope rather than a Trial restriction, but that
is **unproven**; confirming it needs a re-auth with that scope added.

**Practical effect: none for this account.** Boards promoting a public site
should stay `PUBLIC`. Renaming boards and rewriting keyword-led descriptions,
the immediate need, both work today.

### Pin update: blocked, with an explicit error

`PATCH /pins/{id}` was tested as a strict no-op, writing a pin's existing
description back to itself, so no production content could change:

```json
HTTP 401 {"code":3,"message":"Your application does not have access to this restricted feature: pin_edit"}
```

A re-read confirmed the pin's title and description were untouched.

This matters beyond the one endpoint. The scope granted includes `pins:write`,
so this is **not** a scope problem: it is a Trial-versus-Standard feature gate,
named explicitly as `pin_edit`. Pinterest returned a hard, named error rather
than silently accepting the write.

That partially reframes the documented "Sandbox entities" risk. For pin
editing, at least, the failure is loud, not silent. Whether `POST /pins`
behaves the same way or silently sandboxes is **Untested**, and testing it
means creating a pin.

## Probe e: does POST /pins work?

Run after probe d. **It is blocked in production, and it fails loudly.**

```json
HTTP 403 {"code":29,"message":"Apps with Trial access may not create Pins in production
https://api.pinterest.com - use API Sandbox https://api-sandbox.pinterest.com instead."}
```

This answers the open question left at the end of probe d. Pin creation does
not silently sandbox. It is refused with a named reason, the same way
`pin_edit` was, and no pin is created.

It also disproves a claim carried over from upstream's `create_pin` docstring,
that Pinterest has no sandbox environment. Pinterest's own error names the
sandbox host, `https://api-sandbox.pinterest.com`.

The two hosts are complementary, not interchangeable:

| | Production | Sandbox |
|---|---|---|
| Reads | Real data | Isolated test data |
| Board CRUD | Works | Works |
| Pin CRUD | **Blocked under Trial** | Accepts writes |
| Analytics | **Works** | `x-sandbox: disabled` |
| Search | Works | `x-sandbox: disabled` |
| Auth | OAuth token, 30 days | Separate token, 24 hours |

The sandbox rejects production OAuth tokens outright (`401 code 2`), so it
needs its own token issued from the app's Configure tab with the environment
set to Sandbox.

**Caveat on the sandbox column.** The 403 above and the `401 code 2` token
rejection are real responses. The rest of the sandbox column is read from
Pinterest's endpoint metadata, not from a successful sandbox call. **No
sandbox call has ever succeeded against this app**, because no sandbox token
was ever issued. `PinterestClient(sandbox=True)` is wired and unit-safe but
**Untested end to end**.

## Probe f: analytics re-verified

Re-run 2026-09-07 as read-only calls over a 30-day window, 2026-08-08 to
2026-09-07, to close the two rows probe c left Untested.

`GET /user_account/analytics/top_pins`, sort_by `IMPRESSION`. **Works**, and
returns real per-pin numbers:

```json
{"sort_by":"IMPRESSION","pins":[
  {"pin_id":"1091067447264423486","metrics":{"IMPRESSION":129},"data_status":{"IMPRESSION":"READY"}},
  {"pin_id":"1091067447264416042","metrics":{"IMPRESSION":51},"data_status":{"IMPRESSION":"READY"}}]}
```

This was expected to be inert under Trial. It is not. It is the most useful
analytics call available today, because it names which specific pins earn
impressions rather than only totalling them.

`GET /pins/{id}/analytics`. **Works**, returns 30 daily rows plus summary
metrics, every row `"data_status": "READY"`:

```json
{"all":{"summary_metrics":{"SAVE":0,"OUTBOUND_CLICK":0,"PIN_CLICK":0,"IMPRESSION":0},
"daily_metrics":[{"date":"2026-08-08","data_status":"READY",
"metrics":{"SAVE":0,"OUTBOUND_CLICK":0,"PIN_CLICK":0,"IMPRESSION":0}}, ...]}}
```

The zeros are a property of the pin sampled, which was created on 2026-09-07
and so has no history in the window. The call itself succeeded and the data
is `READY`, which is what was being tested. `top_pins` above shows non-zero
impressions on older pins over the same window.

Account state confirmed unchanged during this probe: `board_count: 8`,
`pin_count: 27`.

## Revised summary

| Operation | Trial status | Evidence |
|---|---|---|
| Read account, boards, pins | Works, real production data | probe a |
| Account analytics | Works, real metrics | probe c |
| Create board | Works, publicly visible | probe b |
| Delete board | Works | probe b |
| Update board name | Works, propagates publicly | probe d |
| Update board description | Works | probe d |
| Set board privacy PUBLIC | Works | probe d |
| Set board privacy SECRET | Blocked, 403 | probe d, cause unproven |
| Update pin | **Blocked**, restricted feature `pin_edit` | probe d |
| Create pin (production) | **Blocked**, 403 code 29, names the sandbox host | probe e |
| Create pin (sandbox host) | Untested, no sandbox token ever issued | probe e |
| Delete pin | Untested | destructive, and Trial blocks making a throwaway pin |
| Pin analytics | Works, real daily metrics, `data_status: READY` | probe f |
| Top-pin analytics | Works, real per-pin impressions | probe f |

After every probe the account was restored to its original state:
8 boards, 27 pins.

## Still untested

- **The sandbox host.** `PinterestClient(sandbox=True)` and
  `PINTEREST_SANDBOX_TOKEN` are wired, but no sandbox token was ever issued,
  so no sandbox call has ever succeeded. Everything in the sandbox column of
  probe e except the two quoted error responses is read from endpoint
  metadata, not from a call. Mark it Verified only after a real call.
- **`DELETE /pins/{id}`.** Cannot be safely tested in production: the only
  test destroys a real pin, and Trial blocks pin creation so no throwaway pin
  can be made first. Test it against the sandbox once that host works.
- **The cause of the `privacy=SECRET` 403.** Most likely the missing
  `boards:write_secret` scope rather than a Trial limit. Confirming it costs a
  full browser re-auth, and this account wants public boards anyway.
