# Batch Intake Design

## Overview

Two-phase command for processing the MOOPS SOR queue in bulk. Phase 1 is read-only analysis. Phase 2 executes approved orders.

## Phase 1: `--intake`

Scrapes the MOOPS SOR queue page (Submitted/In Review section), then for each SOR:

### Per-order reads
1. Navigate to linked SO — read products, customer name, internal notes, existing customer ID
2. Navigate to SOR — read processor type, required date, expedited flag, card design type, contact info, shipping method
3. Classify order via `classify_order()` — maps the SOR **Order Type** field (authoritative): "Laundromat System" → System, "Multi-family System - Route" → Route, "Laundry Cards" → Cards, "Parts/Readers Only" → Parts. Product/VAC inference is a secondary sanity check only. NOTE: the SOR queue Type column already shows Order Type, so classification can happen from the queue table WITHOUT opening each SO — only open SOs for orders being analyzed/executed.
4. For System/Route: decode VACs, calculate weighted slots, determine pinpad kit

### Global reads (once, not per-order)
5. Read assembly schedule — calculate capacity across all weeks
6. FIFO-assign assembly weeks across ALL system/route orders together (prevents double-booking same week)

### Customer dedup (per-order)
7. Search Admin Portal Customers tab by customer name and contact name
8. Search Admin Portal Query Tool (`admintools.mitechisys.com/query-tool`) by:
   - Location Address EQUALS street address from SOR
   - City EQUALS city from SOR
9. If matches found → flag as "Potential existing customer" with ID and address
10. If location exists at exact address → escalate to BLOCKER (almost certainly duplicate)

### Rules engine check (per-order)
11. Load `config/intake_rules.json`
12. Cross-reference every product on every order against rules
13. Surface hits in plan output with severity (block/warn/info)

### Card pre-analysis (per-order)
14. Determine card workflow: new design, reprint, generic, modify, or none
15. For new design: pre-generate shortname
16. For reprint: flag that PO is needed (human step)
17. For 5000+ cards: flag that SHIPPING line is needed

### Output
- Print structured plan table to console
- Save plan to `intake_plan.json` for Phase 2

```
INTAKE ANALYSIS — 10 SORs
Schedule: May 25 (22/45), Jul 6 (4/45), Jul 13 (2/45)

 SOR       | Type    | Customer              | Wt  | Week     | Card        | Customer Check     | Status
-----------+---------+-----------------------+-----+----------+-------------+--------------------+--------
 SOR-27654 | System  | Paradise Laundromat   | 1.5 | Jul 6    | New design  | ⚠ Match: ID 8821  | WARN
 SOR-27667 | System  | Midwest Laundries     | 2.0 | Jul 6    | Reprint     | No matches         | READY
 SOR-27673 | Route   | 609 Columbus Avenue   | 0.5 | Jul 13   | Generic     | No matches         | READY
 SOR-27678 | Cards   | Laundry Haven         | —   | —        | Reprint     | No matches         | READY
 SOR-27676 | Cards   | Laundry Card Order    | —   | —        | Unknown     | No matches         | ⚠ No contact

Blockers: 0 | Warnings: 2 | Rules triggered: 0
```

## Phase 2: `--execute-plan [SOR-IDs | --all]`

Reads `intake_plan.json` from Phase 1. Runs the appropriate playbook for each approved order sequentially.

- System/Route → `first_touch.run()` with pre-computed assembly week
- Cards → `cards_order.run()`
- Parts → `parts_order.run()`

Pre-computed data (assembly week, customer type, card workflow) passed directly to playbooks — no redundant schedule reads or SOR navigations during execution.

## Rules Engine: `config/intake_rules.json`

Configurable list of rules checked during intake. Each rule has a match condition, severity, and message. Edit the file directly — add when something breaks, delete when resolved.

### Rule structure
```json
[
  {
    "id": "unique-rule-id",
    "match": {
      "type": "part|model|dealer|qty|keyword",
      "pattern": "string or regex",
      "threshold": 0
    },
    "severity": "block|warn|info",
    "message": "Human-readable explanation",
    "added": "2026-05-28"
  }
]
```

### Match types
- `part` — exact match or prefix against product part numbers on the order
- `model` — regex against product descriptions (for machine model detection)
- `dealer` — dealer name contains pattern
- `qty` — part pattern + quantity threshold (e.g. cards > 5000)
- `keyword` — text search in SOR comments/description

### Severity
- `block` — order cannot be executed, requires manual resolution
- `warn` — order can execute but flag is surfaced in plan for review
- `info` — informational note, no action needed

### Example rules
```json
[
  {
    "id": "dip-switch-defect",
    "match": {"type": "part", "pattern": "CR-11-100"},
    "severity": "warn",
    "message": "DIP switch 1-4 defect — use Reader Number Override setting",
    "added": "2026-04-15"
  },
  {
    "id": "edc-hybrid-broken",
    "match": {"type": "part", "pattern": "KIT-ALLIANCE-EDC-WASHER"},
    "severity": "block",
    "message": "Hybrid EDC washer mapping broken — Oleg investigating",
    "added": "2026-05-01"
  },
  {
    "id": "wascomat-unsupported",
    "match": {"type": "model", "pattern": "W184|W244"},
    "severity": "block",
    "message": "Wascomat W184/W244 unsupported — no door sensor feedback",
    "added": "2026-01-01"
  },
  {
    "id": "large-card-shipping",
    "match": {"type": "qty", "pattern": "CARD-", "threshold": 5000},
    "severity": "warn",
    "message": "5000+ cards — add SHIPPING line item to SO",
    "added": "2026-05-28"
  }
]
```

## SOR Queue Source

Scrape the MOOPS order requests page (`moops.mitechisys.com/order-requests` or similar). Simpler than Snowflake, no extra credentials. The queue page has: SOR number, dealer, type, submitted date, linked SO, PO number, description. Parse the Submitted/In Review table rows.

Future: migrate to Snowflake query (`INTERNAL_RAW_PROD_DB.MOOPS.order_requests` where state = Submitted/In Review) for speed and richer data.

## File structure (new)
```
config/intake_rules.json       ← Configurable rules (edit directly)
playbooks/intake.py            ← Phase 1: analysis
playbooks/execute_plan.py      ← Phase 2: batch execution
intake_plan.json               ← Generated plan (Phase 1 output, Phase 2 input)
```

## Build order
1. SOR queue scraper (parse Submitted/In Review table)
2. Per-order SO+SOR reader (reuse existing read functions)
3. Rules engine loader + matcher
4. Customer dedup via Admin Portal Query Tool
5. Global schedule read + batch FIFO assignment
6. Plan output (console + JSON)
7. Execute-plan runner (call existing playbooks with pre-computed data)
