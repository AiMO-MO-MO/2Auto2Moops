# 2AUTO2MOOPS

> MANDATORY FIRST STEP: Read `OPERATOR_RUNBOOK.md` before responding to ANY message about orders.
> It explains that Claude gives commands, Matt runs them locally. Claude NEVER runs Python or opens browsers.
> This file is the domain cheat sheet. Deep-dive in `docs/reference.md`.

## Your Role

Senior database engineer and automation architect. Production-critical system — real orders, real money, real shipping. Build efficiently, think in systems, guard the money. Playwright is the only dependency. Keep business logic separable from page interaction (NetSuite migration coming).

**Operational mode:** Matt says an SO ID → you give the exact `python run.py` command → he pastes output → you interpret and give the next step. That's it. Don't run code, don't open browsers, don't ask unnecessary questions.

**"run log" = READ `run.log`.** When Matt says "run log" (or "check the log"), he means: open `run.log` in this folder and respond based on what's ACTUALLY in it — that file is the bridge to his terminal (he tees output to it). Do NOT interpret "run log" as "log this to memory," and NEVER infer a run's result without reading the file. Each chat is a fresh context and does not auto-watch the file; read it on demand every time he references it.

**Work pace:** Incremental and focused — one thing at a time. Prefer the smallest correct change and REUSE existing code (one shared path, three flavors) rather than bolting on a parallel branch for one case. **Read the real code, and inspect the live page (DOM), before changing it — never guess at selectors or behavior.** Don't ship a change "to see if it works," and never report success you haven't verified. Confirm before large refactors or new features; don't build multiple features unprompted. When starting a new session, ask what Matt wants to work on.

## Architecture

Human-guided automation for Laundroworks order fulfillment in MOOPS.

| Layer | What |
|-------|------|
| Human Review | Reviews SOR, approves playbook |
| Execution | Python + Playwright runs playbooks against SO IDs |
| Data | Playwright for **reads and writes** off Matt's logged-in session (Snowflake/Looker is analytics only — NOT used by the tool) |

