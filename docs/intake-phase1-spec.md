# Batch Intake — Phase 1 Implementation Spec (`--intake`)

> Read-only analysis pass over the MOOPS SOR queue. Produces `intake_plan.json`.
> Decision (2026-05-28): reads stay on **Playwright** (single dependency, runs off Matt's
> logged-in session). No Snowflake. Companion to `docs/intake-design.md`.
> Phase 2 (`--execute-plan`) is out of scope here.

## Principles

1. **Read-only.** `--intake` never writes to MOOPS, never saves, never opens email/PO/ITF. It
   navigates and reads. If it can't decide something, it flags it — it does not guess and act.
2. **Reuse, don't rebuild.** The per-order reads already exist in `core/moops.py`. Phase 1 is
   mostly orchestration + two net-new readers (queue page, portal dedup).
3. **`intake_plan.json` is the contract.** It is the only thing Phase 2 consumes. Ship Phase 1
   alone, validate the plan by eye against the live queue, then build execution on a trusted format.
4. **Additive only.** New file `playbooks/intake.py`, new config `config/intake_rules.json`, two
   new functions in `core/portal.py`. No changes to existing playbooks or read functions.

## Operating model (the morning loop)

The workflow this serves: Matt comes in, runs `--intake` against whatever is in the queue, and gets
a **board** of what's safe to run. He decides per order what to execute now vs. what needs more
research, then explicitly initiates the chosen ones ("execute these"). Phase 2 runs the selected
orders through the existing playbooks, which keep their current human stopping points. Deliberately
semi-manual for now.

Two consequences for Phase 1:
- The board is a **decision surface**, not a status dump. Everything Matt needs to make the
  run / research / skip call per order must be visible without opening the SO.
- **Execution is per-order and explicit.** Phase 1 never triggers execution. The gate between the
  board and `--execute-plan` is a human picking SOR ids.

## Command surface

```
python run.py --intake                      # analyze full Submitted/In Review queue
python run.py --intake --limit 5            # first N queue rows (dev/testing)
python run.py --intake --sor 27654,27667    # only these SORs (re-check a subset)
python run.py --intake --no-dedup           # skip portal dedup (faster dev loop)
```

`--intake` is a new playbook branch in `run.py main()`, parallel to `--final-touch` etc.:
`if args.intake: intake.run(page, limit=..., only=..., dedup=...)`. It uses the same
`launch_browser()` / keep-alive scaffold as every other command.

## Pipeline (playbooks/intake.py)

### Step 1 — Scrape the queue  *(NET NEW: `read_sor_queue`)*
- Navigate `moops.mitechisys.com/order-requests`. Parse the Submitted / In Review table rows.
- Per row capture: SOR number, dealer, type label, submitted date, linked SO id, PO number, description.
- **Selectors need validation** the same way the MOOPS selector table in CLAUDE.md was validated —
  add the confirmed queue-row selector to that table once nailed down.
- If the queue page is Angular-rendered (likely, given everything else in MOOPS is), use the
  `wait_for_function("() => !document.body.textContent.includes('{{')")` pattern from issue #18,
  not a hard sleep.
- Output: `queue[]` of lightweight rows. This is the only place we learn the SO ids; everything
  downstream is keyed off them.

### Step 2 — Per-order SO + SOR read  *(REUSE)*
For each queue row, one navigation to the SO and (conditionally) one to the SOR.
- SO read — reuse `read_products`, `read_customer_name`, `read_internal_notes`,
  `read_existing_customer_id`, `read_missing_parts`, `read_sale_or_route`, `read_task_states`.
- SOR read — reuse `read_sor_data` (processor type, required date, expedited flag, card design type,
  contact name/email, card qty) and `read_sor_shipping_method`.
