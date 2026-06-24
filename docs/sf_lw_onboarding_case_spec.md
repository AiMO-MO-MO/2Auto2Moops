# LW Onboarding Case — Spec

**Object:** Salesforce `Case`, new record type **Onboarding – Laundroworks** (do NOT reuse the POS onboarding record types). One Case per Opportunity, created off the Opp after Mark's SF setup. Owner = Mark (for now). `LW_Account_ID__c` / `LW_Location_ID__c` formulas already populate.

Two axes: **Status** (operational, runs on its own) and **Completion** (parallel, independent). Live = transacting · System Capable = Live + everything complete. Nothing in Completion gates shipping.

## Status (Path bar)

| Stage | Enters when | Fires |
|---|---|---|
| Unassigned | Case created (Flow off Opp) | — (auto-advances) |
| Accepted | auto | ✉ Welcome — **Stripe/Fortis variant by `Processor__c`** (hold if blank); ✉ card email if card |
| On Schedule | assembly week set | reminders on any unchecked completion |
| Shipped | system shipped | ✉ FAQ; tracking added |
| Live | real transaction after install (not the install test) | — |
| System Capable | Live AND Onboarding Complete | → CSM handoff (intro email / CSAT / assign — TBD) |

Deferred overlays: **Delayed**, **Cancelled**.

## Completion (steps → group rollup → System Capable)

Steps are editable checkboxes. Group flags, Onboarding Complete, and System Capable are **formula(checkbox)** — read-only, computed.

**Cards** (conditional) → `Cards_Done__c`
- `Card_Design_Approved__c` — Intercom ("Card Approved:" email, parse `SO-` id)
- `Cards_Ordered__c` — Looker `moops.sales_order_parts.po_number` (part `CARD-MD-*`)
- `Cards_Shipped__c` — Looker `moops.sales_order_parts` (ship field)
- `Cards_Done__c` = `Cards_Shipped__c` OR `Card_Design_Type__c = None`

**Payments** → `Payments_Done__c`
- `Payments_Set_Up__c` — Stripe `charges_enabled` (account mapped by `metadata.customerId`)
- `Payments_Done__c` = set up (processor-appropriate)

**SaaS** → `SaaS_Done__c`
- `SaaS_Signed__c` — Salesforce Opportunity stage (the Opp the Case is on)
- `SaaS_Done__c` = signed

**Rollups**
- `Onboarding_Complete__c` = `Cards_Done__c` ∧ `Payments_Done__c` ∧ `SaaS_Done__c`
- **System Capable** = Live ∧ `Onboarding_Complete__c`
- **CSM nudge** = Live ∧ NOT `Onboarding_Complete__c` ("not using full system")

## Live & money routing (Stripe)

- **Live** = real charges after `Install_Date__c` (platform `GetCharges`, grouped by `metadata.customerId`; above test threshold). Cards-independent.
- **Routing OK** = charge `customerId` == destination account `customerId`. Replacement-proof — **never verify by SO serial**. Lives on `Custom_Location__c` (durable), Case references it.

## Internal provisioning — tracked, does NOT gate

`Billing account` · `Maxio ID` · `Flip to Healthy` · `Reader connected`.

## Fields

| Field | Type | Source |
|---|---|---|
| `Status` | Picklist (Path) | Flow + sweep |
| `Processor__c` | Picklist (Stripe/Fortis) | Claude (touch) |
| `Card_Design_Type__c` | Picklist (New/Reprint/None) | Claude (touch) |
| `Install_Date__c` | Date | Looker `mitech` `vac.install_date` |
| `Card_Design_Approved__c` | Checkbox | Intercom |
| `Cards_Ordered__c` | Checkbox | Looker `moops` |
| `Cards_Shipped__c` | Checkbox | Looker `moops` |
| `Payments_Set_Up__c` | Checkbox | Stripe `charges_enabled` |
| `SaaS_Signed__c` | Checkbox | SF Opp stage |
| `Tracking__c` | Text | Looker `moops` |
| `Cards_Done__c` / `Payments_Done__c` / `SaaS_Done__c` | Formula(Checkbox) | computed |
| `Onboarding_Complete__c` | Formula(Checkbox) | computed |
| `LW_Account_ID__c` / `LW_Location_ID__c` | Formula | already exist |
| Internal: `Billing` / `Maxio_ID` / `Flip_Healthy` / `Reader_Connected` | Checkbox | ops / Looker `mitech` |

Durable Stripe/payment + reader data lives on `Custom_Location__c` (sweep keeps it current), not the Case.

## Sweep (fills it — zero manual touch)

Once daily, **anchored on open (not-System-Capable) Cases**. **Looker-first** (models `moops` / `mitech` / `payments` / `salesforce` / `intercom`); **Stripe connector only** for the routing check. Match external events to Cases by `SO-` id / `customerId`; unmatched events drop. Write changed fields only; completed Cases drop out.

## Views

- **Matrix report** Status × `Onboarding_Complete__c` — surfaces Shipped-not-Live, On-Schedule-not-ready, Scheduled-and-ready.
- **Kanban** scoped to the near-term install week (not the full weeks-out backlog).
- **Exception lists**: On Schedule & not ready (chase list, = reminder trigger); Shipped & aging (install stuck).

## Open

- **Pinpad serial** — source unconfirmed (not in `mitech.location`; maybe another explore or unmodeled).
- **Fortis** — transacting is in Looker `payments`; merchant-app *approved* status may stay manual (credentialed site).
- **CSM handoff** mechanism (email / CSAT / assignment).
- **Field API names** — verify against existing Case fields before creating (avoid duplicates); reuse `Stripe_Entry_Completed__c` only if not owned by POS flow.
