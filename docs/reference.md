# 2AUTO2MOOPS — Deep Reference

> This file is NOT auto-loaded. Claude reads it on demand when specific domain questions arise.
> For the cheat sheet, see CLAUDE.md. For behavior rules, see OPERATOR_RUNBOOK.md.

## Dashboard Vision (Batch Intake — Next Phase)

Current: one SO at a time via CLI. Target: batch processing all queued SORs.

**Target architecture:**
1. Read SOR queue from Snowflake — `order_requests` + `order_request_items` + `parts`. Filter state = Submitted/In Review.
2. Classify each SOR — VACs → System. KITs/ASSYs → Parts. CARD-MD-* → Cards. Multi-family → Route.
3. Check schedule from DB — `sales_orders` + `work_states` where assembly_week set. Calculate weighted capacity per week.
4. Propose batch plan — order type, suggested assembly week (FIFO), fulfillment path, flagged conflicts.
5. Human approves — Matt reviews, approves/adjusts/skips.
6. Execute — Playwright runs approved playbooks sequentially.

**Key Snowflake tables:**
- `INTERNAL_RAW_PROD_DB.MOOPS.order_requests` — SOR header (id, customer_id, state_id, sales_order_id)
- `INTERNAL_RAW_PROD_DB.MOOPS.order_request_items` — SOR line items (order_request_id, part_id, quantity)
- `INTERNAL_RAW_PROD_DB.MOOPS.sales_orders` — SO header (id, work_state_id, sales_type_id, sale_or_route, assembly_week)
- `INTERNAL_RAW_PROD_DB.MOOPS.sales_order_parts` — SO line items (sales_order_id, parts_id, quantity, unit_price)
- `INTERNAL_RAW_PROD_DB.MOOPS.parts` — Product catalog (id, part_number, part_group_id, current_stock, cost)
- `INTERNAL_RAW_PROD_DB.MOOPS.sales_types` — Order type lookup
- `INTERNAL_RAW_PROD_DB.MOOPS.work_states` — Fulfillment status
- **Critical:** Always filter `_fivetran_deleted = 'FALSE'`
- **Prefix:** sales_types.name in ('Parts','Cards only','Stock Transfer') → SOP-. Otherwise → SO-.

## Provisioning (Sequential, human-confirm pauses)

1. **Admin Portal — Create Customer** (human saves)
   - Customer ID = highest existing + 1 (manual, gaps exist)
   - Name, 10-char name, Region, Contact info, Enable Stripe
   - Create ROOT User (CustomerNameAdmin) + API User (POS access)
2. **LaundroPortal — Create Location** (human saves)
   - Location ID: 0100001 (first), 01xxxxx (shared cards), 02xxxxx (separate)
   - Address, timezone, description, Basic Features, Portal fee
3. **LaundroPortal — Create User** (human saves)
   - Username = FirstInitial+LastName, temp password
4. **Admin Portal — Send Intro Email** (after user exists)
5. **MOOPS — Tie back** Customer ID + Location ID to SO

## Workflow Split (Post First Touch)

1. 2AUTO2MOOPS first touch: parts + tag + assembly week + card + notify Mark
2. Mark dedupes: SF account search, creates account + opportunity + case
3. IT provisions: Customer ID, Location, User, Stripe config, intro email
4. SF case tracks onboarding (LW_Onboarding, LW_Stripe, LW_Fortis, LW_Cards)
5. 2AUTO2MOOPS finalization: week before ship — config files, task cleanup

## Card Shipping Rate Card

Cards over 5000: charge the difference (first 5000 free).

| Qty | Shipping | Method |
|-----|----------|--------|
| 1000 | $185.50 | Air |
| 2000 | $346.00 | Air |
| 3000 | $466.50 | Air |
| 5000 | $592.50 | Air |
| 6000 | $1,011.00 | Air |
| 10000 | $1,105.00 | Air |
| 15000 | $1,657.50 | Air |
| 20000 | $680.00 | Sea |
| 50000 | $1,450.00 | Sea |

Air: ~3 weeks. Sea: 7-8 weeks.

## Card Lifecycle Details