- **Navigation budget:** SOR is the expensive read (~5s Angular render, issue #22). Only fetch the
  SOR when the order actually needs it — System/Route always (schedule + ITF + card), Cards needs it
  (design type + contact + qty), Parts needs it only for shipping method. Mirror the "smart SOR read"
  discipline already in `final_touch.py`.
- Capture `work_state` per SO here too — Phase 2 uses it to skip already-touched orders (see Safety).
- **SOR comments are a required, surfaced field — not just a rules input.** Dealers routinely hide
  real signal in the SOR comments: extra readers tacked on, or notes that they entered the order a
  certain way to get around something. This is where "this one needs more research" gets decided.
  Capture the full SOR comments free-text for every order. Confirm `read_sor_data` already returns
  the comments field; if it only returns structured SOR fields, add a thin `read_sor_comments`
  reader.
- **Display only — intake does NOT interpret comments.** No NLP, no classification, no reasoning over
  the text. Intake captures the raw string and shows it on the board; the human reads it and decides.
  The training CSV shows the variety of what dealers write there, but that variety is for human eyes,
  not a parsing target. (The rules engine's `keyword` type remains a separate, explicitly-configured
  opt-in for specific known strings — that is not intake "understanding" comments.)

### Step 3 — Classify  *(REUSE logic, no new MOOPS reads)*
Pure function over the products already read:
- VACs present + Sale → **System**; VACs present + Route (from `read_sale_or_route`) → **Route**
- only `CARD-MD-*` → **Cards**
- only KITs/ASSYs, no VACs → **Parts**
- ambiguous / empty → **UNKNOWN** (flag, never auto-assign)

### Step 4 — Weight + pinpad (System/Route only)  *(REUSE)*
- `core.schedule.calculate_order_weight(products)` → weighted slots (VAC01–06 = 0.5, 07–08 = 1.0).
- `determine_pinpad_kit(processor_type)` for the kit flag in the plan.

### Step 5 — Global schedule read + batch FIFO  *(REUSE + small wrapper)*
- Read capacity **once** for the whole batch: `read_schedule_capacity(page)`.
- Net-new thin wrapper `assign_weeks_batch(orders, schedule)`:
  - Sort System/Route orders by `(required_date asc, submitted_date asc)` — required-date orders
    placed first so backward-from-delivery math wins the earliest slots; pure-FIFO orders fill behind.
  - For each, call existing `pick_assembly_week(schedule, required_date, is_expedited, order_weight)`.
  - **Decrement in memory after each pick**: `schedule[chosen]["total"] += order_weight`, so the next
    order sees the reduced capacity. This is the one behavior the single-order picker can't do alone
    and is the whole reason the design wants batch assignment.
  - Carry `pick_assembly_week`'s `reason` string straight into the plan row.

### Step 6 — Customer dedup  *(two stages)*
Dedup is the riskiest part of the customer-ID problem: we want to run the whole flow, but we have to
stop and confirm we're attaching to the right account before provisioning. Two stages:

**Stage 1 — Portal/MOOPS dedup (in scope for Phase 1).**  *(NET NEW: 2 functions in core/portal.py)*
Per System/Route order with no existing-customer ID already on the SO:
- `search_admin_customers(page, customer_name, contact_name) -> list[{id, name, ...}]`
  — Admin Portal Customers tab search.
- `search_query_tool_by_address(page, street, city) -> list[{customer_id, location_id, address}]`
  — `admintools.mitechisys.com/query-tool`, Location Address EQUALS street + City EQUALS city.
- Result mapping into the plan: name/contact match → `WARN` ("Potential existing customer: ID …");
  exact location-at-address match → `BLOCK` ("Location already exists — almost certainly duplicate").
- These are the only genuinely new browser automations in Phase 1; build and test them in isolation
  (a `--dedup-only <SO>` dev flag is worth adding) before wiring into the loop.
- `--no-dedup` skips this whole step for fast dev iteration.

**Stage 2 — Salesforce dedup, resolved AT THE INTAKE GATE (FUTURE — data source not built yet).**
The SF account confirmation is the thing currently halting the flow. The design choice: resolve it
**during intake review, not during execution.** Intake surfaces the SF dedup candidates on the board;
the human confirms in SF and records the account decision as part of triaging the order. By the time
they say "execute," the account question is already answered, so execution never stops for it.

The decision recorded per order (`customer_check.resolution`):
- `new_customer` — no SF match; needs a new CUST id (IT creates customer + location + user).
- `existing_customer_new_location` — SF match; reuse CUST id, add a new location only.
- `duplicate` — exact existing customer + location; skip/block.
- `pending` — not yet resolved (default; blocks execution of that order).

**Why this matters (the unlock):** resolving dedup at the gate is what removes the mid-flow human
stop. Once an order carries a resolved account decision, first-touch can run end-to-end and the
**only** remaining manual piece is cards. This is the prerequisite for the "direct portal
provisioning" idea (CLAUDE.md issue #21) — it eliminates the ITF round-trip's blocking dependency.

Data source is TBD — SF data is likely **not** in the MOOPS Snowflake warehouse; may be reachable via
a Looker SF model or a direct SF connector. Matt to provide more detail. For now: reserve
`customer_check.sf_match` and `customer_check.resolution` in the schema; leave implementation open.

### Step 7 — Rules engine  *(NET NEW: loader + matcher, ~40 lines)*
- Load `config/intake_rules.json` (schema unchanged from `intake-design.md`).
- For each order, run every rule's `match` against its products / models / dealer / qty / comments.
- Matcher dispatch by `match.type`: `part` (exact/prefix on part numbers), `model` (regex on product
  descriptions), `dealer` (substring on dealer), `qty` (part prefix + `>= threshold`), `keyword`
  (substring in SOR comments/description).
- Collect hits per order with their severity; the most severe hit drives the row status
  (`block` > `warn` > `info`).
- **Single source of truth:** operational flags (DIP defect, EDC hybrid broken, Wascomat unsupported,
  5000+ card shipping) live in the rules file, not duplicated as prose elsewhere.

### Step 8 — Card pre-analysis (Cards + any order with a card)  *(REUSE)*
- Determine workflow from `read_sor_data` card design type via the existing substring matching:
  new design / reprint / generic / modify / none.
- New design → pre-generate shortname with `generate_card_shortname(customer_name)` (with the
  card-description-first-line fallback for empty names).
- Reprint → flag "PO needed (human step)". 5000+ qty → flag "SHIPPING line needed" (also a rule).
- This is analysis only — **no clone, no email, no PO.**

### Step 9 — Output
- Console: the structured table from `intake-design.md` (SOR / Type / Customer / Wt / Week / Card /
  Customer Check / Status) plus the footer counts (Blockers / Warnings / Rules triggered).
- **Comments display:** any order with non-empty SOR comments prints the full comment text below its
  row (or in a dedicated column), visibly marked. This is a primary purpose of the board — Matt scans
  comments to decide run-now vs. needs-research. Never truncate comments to the point of losing a
  "added 2 extra readers" / "entered this way to get around X" note.
- File: write `intake_plan.json` (schema below) to repo root.

## `intake_plan.json` schema

```json
{
  "generated_at": "2026-05-28T14:33:00",
  "queue_source": "moops/order-requests",
  "schedule_snapshot": [
    {"week": "Jul 6 - Jul 12", "monday": "2026-07-06", "total_before": 4.0, "total_after": 7.5}
  ],
  "orders": [
    {
      "sor_id": "27654",
      "so_id": 19712,
      "so_type_prefix": "SO",
      "classification": "System",
      "customer_name": "Paradise Laundromat",
      "dealer": "…",
      "submitted_date": "2026-05-20",
      "work_state": "Quote",

      "products": [{"part_number": "VAC07-31-10", "qty": 1}],
      "weight": 1.5,
      "pinpad_kit": "KIT-P630",

      "assembly_week": "2026-07-06",
      "assembly_week_label": "Jul 6 - Jul 12",
      "assembly_week_reason": "FIFO — first available week under 30 …",
      "required_date": "2026-07-25",
      "is_expedited": false,

      "card": {
        "workflow": "new_design",
        "shortname": "PARLND",
        "qty": 2000,
        "flags": ["po_needed:false"]
      },

      "sor_comments": "Added 2x KIT-DEXTER01 not on PO — dealer entered as parts to skip assembly",
      "sor_comments_flagged": true,

      "customer_check": {
        "existing_id_on_so": null,
        "matches": [{"source": "query_tool", "customer_id": "8821", "location_id": "0100001",
                     "address": "123 Main St"}],
        "sf_match": null,
        "resolution": "pending",
        "verdict": "warn"
      },

      "rules_hits": [
        {"id": "large-card-shipping", "severity": "warn", "message": "5000+ cards — add SHIPPING line"}
      ],

      "status": "WARN",
      "blockers": [],
      "notes": []
    }
  ],
  "summary": {"total": 10, "ready": 6, "warn": 3, "block": 1, "rules_triggered": 2}
}
```

Field notes:
- `status` ∈ `READY | WARN | BLOCK | UNKNOWN`, derived = max severity across customer_check verdict,
  rules_hits, and classification confidence.
- `work_state` + `so_id` are the idempotency keys for Phase 2 (skip already-processed orders).
- `sor_comments` is always populated (empty string if none); `sor_comments_flagged` is `true`
  whenever comments are non-empty, so the board can highlight without re-parsing.
- `customer_check.sf_match` is reserved for the future Salesforce dedup stage; `null` until built.
- `customer_check.resolution` carries the account decision made at the intake gate
  (`new_customer | existing_customer_new_location | duplicate | pending`). Phase 2 refuses to execute
  an order still `pending`. This is the field that lets first-touch run without a mid-flow stop.
- Everything Phase 2 needs to run a playbook without re-deciding is present: `classification`,
  `assembly_week`, `card.workflow`, `card.shortname`. (Phase 2 may still re-read live state for
  safety — see below — but it never has to re-classify or re-schedule.)

## Phase 2 hooks to bake in now (so the plan doesn't need reshaping later)

- **Idempotency:** persist `work_state` per order. `--execute-plan` skips orders whose live work state
  has advanced past intake (someone touched it manually).
- **Reuse existing playbooks unchanged.** Phase 2 will call `first_touch.run(page, so_id,
  assembly_week=...)` (already supported), `parts_order.run(page, so_id)`, `cards_order.run(page,
  so_id, shortname=...)`. We deliberately do **not** thread the rest of the pre-computed data into
  those playbooks — that would mean refactoring working code (against CLAUDE.md). A few seconds of
  redundant per-order reads at execution time is acceptable; revisit only if it measurably hurts.
- **POs never automated** — the plan only ever *flags* "PO needed"; it carries no action.

## Performance budget (Playwright reads)

Dominated by SOR Angular renders (~5s) and SO loads. Rough per-order: 1 SO nav + (0–1 SOR nav) +
(0–2 dedup searches for System/Route). For a 10-order queue expect a couple of minutes wall-clock —
acceptable for a once-per-batch analysis. Keep it down by: one schedule read for the whole batch
(Step 5), skipping SOR for Parts orders, and `--no-dedup` during development.

## Build & test order

1. `read_sor_queue` — scrape + parse, validate selectors, print rows. (Standalone, no downstream.)
2. Per-order read loop reusing existing functions → classification → weight/pinpad. Print, no JSON yet.
3. Rules engine loader + matcher against `config/intake_rules.json`. Unit-testable offline.
4. `assign_weeks_batch` wrapper over `pick_assembly_week` with capacity decrement. Verify two orders
   in the same week don't double-book.
5. Portal dedup functions (`search_admin_customers`, `search_query_tool_by_address`) — build behind
   `--dedup-only`, test in isolation.
6. Card pre-analysis.
7. Plan output: console table + `intake_plan.json`. **Ship here.** Validate against the live queue by
   eye before any Phase 2 work starts.

## To verify before/while building

- Queue page URL + row selectors (and whether it's Angular-rendered).
- Admin Portal Customers tab and Query Tool selectors for the two new dedup functions.
- `read_sor_data` returns everything card pre-analysis needs (design type, contact, qty) for the
  orders in the real queue — confirm on a couple of live SORs.
- **`read_sor_data` returns the SOR comments free-text** — if not, add `read_sor_comments`. This is a
  required output field, so confirm early.
- Submitted-date availability/format on the queue row (drives FIFO sort).
- Salesforce dedup data source (Looker SF model vs. direct SF connector) — pending Matt's detail.
```