**Lifecycle (see `docs/idempotent_run.md` for the full design):**
Intake (read-only, batch, system-only) returns **all possible dedupe matches** per SOR on the HTML
board → Matt **investigates** and **creates the SO** (a System SO existing IS the go signal; intake and
the run stay decoupled, the new/existing call rides on the SO's End Customer field) → **`system <id>`**
runs the idempotent, **task-driven** workflow (does every task the dependencies allow) → **look-back**:
re-run `system <id>` once the card design is approved; it finishes the rest **without overriding what's
done**. Only a pending **new card design** forces a second pass (it blocks card tasks + the config file);
card-in-hand / reprint / no-cards all finish in one touch.

## Key Systems

| System | URL | Purpose |
|--------|-----|---------|
| MOOPS | moops.mitechisys.com | Sales orders, parts, cards, POs |
| Admin Portal | admintools.mitechisys.com | Customer accounts, API users |
| LaundroPortal | portal.mitechisys.com | Locations, users, billing |
| Snowflake/Looker | — | MOOPS database for analytics |

## Order Types & Playbooks

| Type | Command | Tag Pattern | Assembly Week |
|------|---------|------------|--------------|
| System (Laundromat) | `system <id>` | "2 VAC07 (Store Name)" | Yes |
| Route (Multi-Housing) | `system <id>` (auto-detect) | "1 VAC03 (Store)" | Yes |
| Parts/Readers | `parts <id>` | Descriptive | No |
| Cards Only | `cards <id>` | "5000 Cards (Customer)" | No |
| Card Modify | `m <id>` | (no tag change) | No |

**How to classify:** VACs present → System/Route. KITs/ASSYs only → Parts. CARD-MD-* only → Cards. Sale/Route field distinguishes System vs Route.

### Parts fulfillment paths
1. **EFS (3PL)** — product in `EFS_PRODUCTS` set (see `core/efs.py`). Shipment By → "3PL - EFS", JS clipboard auto-fill.
2. **VUnics** — ships from warehouse. Shipment By → VUnics.
3. **Slack → SF** — not in MOOPS inventory (Penny Devices 76-*, POS Bundles). Post to #ops-moops-orders.
4. **35+ reader kits** at VUnics → upgrade to System, add to assembly schedule.

### EFS kit expansion
Kits not stocked as whole units at EFS but whose components ARE stocked get expanded into individual parts. The `-DS` suffix means shipped from EFS, `-MA` means shipped from Shrewsbury/VUnics. `KIT_EFS_COMPONENTS` in `core/efs.py` maps expandable kits to their components.

| Kit | Components (at EFS) | Status |
|-----|---------------------|--------|
| KIT-A35 | 03-01-95 (A35 pinpad) + 01-02-23 (A35 holder) | Expandable ✓ |
| KIT-P630 | 03-01-99 (P630 terminal) + 01-02-25 (P630 holder) | NOT at EFS — ships VUnics |
| KIT-A35-ATTACHMENT | Uses 01-02-24 | NOT at EFS |
| KIT-P630_ATTACHMENT | Uses 01-02-27 | NOT at EFS |

Whole kits already in EFS (KIT-DEXTER01, KIT-POS-01, etc.) ship as-is, no expansion needed.

**EFS JS snippet:** When Matt can't re-run the playbook (e.g. other fields already set on the SO), generate the JS snippet directly in chat from the shipping data on the SO page. Matt pastes it into the EFS browser console (F12 → Console → Ctrl+V → Enter). Always include shipping address fields + product qty fills. The `read_shipping_to` output or Shipping To field on the SO has: company, address, city, state, zip, ATTN name, phone.

### Route differences from System
No CARD-03-01, no SVC-LAUNDROMAT, no customer/location/Stripe/user/config provisioning. Task checklist: 1-2 Completed, 6-10 N/A. A route CAN carry a card design (most don't) — if the SOR has one, `system <id>` runs the SAME `_do_cards` workflow (new design needs no cust id; clone defaults End-Customer to Mitech) and sets card tasks 3/4/5 from the card on the SO via the shared `action_set_system_tasks` detection. Tag = "QTY VACxx, N Readers (Store)". Auto-detected from the Sale/Route dropdown OR Order Type "System - Multi-Housing".

### Cards-order playbook (`--cards-order`)
1. Read SO + SOR (card design type, contact, card qty)
2. Set tag: "QTY Cards (Customer)" or "QTY Generic Cards (Customer)"
   - Customer name fallback: if `read_customer_name` empty, pulls from card description first line (e.g. "Laundry Depot II card" → "Laundry Depot II")
3. Set Order Type → Cards Only, Shipment → Drop shipment / Card Supplier
4. **Save before card workflow** (preserves tag/order-type/shipment before navigating away to clone)
5. Card workflow:
   - **New design**: clone → human saves → add to SO → save → design email
   - **Reprint**: pause → Create PO → PO email (clear CC) → human sends
   - **Generic**: no card workflow needed
6. Card workflow delegates to `_do_cards` (same code path as system run — no duplication)

### Card-modify playbook (`--card-modify`)
For when SOR says "reprint" but comments say modify (e.g. address change). Same as new-design card flow but **no tag/order-type/shipment changes**. Used on orders already first-touched.
1. Read SO (get customer name)
2. Read SOR (contact info)
3. Clone A-TEMP-CARD-MD with shortname from customer name (same as first-touch)
4. Add new card to SO (replaces existing CARD-MD-*)
5. Save
6. Card design email
- Can override shortname: `--card-modify CUSTOMNAME`
- SOR comments override design type — "CHANGE ADDRESS" on a reprint = modify

## VAC Part Number Decoder

Format: `VACXX-YZ-WM` — XX=cabinet, Y=bill acceptor, Z=pinpad, W=card dispenser, M=modem.

| Digit | Key values |
|-------|-----------|
| XX (cabinet) | 01=Mini, 02=Cashless, 03=Cash front, 04=Cash back, 07=Touch back, 08=Touch front |
| Z (pinpad) | 0=None, 1=IPP320, 2=PAX S300, 3=VX820, 4=Ingenico AU |
| W (dispenser) | 0=None, 1=110-card, 2=260-card |

**Rules:** Z≠0 → pinpad needed. W≠0 → CARD-03-01 (always qty=1). XX=07/08 → paper rolls (03-01-34). SVC-LAUNDROMAT for real customer orders.

**Schedule weights:** VAC01-06 = 0.5, VAC07-08 = 1.0 (touchscreen = full slot).

## Processor Type → Pinpad Kit

| SOR Value | Kit | Notes |
|-----------|-----|-------|
| Empty or "6" | KIT-P630 | Stripe (default, changed from S700 May 2026) |
| "2" or text contains FORTIS/EBT | KIT-A35 | Fortis (EBT) |

## Card Workflow

**States:** No cards → N/A. New design (PLACEHOLDER) → clone+email. Reprint → manual PO. Generic <1000 → goes with system. >5000 → add SHIPPING line.

**New design flow:** Clone A-TEMP-CARD-MD → CARD-MD-{SHORTNAME}, placeholder image, cost $0.175, Part Group=Card, Part Type=Virtual, Pricing=USER-CARDS. Add to SO matching source card qty/price (finds CARD-01-02 first, falls back to any existing CARD-MD-*). Send design email to graphics@mitechisys.com. **PO creation NEVER automated.**

**Card rename detection:** After clone pause, re-reads `input[name="part_number"]` (3s timeout). If human renamed during save, subsequent steps use the actual name.

**Shortname:** Auto-strips vowels, targets 6 chars. Falls back to 2-char-per-word then acronym if still too long.

**Card design types from SOR:** `_card_type(design)` in `run.py` — direct match, no substring guessing. `startswith("new")` → "new", `startswith("modify")` → "modify", `startswith("reprint")`/`"existing"` → "reprint", else "none". All four call sites use this one function.

## Task Checklist

### System Orders (First Touch)

| # | Task | Status |
|---|------|--------|
| 1 | Hardware verified | Completed |
| 2 | End-customer info obtained | Completed |
| 3 | Connected with end-customer/dealer | Completed (new card) or N/A |
| 4 | Card approval received | To Do (new card) or N/A |
| 5 | Card proofs, PO sent | To Do (any card) or N/A |
| 6 | Sent SaaS contract | To Do (Salesforce — not automated, Matt sends) |
| 7 | Sent Payment processing contract | To Do |
| 8 | End-customer and location added to Portal | To Do |
| 9 | VAC Config files attached to order | To Do |
| 10 | Created Admin Portal user and emailed Intro email | To Do |

### Route Orders

| # | Task | Status |
|---|------|--------|
| 1 | Hardware verified | Completed |
| 2 | End-customer info obtained | Completed |
| 3/4/5 | Card tasks | Per SOR card design (Completed/To Do if a card; else N/A) |
| 6-10 | Provisioning + config | N/A (routes aren't provisioned like a laundromat) |

### Final Touch (`--final-touch`) — RETIRED
Replaced by the idempotent `system <id>` re-run (the "look-back" pass), which is task-driven and does the
work itself. Do NOT run it — it opens the ITF Jira form and mis-marks card tasks. Historical detail in
`docs/reference.md`.

## Assembly Week Scheduling

- **45/week hard max**, **30/week soft cap** (30-40 = yellow, 40-45 = red/emergency only)
- **FIFO** — first available week under soft cap, not lead-time math
- Required date → work backwards (delivery minus ~2 weeks shipping)
- EXPEDITED orders can use the 35-45 range
- **Required Date on SO** = Friday of assembly week (SOP). No required date = Saturday.

## MOOPS Selectors (validated)

| Element | Selector |
|---------|----------|
| Tag | `input[name="description"]` |
| Product search | `#validity_product-search` |
| Add To Order | `text=Add To Order` |
| Existing parts | `tr[id^="existing_part_order_"]` |
| New parts | `tr[id^="new_part_order_"]` |
| Part number | `th[scope="row"] a` |
| Qty input | `input[type="number"]` (first in row) |
| Shipment Method | `select[name="delivery_method_id"]` |
| Shipment By | `select[name="part_inventory_location_address_id"]` |
| Work State | `select[name="work_state_id"]` |
| Order Type | `select[name="sales_type_id"]` |
| Task selects | `select[name="task_state"]` (10 total) |
| Internal Notes | `textarea[name="notes_to_admin"]` |
| SOR link | `a[href*="/order-requests/"]` |
| Save | `text=Save` |
| Missing parts | `text=Missing part associations detected` |

**SOR-only selectors:** EBT/Processor label → adjacent span. Required Delivery Date → `span.col-9` with "(Month DD, YYYY)". EXPEDITED → `span.bs-red`.

## Missing Parts Analysis (decision order)

1. Wire splicers (03-01-43) → always ADD to existing qty (they stack)
2. Already handled by rule-based logic → skip
3. "OLD VERSION" in description → skip
4. Blocker plates (01-05-56) from X-series/USX reader (`CR-*-126` family, e.g. CR-02-126, CR-10-126) → skip (built-in blockouts)
4b. Long power cable (`02-06-78*`) → skip (almost never needed; add manually if required)
5. VAC pedestal (01-03-03) → skip (only if customer ordered)
6. Everything else → add

## Code Structure

```
run.py                    ← CLI dispatcher + verb shorthand + chain orchestrators
                            (_do_create_customer/_do_addloc/_do_adduser/_do_cards/
                             _do_provision_chain/_do_custid/_do_dedup_test)
core/browser.py           ← Playwright launch + navigation
core/moops.py             ← All MOOPS page actions (~2,300 lines)
core/schedule.py          ← Capacity, FIFO picking, date parsing
core/efs.py               ← EFS catalog, JS snippet builder, kit expansion
core/portal.py            ← Admin/LaundroPortal verification (tasks 7,8,10), scrape_admin_customers
                            (cached), current_portal_customer (LP write guard)
core/dedup.py             ← Pure customer matcher (email>phone>lastname>name); source-agnostic
core/provisioning.py      ← Eliminate-ITF fills: create-customer, fill_api_user (finalize),
                            fill_location, fill_user, open_stripe, send_intro_email, inspect_form
playbooks/first_touch.py  ← System/Route playbook (10 steps; no_itf + dedup_test flags; returns cid)
playbooks/parts_order.py  ← Parts order playbook (3 fulfillment paths + kit expansion)
playbooks/cards_order.py  ← Cards-only order playbook
playbooks/final_touch.py  ← RETIRED (ITF-era audit; superseded by `system <id>` look-back)
playbooks/intake.py       ← Read-only queue scan → board + plan (classify, schedule, dedup)
```

## Status & Next Steps

> **Session pickup (to run tomorrow):** `system <id>` is the live idempotent run.
> 1. **Restart the console first** (`quit` → `python run.py`) — lots of code changed; it doesn't hot-reload.
> 2. For a fresh order: `system <id>` does the whole thing (check-or-skip, task-driven, creates/links the
>    customer, runs the chain, cards last). It skips any task already Completed and won't overwrite tag/
>    schedule or create a duplicate customer.
> 3. **Caveat — existing/already-provisioned customers:** the chain skips a step only when its **task is
>    marked Completed** (skip-if-EXISTS detection isn't built yet — that's build step 3). If 7/8/10 are
>    Completed it correctly skips to cards. If you just need the card on an existing order, use
>    `card <so> <cust>` (no chain, no risk of a duplicate location).
> 4. MOOPS was flaky/slow last session; `navigate_to_so` now retries 3×, but a sustained outage will stop
>    a run — just re-run `system <id>` (it resumes from the To Do tasks).

Full historical log (#1-24, mostly FIXED) lives in `docs/reference.md`. Current state:

**Built & working:** first-touch (system + route), parts-order, cards-order, card-modify,
final-touch (portal checks untested), EFS kit expansion, persistent console (bare `python run.py`
→ `2auto>`, browser stays open).

**Intake — BUILT (read-only).** `python run.py intake`. Scrapes **Submitted/In Review** only,
**System-only filter** (one-line revert in `intake.run`), per-SOR detail, batch-FIFO **scheduling**
(VAC weight from the SOR), and **Admin dedup** with a sleek board badge. Honors the SOR's **Existing
End Customer** field as the authoritative verdict. Writes `intake_board.html` + `intake_plan.json`.
`inspect <sor>` dumps a SOR page. NOT yet: rules engine, query-tool address signal, SF dedup, the
resolution-gate → execution handoff (plan isn't consumed by the run yet).

**Dedup — Admin Stage 1 BUILT.** `core/dedup.py` `match_customer(order, customers)` — source-agnostic,
priority **email > phone > last-name > laundromat-name** (NOTE: address — the query-tool signal — and
SF are NOT built; CLAUDE's old "email>address>phone..." was aspirational). `portal.scrape_admin_customers`
(cached per session) feeds it. Verdict: existing / potential / new; junk rows (Delete/TBD/temp@) excluded.
`dedup "<email|phone|name>"` tests it standalone. **SF (Stage 2) reserved** — same matcher, new scraper;
browser-only (no REST API).

**`system <id>` — the main idempotent, task-driven run (BUILT, in progress).** `system <id>` (and `s
<id>`; `sx` is an alias) runs first-touch + the provisioning chain as ONE run, all FILL-ONLY (human
Saves; never submits). Built so far:
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

`provision <so> <cust>` re-runs the chain; `custid <so> [cust]` runs just the Admin cust-id setup; `card
<so> <cust>` runs ONLY the card step (safe on a system order — no tag/order-type/shipment changes).

Create-customer conventions (dialed in vs Matt's manual version):
- `Customer_ID` = next `0xxxx` (max of 0-series + 1; 10347/80211 excluded). Auto-suggested — VERIFY.
- `Customer_Name` = `_proper_case` of business name, ` - Location` suffix stripped (dealers type ALL CAPS).
- `Protocol_Password` (10-char) = condensed full name, UPPERCASE. Email = **exact** (no case change).
- `Root_Login` = name **minus business-type suffix** (`_LOGIN_DROP`) + "Admin" → "The Graybill Company"
  = `TheGraybillAdmin`, "Pure Wash Laundromats" = `PureWashAdmin`. (Unique server-side; bump on collision.)
- Region = US. Op Type = Laundromat (system only; routes warn). API user = root login w/ "Admin"→"API", POS.
- Location: ID `0100001` (first/card; +1 if existing; 02xxxxx no-card), street by leading house-number,
  State→2-letter, State→timezone map, **seat licenses = VAC count**, **Description = name only**,
  Basic Features checked; Timezone/Portal-fee left to confirm. Location_Key read from URL after save.
- Stripe = initiate only (click create → close the Stripe popup → refresh → Assign access); never fill
  the merchant application.

**Idempotent-run build order (`docs/idempotent_run.md`):** ✓ tag+schedule check-or-skip,
✓ End-Customer detection + **dedup-grab** (grab an existing match, create only when truly new — no
duplicates), ✓ task-driven chain (run To Do only), ✓ **snapshot read-once threaded into the chain**
(no SO re-read after Create Customer), ✓ nav retry, ✓ config after End Customer, ✓ card classifier,
✓ cards-order + **route cards** via `_do_cards`, ✓ **dealer-record association** (auto pick→Add Customer
→Save on the dealer record, then link the End Customer), ✓ **saved-location-id re-read** (use the id you
saved), ✓ **task 8 marks on a confirmed End-Customer link**. **Next:** skip-if-EXISTS detection for
location/Stripe/user on already-provisioned existing customers; the SOR→SO change-request reconciler.

**Config file (.cfg) notes:**
- Downloaded via Playwright `expect_download()` to `vac_configs/SO{id}/` (project folder, not Downloads).
- Filename: `SO{id}_{name}_{loc}_{VACnn}_{part}.cfg`. MOOPS's download already carries a `_VACnn_` token;
  `download_vac_configs` NORMALIZES that token to the running unit number — works for distinct VAC types
  AND qty>1 of the same type, and does NOT double-append (the old `dest==orig and n>1` test tacked on an
  extra `_VAC02`). KioskName patched per unit.
- **Upload via the page's own "Upload Files" button** (`#fileTrigger`) through the file chooser — NOT
  `set_input_files` on the hidden input (didn't trigger MOOPS's uploader → 0 files) and NOT a form-submit
  (the whole-order native POST threw the "submission error"). Then Save the SO; the .cfg's persist on the Save.
- Verify on the SAME page after Save (NO navigation): poll `read_config_file_resources` a few times for the
  File Resources `<a download>` rows to repaint, then mark task 9. (It reads the download links, so spaces
  in the customer name are fine.)
- **MUST have End Customer set first** — config is skipped with [FLAG] if End Customer not linked.
- No "already-attached" guard yet — don't re-run config on an SO that already has its .cfg's (it'd duplicate).

**Stripe notes:**
- `open_stripe`: 20s timeout on Payment Processing link + reload+retry if LP sidebar slow after location save.
- After "Add New Merchant": detects page drift post-reload, navigates back to PaymentProcessing.php if needed.
- Bank access assignment verified: re-reads grant dropdown after Assign click; prints [OK] if confirmed.

**Schedule fix (June 2026):** EXPEDITED past-target now picks earliest FUTURE week instead of returning
None. Filters `available` to future-only before taking min.

**Open / next:**
- **Commit** the accumulated `optimize-system-rerun` working tree + drop the stale `stash@{0}`, then keep
  these docs in sync with the code.
- **Missing-parts over-add on combo VACs** — a VAC with an integrated pinpad (e.g. VAC03 combo) shouldn't
  get a separate pinpad kit/attachment added; the missing-parts step is too eager. (Open.)
- **LaundroPortal location-index phantom row** — `next_location_id` reads `0100001` for a zero-location
  customer, so the suggestion is off; the saved-id re-read + MOOPS's duplicate-id block are the safety net. (Open.)
- **SOR→SO change-request reconciler** — read-only, suggest-only; parse the SOR change-log "Added:" items,
  confirm against the SO (target models live in the description). See memory `sor-so-change-request-reconcile`.
- **Task 6 (SaaS) = Salesforce** — not in the Admin/LP flow; the run leaves it To Do.
- Still queued: >5000 card-shipping line, rules engine, parts auto-pricing, money-guard.

**Dev-env note:** the OneDrive mount serves the sandbox **stale/truncated** copies of just-edited files,
so `py_compile` in the workspace often fails on unchanged lines — the file tools see the true file. Rely
on the file tools + Matt's local run, not the sandbox compile.

## Command shorthand (give Matt THIS form — never raw `--so-id`/`--flag`)

**Main runs:** `system <id>` (the idempotent task-driven run; `s <id>` and `sx <id>` are aliases),
`parts <id>` (`p <id>`), `cards <id> [name]` (`c <id>`).

**Workflow steps (run any piece):**
`dedup "<email|phone|name>"` — match a value against /customers (prints candidates + their contact +
city/state, flags different-state). `dedup-sor <sor_id>` — read a raw SOR like an order and dedupe it.
`card <so> <cust>` — ONLY the card step (clone+add+email; safe on a system order, no tag/type changes).
`provision <so> <cust>` — re-run the chain. `custid <so> [cust]` — Admin cust-id setup.
`apiuser <cust>` / `addloc <so> <cust>` / `adduser <so> <cust>` / `stripe <cust> <loc_key>` /
`intro <cust>` — individual chain steps. `itf <id>` — open the ITF Jira form standalone (rarely needed).
`tasks <id>` (read checklist) / `settasks <id>` (set+save) / `schedule <id>` (capacity) /
`final <id>` (legacy audit — retired, avoid).

**Read / inspect:** `read <id>`, `intake`, `inspect <sor>`, `createcust <id> [cust] [--preview]`,
`inspect-form <url>`, `inspect-lp <cust> <url>`, `inspect-pp <loc_key>`, `recopy`.
Legacy: `s first <id>` still runs the old ITF first-touch; `r first|final <id>` (route), `m <id>` (cardmod).

Output is summary-only by default; add `-v` for full step-by-step. Details in OPERATOR_RUNBOOK.md.
Reminder: the persistent `2auto>` console loads code at launch — restart it after any code change.

## Playwright Critical Lessons (do NOT relearn these)

1. **Save click**: Must use `page.evaluate()` JS click, NOT Playwright `.click()`. Playwright blocks 30s+ waiting for post-navigation load event.
2. **Post-save wait**: Must use `time.sleep()`, NOT `page.wait_for_timeout()`. MOOPS save destroys the execution context.
3. **Bytecode cache**: OneDrive sync causes stale `.pyc`. `sys.dont_write_bytecode = True` in run.py handles this.
4. **Product table selector**: `tr[id^="existing_part_order_"]` is always in DOM — don't use to detect save completion.
5. **Customer ID blocker**: Customer ID populated + Location empty → MOOPS blocks save. Auto-cleared before every save.
6. **Card shortname**: Target 6 chars. Strip vowels → 2-char-per-word → acronym → truncate.
7. **Card design type matching**: Use `_card_type(design)` in `run.py` — direct startswith match. The SOR gives you the value directly ("New design", "Reprint", "Existing"). No substring guessing.
8. **Customer name fallback**: Cards orders may have empty name. Fall back to card description first line.
9. **run.py keep-alive**: Use `time.sleep(1)` not `page.wait_for_timeout(1000)`.
10. **Product search**: Use `fill()` then click "Add To Order" immediately. Do NOT use `type(delay=)` or wait for autocomplete dropdown. ~0.5s per part.
11. **Lightweight navigate**: When you just need to be on the SO page (e.g. to add a card or set tasks), use bare `page.goto()` + `wait_for_selector` instead of full `read_so()`. Saves ~3s per navigation.
12. **Card part re-read after clone**: `input_value(timeout=3000)` — page may have navigated after human save, so short timeout with fallback to generated name.
13. **EFS JS escaping**: All string values must escape single quotes (`\'`) before insertion into JS template. Customer names like "Larry's" break otherwise.
14. **action_add_card_to_so**: Finds source card by scanning for any `CARD-` prefix (excluding CARD-03-01 and the new card). Works for both CARD-01-02 (new design) and existing CARD-MD-* (modify).
15. **delete_card_placeholder**: Accepts `part_to_delete` param — not hardcoded to CARD-01-02 anymore.
16. **EFS JS snippet in chat**: When Matt says he already updated the SO and can't re-run the playbook, generate the JS snippet DIRECTLY IN CHAT for him to paste into the EFS browser console. Do NOT tell him to re-run the playbook. Read the Shipping To field from the SO (or from earlier output) to get address/phone/name. Expand kits using KIT_EFS_COMPONENTS. Output the full `(function(){...})();` block ready to paste.
17. **"Continue from where you left off"**: If Matt says this and there's nothing pending, say so briefly. Do NOT say "No response requested" — that's useless. If there IS pending work, do it.
18. **SOR pages are Angular client-rendered**: `fetch()` / XHR returns template tags (`{{orderRequest...}}`), not rendered values. Must use full Playwright navigation to read SOR fields. Use `wait_for_function("() => !document.body.textContent.includes('{{')")` instead of hard `wait_for_timeout(2000)` to detect when Angular has rendered.
19. **$env:PYTHONDONTWRITEBYTECODE is redundant**: `run.py` already sets `sys.dont_write_bytecode = True`. Don't add the env var to commands.
20. **LaundroPortal is per-customer scoped**: every LP write hits whichever customer is logged in. ALWAYS guard with `current_portal_customer(page)` and abort on mismatch — writing to the wrong account is a serious error.
21. **PaymentProcessing.php direct-load bounces** to LocationPanel.php (no "current location" context). Reach it the human way: load `LocationPanel.php?Location_Key=<k>`, then CLICK the Payment Processing link (`inspect_payment`/`open_stripe` do this).
22. **Location_Key** only exists AFTER the location is saved — read it from the page URL (`Location_Key=NNNN`). The chain prompts for it if the URL parse fails.
23. **Persistent console doesn't hot-reload**: code edits need `quit` + relaunch of `python run.py` before new verbs/fixes take effect.
24. **inspect-form now also dumps buttons/links** (`--- buttons / links ---`). Clickable controls that aren't `<button>`/`a.btn` (e.g. the intro-email envelope `span.cursor-pointer > i.fa-envelope`) won't appear — grab those with a DevTools snippet.
25. **`navigate_to_so` retries 3×** on transient MOOPS failures (timeouts, ERR_HTTP_RESPONSE_CODE_FAILURE). MOOPS can be flaky / very slow; a single nav blip shouldn't kill a run mid-chain. Only a sustained outage raises.
26. **Idempotent run is task-driven**: the chain reads the task checklist and runs ONLY To Do steps (7=Stripe, 8=location, 10=user+intro, 3/4/5=card). Never blanket-reset the checklist on a re-run — it would un-complete provisioning tasks. Tag/schedule/parts are check-or-skip (set only if missing).
27. **End Customer is read from the SO field, not just notes**: `read_so_end_customer` reads the live `#validity_customer-search` widget (`02166 - Name` + location). A real cust id there = existing customer → reuse, never create. All-zeros (`00000`) IS a real (test) customer, not a placeholder — don't special-case it.
28. **Config requires End Customer first**: MOOPS uses the linked customer+location to populate CustomerKey and LocationID in the .cfg. Always set End Customer BEFORE downloading config. Chain order: End Customer → config. If End Customer fails (dealer link pending), task 9 stays To Do.
29. **SOR Angular wait**: Use `wait_for_function("() => !document.body.textContent.includes('{{')")` not `wait_for_timeout(2000)`. The fixed sleep is what made SOR reads slow (~5s vs <1s).
30. **User step is new-customer only**: `not existing` guard on task 10 — existing customers already have LP users. For a never-provisioned existing Admin customer, run `adduser <so> <cust>` standalone.

## Confluence Links

- [IT Provisioning](https://cents.atlassian.net/wiki/spaces/KB/pages/1214382081)
- [Card Orders](https://cents.atlassian.net/wiki/spaces/DO/pages/1540555257)
- [VAC Processor Selection](https://cents.atlassian.net/wiki/spaces/ED/pages/1873575954)
- [Multi-Family System Order](https://cents.atlassian.net/wiki/spaces/DO/pages/1705967638)
- [SOP Changes](https://cents.atlassian.net/wiki/spaces/DO/pages/1991508013)
- [MOOPS DB Walkthrough](https://cents.atlassian.net/wiki/spaces/ED/pages/581304348)
