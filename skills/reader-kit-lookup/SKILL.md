---
name: reader-kit-lookup
description: "Identify the correct Laundroworks reader kit (KIT-*) for a machine model number on a MOOPS Sales Order Request, AND produce the regex row to add it to the reader_lookup table. Use whenever someone asks about reader kits, machine-to-kit mapping, MOOPS reader lookup, or mentions a washer/dryer model that needs a kit assigned. Triggers: 'what kit', 'reader kit', 'no reader kit assigned', 'which reader', 'what parts', 'KIT-', 'CR-', 'reader lookup', 'MOOPS order', 'SOR', 'regex for', model numbers like HCT*, SCT*, WC0*, DC0*, EH0*, REM*, AD*, DE6*. If someone pastes a model number, use this skill."
---

# Reader Kit Lookup

## Purpose & output contract (read first)

The person already knows MOOPS shows "No reader kit assigned" — that is the *input* to this skill, not a
finding. **Never report "no kit / not in the system" as an answer.** Your job is to produce, for each
model, the answer that lets them ADD it to the system:

For every model, output exactly:
1. **Decode** — manufacturer, machine type, control era, card-only vs hybrid (one line).
2. **Kit** — the card-only KIT-* and the hybrid KIT-* (state both; pick per the SOR's Installation type).
   If the right kit doesn't exist yet, say which kit must be created and its reader/cable BOM.
3. **Regex row** — a ready-to-insert `READER_LOOKUP` row: `reg_ex`, `manufacturer`, `description`,
   `question_id` (if any), and the `READER_LOOKUP_PARTS` mapping (`part_id` / `part_hybrid_id`).

Keep it to that. No "root cause," no restating the problem.

## Fast path — do NOT drive the page per-model (efficiency is mandatory)

The Reader Lookup page loads its entire dataset once from:

```
GET https://moops.mitechisys.com/reader_lookup/index   →  JSON array (~190 rows)
```

Each row: `id, manufacturer, description, reg_ex, question_id, sample_model,
reader_lookup_parts[] { part_id, part:{part_number,description,bom...}, part_hybrid_id, is_secondary_kit }`.

**Standard procedure (≈3 tool calls, not 30):**
1. One `javascript_tool` call: `fetch('/reader_lookup/index')`, then test every target model against every
   `reg_ex` locally (`new RegExp(r,'i')`, substring). This both confirms what does/doesn't map AND gives
   you the closest existing rows to use as templates for new regex.
2. If you need SOR context (install type, qty, photos), read SORs with **`browser_batch`** (group the
   navigate + get_page_text), not one call per SOR.
3. Open the live page only to view a nameplate photo when decoding requires it.

Never type models into the search box one at a time via click→type→read. That run-style is the failure
mode this section exists to prevent.

## Two-stage matching (why a matched model still shows no kit)

1. `reg_ex` selects the row (case-insensitive, usually substring).
2. The SOR **Installation type** selects the kit from that row's `reader_lookup_parts`:
   - **CARD-ONLY** → uses `part_id`.
   - **COIN+CARD (HYBRID)** → uses that kit's **`part_hybrid_id`**.
   - **Question rows** (`question_id` set) → the dealer's answer picks which `reader_lookup_parts` entry
     applies; then card/hybrid still applies as above.

So `part_hybrid_id = NULL` ⇒ a hybrid order shows "No reader kit assigned" even though the model matched.
The fix is a kit-mapping edit (set `part_hybrid_id`), not a new regex. Distinguish the two cases when you
answer.

## Authoring regex for the table (house style)

- Patterns are PCRE, matched case-insensitively, mostly as a substring (some anchor with `.{N}$` to pin an
  18-char model). Brand-letter classes like `[SHI]`, `[SHKP]`, `[USH]` are standard.
- To make a **short/nameplate form** resolve when only the full coded model matches, add an alternative
  with `|(...)` or make a mandatory prefix optional with `?` — don't rewrite the whole pattern.
- Anchor enough to avoid cross-brand collisions (e.g. `WC...` must not catch `WX` X-Series).
- Always give the matching `READER_LOOKUP_PARTS` mapping alongside the regex, including the hybrid kit.

There is **no UI** for these tables — Oleg edits `READER_LOOKUP` / `READER_LOOKUP_PARTS` directly in
Snowflake (`CENTS_LW.CENTS_LW_MOOPS`). Deliver regex + part mapping in a form he can paste.

## Step 1 — Decode the model number

### Alliance / SpeedQueen / Huebsch
Alliance models are **15 digits** (older) or **18 digits** (newer). First letter = brand:
S = SpeedQueen, H = Huebsch, P = Primus, B = IPSO. Prefixes are interchangeable (same machine).

| Prefix | Machine Type | Example |
|---|---|---|
| HCT, SCT | Hardmount tumbler / washer-extractor (ACA/Midas) | SCT040NY0FXU800000 |
| HTT, STT | Stack tumble dryer (MDC/Centurion or newer) | HTT30NBC |
| HCN, SCN | Hardmount coin washer (ACA/Midas) | HCN030KC2OU1001 |
| HWN, SWN | Hardmount washer, small-chassis (newer ACA/Midas) | SWNNY2SP116TW01 |
| HFN, SFN | Front-load washer (newer) | SFNSXRSP112TW01 |
| HT0/ST0 "…VP/…NVP" | **"P" SpeedQueen App-ready Touch** single dryer | HT075NVP0RXS6NC000 |
| HCT…VP / "P" | "P" App-ready Touch **washer** | HCT080VP0FXB80BA00 |
| HTT…VP / "P" | "P" App-ready Touch **stack dryer** | HTT55NVP0RXS6NC000 |
| C##MD2 / U/S/H-prefixed | **Mechanical** washer-extractor, coin board (1990s Raytheon/Alliance) | C50MD2AU2 |
| SDG | Legacy gas dryer (short format) | SDG909WF |

**Control era (serial vs pulse):** Titanium (11th digit 6/8) SERIAL · Midas/Touch SERIAL · ACA (11th 3/5)
SERIAL · MDC/Centurion (15-digit) washers SERIAL-w/jumper, dryers PULSE · NetMaster PULSE · EDC PULSE.
Oleg: "all new Alliance stuff is ACA cables" (harness 30-01331 card-only / 30-01341 hybrid, ACA+Midas).
ACA & Midas share the same Generic Serial board + harness; only provisioning differs.

### Dexter
| Prefix | Series | Type | Comm |
|---|---|---|---|
| WC, WCO | C-Series | Washer | SERIAL |
| DC, DCO | C-Series | Dryer | SERIAL |
| SC, SCO | C-Series | Stacked W/D (TWO kits, "Split up kit") | SERIAL |
| WX, WXO | X-Series | Washer (DIFFERENT kit from C) | SERIAL |
| WCAD/WSAD/WCVD | A/V-Series | Washer | PULSE |
| DDAD, DSTD | A-Series/Legacy | Stack dryer | PULSE |
| WCN | N-Series | Washer (accumulator board) | PULSE |
- Short nameplate forms (e.g. `WC0600XA`) are C-Series washers; the full coded model
  (`WC0600XA-12EC2X-SSBCS-US`) is what the existing regex matches.

### Continental Girbau (= U.S. Girbau, Spain)
| Prefix | Type | Key question |
|---|---|---|
| EH | Large coin-box washer | COM board? (pre/post 2015) |
| EM | Medium washer | COM board? |
| REM | Small coin-box washer | COM board? |
Post-2015 + "INTELI COM/B" on board = SERIAL kit; older/no-COM = PULSE kit; card-ready or **hybrid** =
the CARDREADY kit.

### Electrolux / Wascomat / Laundrylux (sister brands; NOT Continental)
ELD-6xx Compass washer (serial) · DE6xx Compass dryer (PULSE only) · W### Wascomat mechanical (Gen4 =
NO KIT/unsupported; Gen5/6 = KIT-WASCOMAT-MECHANICAL) · W6xxCC Compass = KIT-COMPASSWASHER.

