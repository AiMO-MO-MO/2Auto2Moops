# STATUS — current state & next steps

> Session log. Updated as the project moves; read at session pickup. Stable rules live in
> CLAUDE.md / docs/domain.md / docs/lessons.md — never here.

## Session pickup

> `system <id>` is the live idempotent run. **2026-06-09: the EXISTING-customer flow was proven
> end-to-end on SO-19946** (Wash'n Up 00720, second location): gap-fill on the cust page (Stripe
> feature), new LP location create (with operator rename picked up by re-run), Stripe merchant +
> bank access, End Customer link (both halves), VAC configs downloaded+uploaded, tasks derived.
> Remaining on 19946: tasks 4/5 (card approval look-back → re-run `s 19946`), task 6 (SF).
> 1. **Restart the console first** (`quit` → `python run.py`) — it doesn't hot-reload; stale code
>    caused two phantom failures this session.
> 2. `system <id>` does the whole thing. NO prompts in the chain anymore (lesson 36): fill-only is
>    the gate. Location no-match → fills a NEW location and pauses for the save.
> 3. Read `docs/lessons.md` lessons 31-39 before touching chain/save/LP code — all from live runs
>    on 2026-06-09 (save-blocker MOOPS bug, navigating-reads-before-fills, LP index trust rules,
>    re-read-after-pause, CC rules, SOR shipping fallback).
> 4. `navigate_to_so` now skips when already on the SO; `run.log` starts with the command + timestamp.

Full historical log (#1-24, mostly FIXED) lives in `docs/reference.md`.

## Untested-but-built (verify on next live runs)

- Card design email **keeps CC** now (clearing = PO email only) — verify on next new-design card.
- **Location ID re-read after the save pause** (lesson 35) — built after the 19946 rename incident;
  not yet exercised live.
- **End Customer half-link completion** (cust linked, location missing → finishes the link).
- **SOR shipping fallback** (lesson 39) — fixed after SO-19943 went Ground instead of Next Day;
  verify on the next urgent parts order. 19943 itself was corrected by hand.
- `shipping <id>` spot-check verb (parked post-ship work, docs/post_ship.md).

## Built & working

first-touch (system + route), parts-order, cards-order, card-modify, final-touch (portal checks
untested; RETIRED), EFS kit expansion, persistent console (bare `python run.py` → `2auto>`,
browser stays open).

**Branches:** `main` = older version; `optimize-system-rerun` = current work (order_plan.py pure
planner, task workflows, tests). Known trouble: locations / cust id bouncing in the chain.

## Intake — BUILT (read-only)

`python run.py intake`. Scrapes **Submitted/In Review** only, **System-only filter** (one-line
revert in `intake.run`), per-SOR detail, batch-FIFO **scheduling** (VAC weight from the SOR), and
**Admin dedup** with a sleek board badge. Honors the SOR's **Existing End Customer** field as the
authoritative verdict. Writes `intake_board.html` + `intake_plan.json`. `inspect <sor>` dumps a SOR
page. NOT yet: rules engine, query-tool address signal, SF dedup, the resolution-gate → execution
handoff (plan isn't consumed by the run yet).

## Dedup — Admin Stage 1 BUILT

`core/dedup.py` `match_customer(order, customers)` — source-agnostic, priority **email > phone >
last-name > laundromat-name** (NOTE: address — the query-tool signal — and SF are NOT built in the
Python tool). `portal.scrape_admin_customers` (cached per session) feeds it. Verdict: existing /
potential / new; junk rows (Delete/TBD/temp@) excluded. `dedup "<email|phone|name>"` tests it standalone.

## SF dedup — BUILT via the live SF MCP connector

(Supersedes the old "SF browser-only / no REST API" note.) The Salesforce connector is read-queryable
against `trycentssf` (SOQL + SOSL) — use it, not Playwright, for SF dedup reads. Matches across
**Account, Contact, Lead, Custom_Location__c** on CUSTID (`Account.LW_account_ID__c`,
`Lead.Moops_Customer_id__c`, `Custom_Location__c.LW_Location_ID__c`), email, phone, name, last name,
and address. CUSTID format varies (MOOPS pads `00378`; some SF rows unpadded `1595`). Cross-system
tells: a matched SF Account with `LW_account_ID__c` null = in SF, not provisioned in LW.
**Combinability red flag:** a matched location's `Custom_Location__c.Cents_Identifier__c` (Cents
Location ID) being populated = live Cents POS identity → hard to combine, escalate for manual
reconciliation (never auto-create/link); blank = easy/convertible.

## moops-dedupe skill — BUILT

`skills/moops-dedupe/` — live, read-only dedup of Submitted/In-Review **System** SORs against BOTH
Admin AND Salesforce, all signals. Admin = full `/customers` scrape + `dedup.py` logic run via the
Chrome connector's `javascript_tool` (`references/admin_dedupe.js`) — the only way to match Admin on
email/phone (the `/customers` filter is name-only; the Query Tool is address/CUSTID-only). SF = the
connector. Reads SORs live via Chrome; renders `dedupe_board.html` from a `dedupe_results.json`
(`render_board.py`). Query BOTH systems LIVE every run — never reuse cached `intake_plan.json`
matches. Self-contained, installable bundle `moops-dedupe.skill`; updating the source does NOT
update the installed copy — re-import via Settings → Capabilities. See `references/sf_queries.md`.

## `system <id>` — the main idempotent, task-driven run (BUILT, in progress)

`system <id>` (and `s <id>`; `sx` is an alias) runs first-touch + the provisioning chain as ONE run,
all FILL-ONLY (human Saves; never submits). Built so far:
- **Check-or-skip tag + schedule** (first_touch Steps 4/5): never overwrites; sets only if empty. If the
  assembly week is already set, Step 3 doesn't even re-read capacity.
- **Customer resolution** (Step 8): reads the SO's **End Customer field** (`read_so_end_customer`, the
  authoritative signal) AND the notes; if a real cust id is linked → reuse, don't create. Replacement/
  exchange → inherit the referenced SO's customer (verify-mode, provision nothing). **Strong-dedupe
  guard**: if no End Customer is set but an exact email/phone match exists, STOP — don't create a
  duplicate; tell Matt to set the End Customer and re-run. Blank-name create is blocked.
