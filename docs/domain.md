# Domain Reference — Order Types, Playbooks, Hardware, Cards, Scheduling

> Loaded on demand (see CLAUDE.md pointer map). Stable domain rules — not session status.

## Order Types & Playbooks

| Type | CLI | Tag Pattern | Assembly Week | ITF |
|------|-----|------------|--------------|-----|
| System (Laundromat) | `--first-touch` | "2 VAC07 (Store Name)" | Yes | Yes |
| Route (Multi-Housing) | `--first-touch` (auto-detect) | "1 VAC03 (Dealer - Location)" | Yes | No |
| Parts/Readers | `--parts-order` | Descriptive | No | No |
| Cards Only | `--cards-order` | "5000 Cards (Customer)" | No | No |
| Card Modify | `--card-modify` | (no tag change) | No | No |

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
No CARD-03-01, no SVC-LAUNDROMAT, no ITF. Task checklist: 1-2 Completed, 3-10 N/A (routes need no VAC config either). Tag = "QTY VACxx, N Readers (Dealer - Location)" — name AND address. Generic cards (CARD-MD-GEN01) under 1000 go with system. Auto-detected from Sale/Route dropdown on SO.

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

**Email CC rules:** card DESIGN email keeps MOOPS's pre-filled CC (Matt needs it). PO email clears CC. Two different rules — never share the code path.

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
| 6 | Sent SaaS contract | Completed (ITF covers this) |
| 7 | Sent Payment processing contract | To Do |
| 8 | End-customer and location added to Portal | To Do |
| 9 | VAC Config files attached to order | To Do |
| 10 | Created Admin Portal user and emailed Intro email | To Do |

### Route Orders (First Touch)

| # | Task | Status |
|---|------|--------|
| 1 | Hardware verified | Completed |
| 2 | End-customer info obtained | Completed |
| 3-8, 10 | All provisioning | N/A (no ITF for routes) |
| 9 | VAC Config files | To Do |

### Final Touch (`--final-touch`) — RETIRED (ITF-era audit)
Being replaced by the idempotent `system <id>` re-run (the "look-back" pass), which is task-driven and
does the work itself instead of opening the ITF form. Kept for reference only; do NOT run it on the
eliminate-ITF flow (it opens the ITF Jira form and mis-marks card tasks). Historical behavior below.
Run week before assembly. Reads task checklist, completes what it can, flags the rest.

| # | Task | Final Touch behavior |
|---|------|---------------------|
| 3 | Connected with end-customer | Check SO log for "card design email"; if missing, send now |
| 4 | Card approval | Check product description — PLACEHOLDER = not approved |
| 5 | Card PO | If card approved, create PO + PO email + set Purchase State → Ordered |
| 6 | SaaS contract (ITF) | Open ITF Jira form (reads SOR + internal notes for data) |
| 7-10 | IT provisioning | Blocked if ITF not done; "waiting" if ITF just sent; "verify in portal" if ITF was done previously |

**Dependencies:** 4→5 (can't PO without approval), 6→7,8,9,10 (can't provision without ITF).
**Smart reads:** SOR only fetched when tasks 3/5/6 need it. Products always read (card detection).

## Assembly Week Scheduling

- **45/week hard max**, **30/week soft cap** (30-40 = yellow, 40-45 = red/emergency only)
- **FIFO** — first available week under soft cap, not lead-time math
- Required date → work backwards (delivery minus ~2 weeks shipping)
- EXPEDITED orders can use the 35-45 range
- **Required Date on SO** = Friday of assembly week (SOP). No required date = Saturday.

## Missing Parts Analysis (decision order)

1. Wire splicers (03-01-43) → always ADD to existing qty (they stack)
2. Already handled by rule-based logic → skip
3. "OLD VERSION" in description → skip
4. Blocker plates (01-05-56) from X-series/USX reader (`CR-*-126` family, e.g. CR-02-126, CR-10-126) → skip (built-in blockouts)
4b. Long power cable (`02-06-78*`) → skip (almost never needed; add manually if required)
5. VAC pedestal (01-03-03) → skip (only if customer ordered)
6. Everything else → add

## Create-customer conventions (dialed in vs Matt's manual version)

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

## Confluence Links

- [IT Provisioning](https://cents.atlassian.net/wiki/spaces/KB/pages/1214382081)
- [Card Orders](https://cents.atlassian.net/wiki/spaces/DO/pages/1540555257)
- [VAC Processor Selection](https://cents.atlassian.net/wiki/spaces/ED/pages/1873575954)
- [Multi-Family System Order](https://cents.atlassian.net/wiki/spaces/DO/pages/1705967638)
- [SOP Changes](https://cents.atlassian.net/wiki/spaces/DO/pages/1991508013)
- [MOOPS DB Walkthrough](https://cents.atlassian.net/wiki/spaces/ED/pages/581304348)
