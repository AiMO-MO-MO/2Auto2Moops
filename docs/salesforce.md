# Salesforce account creation — build spec (2AUTO2MOOPS)

> Source: live walkthrough with Mark Cummings (2026-06) + the field-level "MOOPS Order
> Processing Agent" handoff brief from a connector-based skill. **This project has NO SF
> connector** — everything is driven via Playwright on the Lightning UI
> (https://trycentssf.lightning.force.com). The brief's SOQL/connector mechanics are
> adapted: dedupe = global search (`#global-search-01`), create = filling the "New" forms.
> SF runs LAST in the chain (after MOOPS/LP), completes task 6 (SaaS/Salesforce contract).

## Dedupe FIRST (go/no-go, in intake)
Dedupe before creating — duplicates are trivially easy (e.g. "West" vs "W" made a dup
location live in the walkthrough). Signal priority in SF: **address first, then email**.
Search across Account / Contact / Lead / Opportunity / **Location**. A go/no-go decision is
made at intake; only NEW accounts get created here.

## Order of operations
Account → Location → Contact → MOOPS Opportunity → (Cents POS Opportunity if a POS is on the
order) → add Products to the opp → Account Note → set LW IDs → IT email → hand off opp owner.

## 1. Account  (New → record type "Prospect")
- **Account Name = the location/store name** (account name == location name).
- Type: **Prospect**; Account Status: **Working**.
- **LW_account_ID__c = the LW customer cust id** (the Admin cust id we already make).
- Billing address = the **end customer's store address**; expand state abbreviations to full.
- Phone + Website are needed before a contract: Google the business name + address for the
  website; only use it if it's clearly their own site (no Yelp/Maps/directories). If none,
  leave blank now (placeholder added at contracting).
- **"Moops ID" field**: the distributor's id, hidden in the URL. It caused a duplicate-value
  error on save in the walkthrough — Mark doesn't fill it; **leave it blank** (remove if it
  blocks save).

## 2. Location  (Custom_Location__c — New → "Prospect")
- Use the Google address autocomplete to set the address.
- Status: **Prospect**; Status Reason: **Working**.
- Location Type: **SS + FS** (Self-Serve + Full Service) for now (edited later once known).
- A duplicate location is expected/OK when we deliberately re-create one (the old record is
  weeded); but in general a dup here is a serious problem — that's why we dedupe first.

## 3. Contact
- First name, last name, phone, email (the end-customer contact).
- **Phone must be E.164**: `+1XXXXXXXXXX`, no hyphens (SF rejects the hyphenated format on
  save). Workaround Matt floated: save into the **mobile** field (no reformat), then edit.
- Link to the Account; same mailing address as the account.

## 4. MOOPS Opportunity
- **Name = `<street number> <first word of street>-Moops-SO-<SO#>`** (e.g.
  `3400 Vine-Moops-SO-19402`). SO# is digits only, from "Linked to SO-XXXXX" — never the PO.
- Stage: **Demo Booked**.  Type: **New Business** (existing-account expansions auto-revert to
  Upsell if other opps exist — fix to Expansion later if needed).
- **Secondary_Owner__c = `005S6000003nvEjIAI`** (Mark's id) — ALWAYS.
- Primary Contact = the Contact from step 3.
- Billing frequency: **Monthly**; Term: **One Year**.
- Demo book date + demo complete date = **today** (some status moves require a complete date).
- **Sales_Opportunity_Source__c = "LW Moops Order"**.  LeadSource: **Partner**.
- NextStep: "Install date - payment processing".  HW_Interest = **Laundroworks**.
- **Related Distributor = the dealer** (from the order).
- Close date: end of the current month (walkthrough) / today (brief) — not critical.
- **After save: re-open the opp → add Products → the location's hosting plan**
  ("Laundroportal hosting an app").

## 4b. Cents POS Opportunity  (only if a POS is on the order)
- Name = `Cents POS {Location Name}`.  Stage: Demo Booked.  Type: **Upsell - New Product Added**.
- Close date = today + 30.  Sales_Opportunity_Source__c = **"Phone - AE"**.
- Do NOT set distributor, kit count, or HW fields.

## 5. Account Note
- A ContentNote titled **"Laundroportal Set Up"** linked to the Account.

## 6. LW IDs + IT email  (IT dependency)
- Write the LW **customer** id → `LW_account_ID__c` on the Account.
- The **LW Location id** can't be entered, and the Location/Account can't be flipped from
  **Prospect → Customer**, without IT (permissions). So we end at Prospect and **emit an IT
  email** requesting it (no subject/greeting/sign-off):
  ```
  May we please make the following a customer location with LW Loc ID: <location value>
  https://trycentssf.lightning.force.com/lightning/r/Custom_Location__c/<ID>/view
  May we also make the associated account a customer account
  ```
- (Matt's goal: eventually get the permission to do this ourselves and drop IT from the loop.)

## 7. Opportunity owner handoff
- Change the **Opportunity owner** from the creator (Matt) to **Mark**, with **Send
  notification email** checked — that's how it lands in Mark's tracking report. This is how
  the order surfaces to Mark without Matt flagging it manually.

## Constraints / notes
- Phones E.164; expand state abbreviations; never use PO as SO number.
- `Secondary_Owner__c = 005S6000003nvEjIAI` on every opportunity.
- **All SF links in THIS org are `trycentssf.lightning.force.com`** (Matt confirmed
  2026-06-04, e.g. `/lightning/r/Opportunity/006S6.../view`). The brief's `cents.lightning`
  split was the other skill's setup — ignore it here. Object id prefixes: Account `001`,
  Contact `003`, Opportunity `006`, Custom_Location__c its own prefix.
- SF auth = Okta SSO (aggressive). Run inside the kept-open authenticated console; guard for an
  Okta bounce (`cents.okta.com`) and pause for sign-in. Lightning controls are in shadow DOM.
- Emails (welcome Stripe / welcome Fortis / intro) are sent from SF templates later; strip
  em-dashes before sending to customers. (We're now fully P630 — no S700.)