### LG (own protocol) · ADC · Maytag · IPSO
LG: TCW/CTD/CWG → KIT-LG-CARDREADY (card) / KIT-LG-HYBRID. ADC dryers: count buttons → Phase 5 (3) =
KIT-ADC-PHASE5, Phase 7 (4) = KIT-ADC-PHASE7; stacks = TWO readers. Maytag MDG/MLG/MAT/MFR/MHN.
IPSO WE234 (connector-type question), IWF/IWE (board-type question).

## Step 2 — Board type
Generic Serial CR-10-150/151 (Alliance serial, Dexter C/X) · Generic Relay CR-11-150B/151 (pulse: ADC,
Dexter legacy, MDC dryers) · Continental Serial CR-12-13x-18 (COM) / Pulse CR-11-13x-16 (no COM) /
Card-ready CR-12-151-18 · Compass CR-11-121-29NCDRY · LG CR-10-123-09 (card)/CR-11-151-22 (hybrid) ·
EDC CR-11-165-04 · Mechanical CR-08-117-06 (needs HV sensor).

## Step 3 — Kit reference (confirmed from reader_lookup/index, June 2026)

**Alliance "P" App-ready Touch family** (same store often mixes all three):
| Machine | Kit | reg_ex template |
|---|---|---|
| "P" Touch **washer** | KIT-ALLIANCE-SERIAL21 | `[SH]CT0[0-9]0VP.{10}$` |
| "P" Touch **single dryer** | **KIT-ALLIANCE-SERIAL17** (part 1296) | `[SHK]T[0O][1-9][0-9][NE][WV][PL].{10}$` |
| "P" Touch **stack dryer** | KIT-ALLIANCE-SERIAL18 | `[SHKIP][TG]T…VP` |

