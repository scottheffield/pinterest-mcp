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

## Still untested

- Pin writes (`POST /pins`). Not attempted. Pinterest's sandbox warning is
  specifically about created content, and the board result does not
  generalise to pins without a test.
- Pin analytics (`GET /pins/{id}/analytics`).
- `GET /user_account/analytics/top_pins`.
