# Post-Ship Lifecycle — design (PARKED until main workflows are done)

> Decision (2026-06-09): documented, NOT built. Finish the main order workflows first.
> Sequence in the order's life: tasks complete → order waits in assembly queue → built →
> ships → delivered → pinpads verified → customer transacting. Everything here is AFTER
> the `system <id>` workflow finishes; nothing below blocks the main build.

## Architecture decision — reads come from Snowflake, not the browser

MOOPS is a database app and its DB replicates to Snowflake (`CENTS_LW.CENTS_LW_MOOPS`).
Batch reporting belongs there, NOT in Playwright:

- **Playwright** = writes + live mid-chain reads only (replication lag makes Snowflake
  unsuitable mid-run).
- **Snowflake SQL** = all batch reads/reports: shipped orders, tracking, carrier,
  `notes_from_assemblers`, work states — one query replaces N browser navigations.
  Runtime: `snowflake-connector-python` in this repo (portability rule: no Claude/MCP
  dependence in the recurring process). Looker MCP = ad-hoc chat queries only.
- **External fetches** = only data that isn't ours: carrier delivery status (public
  tracking pages), Intercom card emails (REST API).

**Verification still pending** (Looker connector auth was expired 2026-06-09):
```sql
SHOW TABLES IN CENTS_LW.CENTS_LW_MOOPS;
SELECT WORK_STATE_ID, MAIN_SHIPMENT_TRACKING_NUMBER, MAIN_SHIPMENT_CARRIER_ID,
       NOTES_FROM_ASSEMBLERS, ORDER_DATE
FROM CENTS_LW.CENTS_LW_MOOPS.<orders_table> LIMIT 5;
```
Confirm: (a) shipment columns are replicated, (b) replication lag (daily = fine for
reports). If the orders table is NOT replicated, fallback = Playwright read off the
/orders list ("Shipped - Last 60 Days" filter — validated via inspect-form).

## Data facts (validated 2026-06-09 via inspect-form on SO-19757 + /orders)

- SO page fields: `main_shipment_tracking_number`, `main_shipment_carrier_id`
  (UPS/FedEx/DHL/Polaris/USPS/CSA Transportation/RXO-CoyoteGo/Other),
  `card_shipment_tracking_number` (separate card-supplier drop ship),
  `shipping_address`, `work_state_id` (…Packed, Shipped…), `notes_from_assemblers`.
- Assembler notes carry post-build hardware identity, e.g.
  `PIN PAD S/N : 552-185-795` / `KEYS S/N : 7MA` — parseable.
- Shipment email is sent from MOOPS itself ("Send Shipment Email" button), so the SO is
  the source of truth; Intercom is NOT needed for shipping data.
- /orders list filter enumerates shipped orders: Open / Shipped 60d / 12mo / All.

## The three reports/steps (build order, when resumed)

1. **Shipping report** — SQL: shipped/packed orders + tracking + flags (no tracking
   number, no pinpad serials). Delivery status via carrier public tracking pages
   (UPS/FedEx/USPS/DHL deep links; Polaris/CSA/RXO landing pages). Output: HTML board.
2. **Pinpad/Stripe verify** — post-delivery: parse pinpad serials from assembler notes →
   LP location → Payment Processing tab → verify each pinpad assigned (verify-only
   first; fill-and-pause later). Pinpads are created on the PageStick site — needs a
   walkthrough/form dump before any fill is specced.
3. **Transacting check** — Looker/Snowflake payments data joined on cust id:
   "delivered N days, not transacting" flag. No Stripe integration needed.

Card-art flag (Intercom "Card Approved: CARD-MD-X" emails from graphics@, SO# in body,
art attached; supplier replies with proof) is specced in STATUS.md — also parked.

## What exists in code already (built 2026-06-09, UNTESTED, parked)

- `core/shipping.py` — PURE helpers, source-agnostic by design (work with Snowflake rows
  or page reads): `tracking_url`, `parse_assembler_notes`, `shipment_flags`. Reusable as-is.
- `core/moops.py::read_shipment_info(page)` — single-SO page read.
- `shipping <id>` verb in run.py — live single-SO spot-check. Fine to keep as a manual
  inspection tool; NOT the report mechanism. No tests written yet.