**Alliance ACA card-ready (rows 4/19) + their hybrid kits:**
| Card-only | Hybrid (`part_hybrid_id`) |
|---|---|
| KIT-ALLIANCE-SERIAL01 (washer-extractor, part 1246) | KIT-ALLIANCE-SERIAL-HYBRID-WASHER-ACA (part 2245) |
| KIT-ALLIANCE-SERIAL07 (small-chassis, part 1254) | *small-chassis ACA hybrid kit does not exist — create it* |
| KIT-ALLIANCE-SERIAL04 (ACA single dryer, 1251) | 2245 (single) / KIT-ALLIANCE-SERIAL-HYBRID-DRYER-ACA 2241 (stack) |

**Alliance mechanical (row 13):** `C[0-9][0-9]MD2` family → KIT-ALLIANCE-MECHANICAL-02 (part 1269, CR-08-117-06 + HV sensor), hybrid part 3320. Make the brand letter optional (`[USH]?C[0-9][0-9]MD2`) so bare `C50MD2…` resolves.

**Dexter C-Series washer:** KIT-DEXTER-CSERIES-WASHER-READER-PRICING (part 1838; `-WITHBLOCKOUTS` if Q17 says no factory blockouts), hybrid part 2264. Add `|(WC[0O][0-9][0-9][0-9]XA)` to catch the short form.

**Continental (rows 81 small EH / 106 large EH / 107 REM):** COM-serial coin → KIT-CONTINENTAL-SERIAL-(SMALL|LARGE); no-COM → PULSE; **card-ready or HYBRID → KIT-CONTINENTAL-SERIAL-CARDREADY (part 1360)**. For hybrid support set the COM-serial kit's `part_hybrid_id = 1360` (row 81 is correct; rows 106 and 107 are missing it).

(Full prior kit/BOM tables for Dexter A-series, LG, ADC, EDC, Compass remain valid — see Step 2 boards.)

## Step 4 — Card-only vs Hybrid
Card-only = blocker plates on, coin drop disconnected. Hybrid = no blockers, coin drop stays inline. Some
machines change board (Generic Serial vs Relay). The kit difference is captured by `part_id` vs
`part_hybrid_id` in the table.

## Common MOOPS questions
COM board? (INTELI COM/B → serial, post-2015) · coin-box vault? (yes=coin-op, no=EasyCard) · blocking
plates? (USX models already have blockouts) · dryer control? (3 buttons=Phase5, 4=Phase7) ·
before/after 2019? · WE/IWF board connector (green vs spade).

## Escalate to Oleg (creates kits / edits the tables directly — no UI)
New manufacturer, no pattern match AND no decode, harness-from-photo, mixed serial/pulse, reused readers,
suspected mapping bug, or **a needed kit that doesn't exist yet** (e.g. small-chassis ACA hybrid). Give
him: full model, machine type, install type, SOR link, photos, and your proposed regex + part mapping.
Contacts: Oleg Stepanov (kits/DB), Marc Mullings (install/hybrid), Jaydeep Patel (testing/DIP SOP).

## Known issues (2026)
~1,400 Generic Serial boards w/ DIP 1-4 defect → use Reader Number Override. EDC washer hybrid mapping
broken (card-only needs CR-11-117). Continental COM-serial rows missing hybrid pointers (set 1360).
HT0…"P" single-dryer has a stale TBD row overlapping the SERIAL17 row (dedupe).

## Reference
- `references/confirmed_mappings_2026-06-17.md` — dated worked determinations (regex + part mapping) for
  models resolved live against `/reader_lookup/index`; provenance + the granular regex edits Oleg applies.