- **Task-driven chain** (`_do_provision_chain`): reads the checklist, runs **only To Do** steps, skips
  Completed (7=Stripe/payment, 8=location, 10=user+intro, 3/4/5=card). NEVER blanket-resets the
  checklist (would un-complete 7/8/10); existing/replacement only mark the card task they did.
- **Order:** customer → API user + Stripe feature → location → **user → Stripe** (user before Stripe: the
  user is assigned bank/account access) → cards → **End Customer on SO** → **config (task 9)** → tasks.
  Config MUST come after End Customer: MOOPS uses the linked customer+location to populate CustomerKey
  and LocationID in the .cfg. If End Customer isn't set, config is skipped with a [FLAG] and task 9
  stays To Do for the re-run.
- **User step** (`not existing`): skip for existing customers (they already have LP users). For a
  never-provisioned existing Admin customer, run `adduser <so> <cust>` manually.
- **Strong-dedupe single match**: now confirms+proceeds automatically (press Enter) instead of stopping.
  Multiple strong matches still stop (ambiguous). Eliminates the two-run dance for the common case.
- **Read once:** first-touch's SOR read is threaded into the chain + merged with SO notes at chain start.
  Location/User steps never re-navigate to the SO. One-pass: tasks + VAC seats + notes all read together.
- **Resilience:** `navigate_to_so` retries 3× on transient MOOPS failures (timeouts,
  ERR_HTTP_RESPONSE_CODE_FAILURE); read-only customer check is non-fatal.
- LP writes **guarded** by `current_portal_customer`; `fill_location`/`open_stripe` re-establish scope via
  the admin→LP bridge if the session is on the wrong customer (e.g. left scoped by a dedupe lookup).

`provision <so> <cust>` re-runs the chain; `custid <so> [cust]` runs just the Admin cust-id setup;
`card <so> <cust>` runs ONLY the card step (safe on a system order — no tag/order-type/shipment changes).