### SOR conflict detection (future — flag for human review):
- Design type = "New design" but comments suggest card exists
- Existing End Customer ID set on SOR — card may exist but ownership wrong
- Check if CARD-MD-* already exists for customer before cloning
- Comments field on SOR is key signal — always surface during processing

### Proof Upload (Phase 2):
1. Find SO, click into card part
2. Update image with proof from Intercom
3. Update description, verify customer ID, save

### PO Creation (NEVER automated):
1. On card part → Create PO → Supplier: Mind IoT Technology (Peter)
2. Send templated email to MIND
3. Human confirms and sends

### Updated Designs to Existing Cards:
- Create NEW card part (don't modify old)
- Note changes in email to Helena
- Set OLD card to obsolete + "[obsolete]" in description

### Existing Card Reorders:
- Confirm no design changes from comments
- Create PO immediately, send (remove CC), add expedited notes

## Confluence-Sourced SOPs

### Stripe P630 Pre-Assignment (May 2026)
- Production pre-assigns P630s by Thursday morning each week
- Serials in "Notes From Assembler"
- Ops registers P630 under Merchant Accounts for Stripe link
- Validation: NEW CARD on VAC → confirm pin pad lights up

### Fortis vs Stripe Guidelines
- Submitted Fortis application → continue Fortis (A35)
- Property with S300s → outdated, recommend Stripe + S700 (no EBT)
- Combo VAC → S700 bracket not manufactured, A35 required
- Do NOT disclose potential Fortis→Stripe conversion

### VUnics Standard Shipments
- Shipment Method = Ground/Next Day/Freight, Shipment By = VUNICS
- Placed → visible to VUNICS for fulfillment
- Canada → U.S. border crossing with commercial docs

### Shrewsbury Fulfillment (MA1)
- Product Swap: Virtual products (no VUnics inventory decrement)
- Pricing: zero Cents products, Virtual retains original pricing
- Shipment By = Shrewsbury. Ground: $35, Expedited: $60.

### Multi-Family Route (Confluence-confirmed)
- Remove Customer ID initially (no Location ID yet)
- Tag: "2 VAC03 combo (Dealer - Address)"
- Clean Notes To Assemblers — remove pricing, add assembly instructions
- Most tasks N/A for routes

## Existing Customer Differences

- Existing End Customer ID in Internal Notes: "Existing End Customer: Name (XXXXX)"
- Card End-Customer uses real customer ID, not Mitech
- Contact info not on SOR — look up from Admin Portal
- No new user/credentials needed, only new location in LaundroPortal
- Access Sharing = No → separate location IDs (02xxxxx)

## Timing Observations

- Save: ~33s. SOR round-trip: ~5s. Adding parts: ~6s each.
- Full first-touch: ~70-80s without card workflow.
- ITF form: ~5s to fill (radio button selectors need fixing).

## Historical Bugs (fixed, for context)

- Pinpad kit qty counted all VACs instead of only pinpad-equipped (Z≠0). Fixed.
- Paper roll qty counted all VACs instead of touchscreen only (07/08). Fixed.
- `determine_pinpad_kit()` compared `== "2"` but SOR text is "Yes — Fortis (EBT)". Fixed: matches FORTIS/EBT.
- Missing parts table uses `th` + `td`, not just `td`. Fixed.
- Delete button is SVG `svg[data-icon="trash-alt"]`, not a button element. Fixed.
- MOOPS date format: abbreviated months (%b before %B in parser). Fixed.
- Splicers ADD to existing qty, don't replace. Implemented correctly.

## Process Discipline (lessons baked into code)

- Test each action individually before adding to playbooks
- Rule-based parts ≠ missing parts (different DOM, different logic)
- Product table order matters (hardware first, SVC last)
- Card clone must be separate run from add-card-to-so (MOOPS search delay)
- Don't auto-save after card operations — human reviews first
- ITF data comes from Internal Notes, not SOR navigation
- Schedule must be checked fresh each time

## Onboarding Emails (SFMC, not Intercom)

Templates: https://docs.google.com/document/d/1YoNeK8VVucU8VOMIHdU04lUiNTbQabj_hzwSm5H6JHs/edit

Emails: Welcome, Fortis Payment, Stripe Payment, Payment Reminder, Custom Cards, Card Reminder, FAQ/Shipped. Triggered by SF case status changes.

## Key Contacts

- **Matt** — Built this, knows the full workflow
- **Oleg Stepanov** — Reader kit mappings, DB config, kit creation
- **Marc Mullings** — Install troubleshooting, hybrid setups
- **Mark** — SF account dedup, opportunity + case after first touch
- **Andrew** — Route order management (Jira)
- **Jaydeep Patel** — Production ops, reader testing, DIP switch SOP
- **Helena** — Card design updates
- **Peter (Mind IoT Technology)** — Card supplier, POs

## Known Issues & Next Steps (historical log — moved out of CLAUDE.md May 2026)

1. ~~Task selects not found after ITF~~ — FIXED.
2. ~~ITF radio buttons timeout~~ — FIXED. Pure JS DOM walking.
3. ~~Save popup~~ — FIXED. Auto-dismiss after save.
4. ~~Route auto-detect~~ — FIXED. Reads Sale/Route dropdown.
5. ~~Existing customer save blocker~~ — FIXED. `_clear_customer_id_if_blocking()`.
6. ~~Save speed~~ — FIXED. JS click + `time.sleep()`.
7. ~~Product search speed~~ — FIXED. `fill()` + immediate "Add To Order", skip dropdown. ~0.5s per part.
8. ~~read_missing_parts NameError~~ — FIXED.
9. ~~ITF step ordering~~ — FIXED. ITF is step 8 (before cards step 9).
10. ~~EFS JS syntax error~~ — FIXED. Single quotes in customer names escaped.
11. ~~Cards-order tag wipe~~ — FIXED. Save before card workflow preserves tag.
12. ~~Address includes customer info~~ — FIXED. Parser stops at "Existing End Customer" lines.
13. ~~Route task checklist~~ — FIXED. `is_route` param; routes now 1-2 Completed, 3-10 N/A.
14. ~~Generic cards tag~~ — FIXED.
15. ~~Final touch playbook~~ — DONE. `playbooks/final_touch.py`. Tested on SO-19582, SO-19662.
16. ~~Portal checks (tasks 7,8,10)~~ — DONE. `core/portal.py`, integrated into final_touch Phase 5. NOT YET TESTED.
17. **Config file download (task 9)** — Future. JSON command sets per VAC, naming `SO{ID}_{Customer}_{LocationID}_{VACxx}_{Part}.cfg`. Cust ID associated with dealer first, then Cust ID + Location set on SO, then download/rename/upload to File Resources.
18. **Dead code cleanup** — 6 items in moops.py (read_tag, read_vacs, read_processor_type, map_sor_to_efs_shipping, action_set_shipment_3pl, duplicate docstring). Confirm with Matt before removing.
19. **Batch intake** — Phase 1 BUILT (see Status in CLAUDE.md + `docs/intake-design.md`, `docs/intake-phase1-spec.md`).
20. ~~EFS kit expansion~~ — DONE. `KIT_EFS_COMPONENTS` in `core/efs.py`. Only KIT-A35 expands. Fixed `03-01-101` (S700 pinpad).
21. **Direct portal provisioning** — Future: eliminate ITF by provisioning Cust ID, location, user directly. Unblocked by the intake dedup gate.
22. **Parts order optimizations (May 2026)** — SOR shipping read uses navigation + `wait_for_function` (Angular). Swap dialog timeouts cut. ~9s total. `PART_NAMES` dict for clean tags.
23. **Auto-pricing for parts orders** — Future. Parts with "Target Machines:" = BOM → $0.00. Parts WITHOUT target machines at $0.00 → read selling price from popover `data-content` and fill. Readers (CR-*) have target machines but ARE priced. Only ADD pricing, never change existing.
24. **Order classifier + first-touch guard** — Designed. `classify_order()` maps SOR Order Type field → playbook. (a) first-touch reads Order Type in Step 2 and **aborts before any writes** if not System/Route (money-guard, build FIRST). (b) intake already classifies from the queue Type column.
