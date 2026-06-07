# AUTOMOOPS V2 — Architecture

## Core Principle

Human-guided operational workflow automation. Humans decide, automation executes deterministic procedures.

This is NOT "AI agent runs the company." This IS humans making judgments, then automation executing predefined workflows.

---

## Three Layers

### Layer 1 — Human Review (Decision Point)

- Start from an SO ID (not URLs or dashboards)
- Human reviews SOR/SO data
- Human assigns a workflow type (or approves AI suggestion later)
- Output = "approved work queue"

**Key idea:** Humans decide what should happen.

Future AI assist possibilities: summarization, anomaly detection, workflow suggestions. But human remains final approver.

### Layer 2 — State / Queue (Source of Truth)

- Stores SO list with workflow assignments
- Tracks status: pending → reviewed → approved → completed → failed
- CSV or SQLite initially, could grow to a proper queue
- Enables batching later
- Enables retry, resume, audit

**Key idea:** System tracks work, not browser state.

### Layer 3 — Execution (Playwright / Python)

- Takes a single SO ID or approved queue item
- Navigates to SO via direct URL or search (NOT dashboard scraping)
- Executes a predefined workflow ("playbook")
- Logs every step and result
- Does NOT make business decisions

**Key idea:** Automation executes instructions, not decisions.

---

## Playbooks

Each order type has predefined playbooks. Human selects which one to run.

### Playbook: Card Order — New Design
1. Navigate to SO
2. Set tag: "{qty} Cards ({customer})"
3. Set Shipment Method = Drop shipment, Shipment By = Card Supplier
4. Save SO
5. Navigate to Cards → Clone A-TEMP-CARD-MD → set part number → save
6. Navigate back to SO → add card part → set qty/price → save
7. Send card design email (open form, select files, human reviews and sends)
8. If qty > 5000: add SHIPPING line (charge overage above 5000)
9. Set Work State = Placed → Save → Accept SOR

### Playbook: Card Order — Reprint
1. Navigate to SO
2. Set tag: "{qty} Cards ({customer})"
3. Set Shipment Method = Drop shipment, Shipment By = Card Supplier
4. Add existing CARD-MD-* to SO → set qty/price
5. Save SO
6. Set Work State = Placed → Save → Accept SOR
7. (PO creation is manual — never automated)

### Playbook: Parts Order
1. Navigate to SO
2. Set tag (from order description)
3. Check missing parts section → add what's flagged
4. Save SO
5. Set Work State = Placed → Save → Accept SOR
6. (EFS if needed — manual trigger, separate login)

### Playbook: System Order — First Touch
1. Navigate to SO
2. Set tag: "{qty} {VACtype} ({store name})"
3. Set assembly week
4. Add missing parts: pinpad kit (KIT-S700 or KIT-A35), CARD-03-01, SVC-LAUNDROMAT, 03-01-34, 03-01-43
5. Save SO
6. Card workflow (new design or existing — see card playbooks above)
7. Update task checklist
8. Notify Mark via Slack
9. Set Work State = Placed → Save → Accept SOR

### Playbook: System Order — Finalization (before ship)
1. Navigate to SO
2. Verify all parts present
3. Attach config files
4. Complete remaining task checklist items
5. Update SF case
6. Close out

---

## Why This Architecture

### Original Problem
The initial approach (paste URL → Playwright scripts → do everything) became brittle when trying to execute actions, branch workflows, interact with Salesforce, process multiple orders, and handle login systems.

### Key Realization
The workflow is NOT fully automatable because:
- Each SOR/SO requires human review
- Actions differ per order
- Some workflows require judgment
- Some are exceptions
- Automation should not decide business logic

### Why NOT Chrome Extensions
Extensions are good for UI enhancements and helper buttons, but break down for multi-system workflows, Salesforce handoffs, queue management, workflow state tracking, retries, and orchestration logic.

### Why NOT Dashboard Scraping
Mixing extraction, business decisions, execution, and queue management into one layer is fragile. Testing should isolate ONE known SO first.

### Why NOT Automated Login
Email magic links, MFA, and session-sensitive auth are unstable to automate. User logs in manually, Playwright reuses the authenticated session.

### Why SO IDs Instead of URLs
SO IDs enable workflow routing, queue management, scaling, and batching. URLs are debugging/override tools, not primary control flow.

---

## Testing Strategy

### Phase 1 — Single SO Test (required first)
- Hard-code 1 SO ID
- Run full workflow end-to-end
- Validate: navigation, selectors, actions, logs
- No batching

### Phase 2 — Dry Run Mode
- Same single SO
- No real actions (no submit/save/send)
- Only log "what would happen"
- Validates logic safely

### Phase 3 — Single Live Run
- Same SO
- Real execution enabled
- Still one record only

### Phase 4 — Batch Mode (later)
- CSV or queue input
- Process multiple SOs sequentially
- Stop on failure + log everything

---

## What NOT To Do

- Don't start from dashboards in test phase
- Don't automate email/login flows
- Don't run multi-order batches early
- Don't mix decision logic inside Playwright
- Don't rely on UI state for workflow selection

---

## MOOPS Page Selectors (Known from Screenshots)

### SO Page Header
- Tag: text input next to "Tag" label
- Work State: select dropdown in blue panel (Draft, Placed, etc.)
- Assembly Week: date input in blue panel
- Required Date: date input in blue panel (red when empty)
- Shipment Method: select (Ground, Drop shipment, Next Day, Freight)
- Shipment By: select (VUnics, Card Supplier, etc.)
- Save: green button top right

### SO Product Table
- Rows: `tr[id^="existing_part_order_"]`
- Part number: `th[scope="row"] a` within row
- Quantity: first `input` in row
- Price: second `input` in row
- Product search: input with magnifying glass near "Product" label
- Add To Order: button next to product search

### SOR → SO Conversion
- "Create Sales Order" button on SOR page
- Warning popup: "Unrecognized Other Parts" with Continue button
- After conversion: "Would you also like to transition the linked SOR(s) to Accepted?" with Cancel/No/Yes

### Missing Parts Section
- Header: "Missing part associations detected" (red text)
- Table: Part Number, Associated Part, Description, Quantity, Alternative

### Task Checklist
- Table with Status (select dropdown: Completed, To Do, N/A) and Task columns

### Card Operations
- Card Design Email: button at bottom of SO page, opens email form
- Cards page: search, click A-TEMP-CARD-MD, Clone button, Register Part form
- Product search on SO: type part number, dropdown appears, click result, click Add To Order

### Save Behavior
- Save required between steps to unlock features
- Product search won't find a new card part until it's saved in Cards first
- End Customer section needs save to populate
- "Create PO" only appears after SO is saved with card on it

---

## Card Shipping Rate Card

Cards over 5000: charge the difference (first 5000 free).

| Qty | Cost | Import Fee | Method | Total Shipping |
|-----|------|-----------|--------|---------------|
| 1000 | $125.00 | $60.50 | Air | $185.50 |
| 2000 | $225.00 | $121.00 | Air | $346.00 |
| 3000 | $285.00 | $181.50 | Air | $466.50 |
| 5000 | $290.00 | $302.50 | Air | $592.50 |
| 6000 | $324.00 | $687.00 | Air | $1,011.00 |
| 10000 | $500.00 | $605.00 | Air | $1,105.00 |
| 15000 | $750.00 | $907.50 | Air | $1,657.50 |
| 20000 | $580.00 | $100.00 | Sea | $680.00 |
| 50000 | $1,350.00 | $100.00 | Sea | $1,450.00 |

Air: ~3 weeks. Sea: 7-8 weeks.