**Schedule fix (June 2026):** EXPEDITED past-target now picks earliest FUTURE week instead of returning
None. Filters `available` to future-only before taking min.

## Idempotent-run build order (`docs/idempotent_run.md`)

✓1 tag+schedule check-or-skip, ✓ End-Customer-field detection + strong-dedupe confirm+proceed
(single match), ✓ task-driven chain (run To Do only), ✓ SOR read-once + notes merged, ✓ nav retry,
✓ config after End Customer, ✓ card type classifier, ✓ cards-order delegates to `_do_cards`.
**Next:** (3) dealer-record link (new customers must be added to dealer account before End Customer
is settable — need `inspect-form` on Admin customer page to see sub-customer add UI); (4) location/
Stripe/user skip-if-exists detection; (5) task-state derivation; (6) finish retiring final-touch.

## Existing-customer chain — BUILT & PROVEN (2026-06-09, SO-19946)

The "exists in Admin ≠ provisioned" path now works: cust-page check FILLS gaps (API user/Stripe
feature, fill-only + save pause) instead of punting; location index read is hardened (lesson 34 —
never trusts a silent 0); no-match → fills a NEW location directly (Access Sharing No→02 series
suggested; **Matt overrode to 01-series 0100002 on 19946 — the Yes→01/No→02 rule may be wrong or
nuanced, OPEN QUESTION**); Stripe step initiates merchant + assigns bank access; End Customer links
cust+location and config follows. Save blocker (SOR-pulled End Customer, lesson 32) cleared
automatically; saves verified (lesson 31).

## Open / next

- **LP bridge once per chain** — each LP step still re-bridges Admin→LP (the "cust id bounce").
  Works, wasteful. Bridge once, then assert scope via `current_portal_customer`.
- **Big customers (50+ locations)** — does the LP Locations page paginate/lazy-load? If yes, the
  index read sees a partial list (false no-match risk). Check a big cust id before one hits intake.
- **Access Sharing → location series rule** — see open question above; get the real rule from Matt.

- **Dealer-record link** — new customers need to be added to dealer's Admin account before End Customer
  is settable. Need `inspect-form https://admintools.mitechisys.com/customers/{dealer_id}` to see
  sub-customer add UI. Existing customers already linked; new customers are not.
- **Task 6 (SaaS contract) = Salesforce.** Not in the Admin/LP flow; the run leaves it To Do.
  Plan: a Claude-side SF skill (connector writes via field spec in `docs/salesforce.md`) run after
  the MOOPS chain, joined on LW cust id + SO#.
- **End-Customer search-select DOM** — `set_so_end_customer` types cust id + location, clicks matching row.
- **Batch mode (designed, not built)** — loop `system <id>` with pauses converted to flags + one
  review board; approvals detected from system state on pass 2 (see chat 2026-06-09).
- Still queued: >5000 card-shipping line, rules engine, parts auto-pricing, first-touch money-guard.

## Post-ship lifecycle — PARKED (see `docs/post_ship.md`)

**Decision 2026-06-09: finish the main workflows first.** Full design documented in
`docs/post_ship.md` — key call: batch reads (shipping report, watchtower) come from **Snowflake**
(`CENTS_LW.CENTS_LW_MOOPS`), not Playwright; Playwright = writes + live mid-chain reads only.
Snowflake replication of the orders table still needs verifying (queries in the doc; Looker
connector auth was expired). Built & parked: `core/shipping.py` pure helpers,
`read_shipment_info`, `shipping <id>` spot-check verb (untested).

**Portability rule: Claude builds the system; the system doesn't run on Claude.** Recurring process
logic = Python in this repo. MCP skills only for ad-hoc human judgment (dedupe). SF task 6 → SF
REST API (`simple_salesforce`) per `docs/salesforce.md`, NOT a connector skill. Intercom card-art
flag → Intercom REST API ("Card Approved: CARD-MD-X" from graphics@, SO# in body, signed attachment
URLs expire — fetch at detection). Events flag, humans fire — no event ever writes.
