---
name: moops-dedupe
description: >
  Dedupe a MOOPS Laundroworks system order against BOTH the Admin Portal customer list and
  Salesforce, live, and surface every possible match in each system (with what it matched on) so a
  human can decide new-vs-existing BEFORE a Sales Order is created. Use whenever someone says
  "dedupe the SORs", "dedupe intake", "check for duplicate customers", "is this a new customer",
  "any matches for SOR-XXXXX", "run the dedupe", or "check this order against Salesforce / Admin".
  Reads the live MOOPS order-requests queue and each SOR via the Chrome browser connector, queries
  Salesforce via the SF connector (SOQL/SOSL), and matches the live Admin /customers list via the
  Chrome connector's JavaScript tool. Matches on CUSTID, email, phone, business name, last name, and
  address. READ-ONLY — never creates, edits, or deletes anything in either system.
---

# MOOPS Dedupe — Admin Portal + Salesforce (live)

You determine whether the customer on a MOOPS **system order** already exists, **before** a Sales
Order is created, by checking two systems live and bringing back every plausible match with the
signal it matched on. You never decide silently and you never write to either system. This is the
gate, not the gatekeeper.

The same SOR read also carries the **Card Reader Kits** table, so the run additionally reports
**reader-kit assignment** for each order's machines (Step 2b) — deterministic, no extra SOR
navigation and one shared index fetch. This is read-only too: surface the model → kit determination
(and what's blocking an assignment); never edit the lookup tables.

## Prerequisites (the skill rides YOUR sessions — it carries no credentials)

- **Salesforce connector** connected to the org (`trycentssf`), under a login with read access to
  Account, Contact, Lead, and Custom_Location__c. (This skill uses whatever SF connector is
  attached; it stores no token.)
- **Chrome browser connector** (Claude in Chrome) with a logged-in browser that can reach **MOOPS**
  (`moops.mitechisys.com`) and the **Admin Portal** (`admintools.mitechisys.com`). It reads your
  authenticated session — if you can't open those pages in your browser, the skill can't either.

If either is missing, say so plainly and stop — the data cannot be fetched another way.

## Scope — only dedupe these

Apply both filters:

1. **Queue status = Submitted/In Review only.** Ignore Awaiting Update, Accepted, Cancelled — those
   are past the intake decision; dedupe is a pre-SO gate.
2. **Order Type = "Laundromat System" only.** Skip **Routes** (Multi-family System – Route/Sale;
   they attach to an existing dealer umbrella account), **Cards**, and **Parts/Readers Only** (not
   new-customer events; may not be tied to an account at all).

On the queue, the Type column gives the type and the section heading gives the status.

## Match on ALL signals, query LIVE every run

Match on every signal available: **CUSTID, email, phone, business name, last name, address
(street/city/state)**. Priority: email/phone/CUSTID = **strong**; name/last-name/fuzzy-address =
**weak**. Strong hit ⇒ likely **existing**; weak only ⇒ **possible**; nothing ⇒ **new**. Surface
*all* candidates with the signal that hit — don't suppress a weak match because a strong one exists.

**Always query both systems live.** Never reuse a cached result file for the matches — dedupe's only
value is reflecting the current state of both systems at decision time.

## Run efficiently — batch, short-circuit, one pass each

Do NOT fan out one query per order per signal, and don't narrate every hop — gather, batch, present.
A run of N orders should be: one queue read + one read per SOR + ~4 SF calls + one Admin call + render.

1. **Gather all in-scope orders' signals first** (Step 1–2), then dedupe them together.
2. **CUSTID short-circuit:** orders that name an Existing End Customer id are essentially resolved by
   one ID lookup — don't run the fuzzy fan-out for them.
3. **Batch SF with IN-lists**, not per-order calls:
   - `SELECT Id,Name,LW_account_ID__c,Type,BillingCity,BillingState FROM Account WHERE LW_account_ID__c IN (<all custids>)`
   - `SELECT Id,Name,Account.Name,Account.LW_account_ID__c FROM Contact WHERE Email IN (<all emails>)`
   - `SELECT Id,Name,Company,Email,IsConverted,Moops_Customer_id__c FROM Lead WHERE Email IN (<all emails>)`
   Then run the **per-term SOSL** (phone / name / address) ONLY for orders still unresolved after the
   ID + email batch. (SOSL can't be IN-listed, so it's the only per-order part — minimize it.)
4. **Admin = one JavaScript-tool call for ALL orders** (Step 4) — never per order.
5. **Combinability query only for orders with an SF account match:** one
   `Custom_Location__c WHERE Account__c IN (<matched account ids>)` covers them all.
6. **Reader-kit = one index fetch, and ONLY if a kit is missing** (Step 2b): skip it entirely when
   every machine already has a kit; otherwise `fetch('/reader_lookup/index')` once and match every
   unassigned model locally — never the per-model search box.
7. **Render once** at the end.

## Step 1 — Read the live MOOPS queue (Chrome connector, JS extraction)

**NEVER `get_page_text` the queue or the SOR pages** — they dump hundreds of rows/lines into context
and are the single biggest reason a run burns usage. Use the `javascript_tool` to extract ONLY the
fields you need and return compact JSON.

1. `list_connected_browsers` → `select_browser` → `tabs_context_mcp(createIfEmpty: true)`.
2. `navigate` to `https://moops.mitechisys.com/order-requests`, then run `references/queue_extract.js`
   via `javascript_tool` (`action: javascript_exec`). It waits for the Angular render, walks the
   table tracking the section heading, and returns ONLY the in-scope rows
   (`section == 'Submitted/In Review'` AND `type` contains `Laundromat System`) as compact
   `[{sor, type, linkedSO, desc}]` — typically a handful of rows, not the ~400-line page.

If a call returns "Browser connection is unavailable", retry once — the bridge is occasionally flaky.

## Step 2 — Extract the dedupe signals from each SOR (JS extraction, batched)

For each in-scope SOR, `navigate` to `https://moops.mitechisys.com/order-requests/<id>` and run
`references/sor_extract.js` via `javascript_tool`. It returns ONLY the dedupe fields as compact JSON
(~7 fields), NOT the whole ~150-line page. **Batch all SORs in ONE `browser_batch`** (navigate +
javascript_tool pairs) so it's a single round trip.

`sor_extract.js` pulls from the **End Customer/Operator Info** block (falling back to Shipping):
- **Business name** ← `Location Name` / `Description`
- **Contact** ← `New Contact Name` / `New Contact Email` / `New Contact Phone`
- **Address** ← `Location Address`
- **Existing End Customer** — if a real cust id is named, that's the authoritative verdict; record it
  and still confirm in both systems.

Normalize: phone → last 10 digits; email → lowercased first token. ONLY if a field comes back empty
for a given SOR, fall back to `get_page_text` for that one SOR.

**Same batch, second JS call per SOR:** run `references/sor_readers_extract.js` alongside
`sor_extract.js` (same navigation) to capture the **Card Reader Kits** table + **Installation type**.
It returns `{sor, installType, machines:[{model,desc,partReq,kit,secondary,assigned,qty,kitsNeeded}]}`.
Carry it into Step 2b.

## Step 2b — Reader-kit assignment (only when a kit is missing)

**Gate — run this step ONLY if at least one machine came back `assigned === false`.** The cheap
table read in Step 2 already tells you: if every machine on every in-scope SOR has a Reader Kit, there
is nothing to resolve — **skip Step 2b entirely (no `/reader_lookup/index` fetch)** and just note
"all reader kits assigned" for those orders. The index fetch + matching happens only for the orders
that actually have an unassigned model.

When the gate is open: collect the unassigned models (`assigned === false`, i.e. the Reader Kit cell
reads "No reader kit assigned") across ALL orders that have them, then make **one** call: run
`references/reader_match.js` (with `MODELS` filled in) on any MOOPS tab. It does `fetch('/reader_lookup/index')` once and tests every
model against every `reg_ex` locally, returning the matched row(s) with the card kit (`card_part_id`/
`card_kit`), the `hybrid_part_id`, and any `question_id`. **Never** drive the `/reader_lookup` search
box one model at a time.

Then classify each unassigned model (two-stage: regex picks the row, the order's **Installation
type** picks the kit from that row's parts):

- **No regex match** (`matched:false`) ⇒ genuinely not in the table → decode the model and propose a
  new `READER_LOOKUP` regex + part mapping; escalate to **Oleg** (no UI — he edits Snowflake).
- **Matched, but the order is `COIN+CARD (HYBRID)` and `hybrid_part_id` is null** ⇒ the row has no
  hybrid kit. Check the SOR **Comments** for a per-machine card-only/hybrid split (orders mix). If the
  machine is actually **card-only**, the answer is the row's `card_kit` (it shows unassigned only
  because the order-level HYBRID flag forces the hybrid path). If it's **truly hybrid**, the hybrid
  kit must be created/mapped → escalate to Oleg.
- **Matched, card-only order, `card_part_id` present** ⇒ should have resolved; flag as an anomaly to
  re-check (stale/overlapping row, or the model didn't match what MOOPS used).

Output per the reader-kit contract — for each unassigned model: **decode** (one line), the **card-only
+ hybrid kit**, and a **ready-to-paste regex/part row** only when a new mapping is actually needed.
Do not report "no kit assigned" as a finding — that's the input.

## Step 3 — Salesforce dedupe (SF connector, read-only SOQL/SOSL)

**Salesforce is the master record.** A customer can live across four objects; the MOOPS CUSTID may
be stamped on any of them. **Every connector call passes `x-gram-toolset-id`
= `019e02db-e17c-7698-8a89-f00e3692570b` unchanged.** Signals by object:

| Object | Match on | MOOPS / LW id field |
|--------|----------|---------------------|
| **Account** | `Name`, `Phone`, billing address | `LW_account_ID__c` = MOOPS customer id (CUSTID) |
| **Contact** | `Email`, `Phone`, `Name`/`LastName` | (linked to Account) |
| **Lead** | `Email`, `Phone`, `Company`, `Name` | `Moops_Customer_id__c` (CUSTID), `Moops_Location_Key_ID__c`, `Existing_Customer__c` |
| **Custom_Location__c** | `Name`, `Street__c`/`City__c`/`State_Province__c`, `Phone_Number__c` | `LW_Location_ID__c`; parent via `Account__c` (relationship name `Account__r`) |

**Tier 0 — CUSTID (run first if MOOPS gives an Existing End Customer id / location key):**
```sql
SELECT Id, Name, LW_account_ID__c, Type, BillingCity, BillingState FROM Account WHERE LW_account_ID__c = '<custid>'
```
```sql
SELECT Id, Name, Company, Email, Phone, Status, IsConverted, Moops_Customer_id__c FROM Lead WHERE Moops_Customer_id__c = '<custid>'
```
CUSTID format varies — MOOPS pads to 5 digits (`00378`); some SF rows hold it unpadded (`1595`). Try both.

**Tier 1/2 — contact / name / address.** Batch email across all orders with one `Contact ... WHERE
Email IN (...)` and one `Lead ... WHERE Email IN (...)` (see "Run efficiently"). Then use the SOSL
templates below **only for orders still unresolved** (skip an order already matched by CUSTID/email).
Templates:
```sql
SELECT Id, Name, Email, Phone, Account.Name, Account.LW_account_ID__c FROM Contact WHERE Email = '<email>'
```
```sql
SELECT Id, Name, Company, Email, Phone, IsConverted, Moops_Customer_id__c FROM Lead WHERE Email = '<email>'
```
```
FIND {<10 digits>} IN PHONE FIELDS RETURNING Contact(Id,Name,Phone,Account.Name,Account.LW_account_ID__c), Account(Id,Name,Phone,BillingCity,BillingState,LW_account_ID__c), Lead(Id,Name,Company,Phone,Moops_Customer_id__c), Custom_Location__c(Id,Name,Phone_Number__c,LW_Location_ID__c,Account__c)
```
```
FIND {<business or location name>} IN NAME FIELDS RETURNING Account(Id,Name,BillingCity,BillingState,LW_account_ID__c), Lead(Id,Name,Company,IsConverted,Moops_Customer_id__c), Custom_Location__c(Id,Name,Street__c,City__c,State_Province__c,LW_Location_ID__c,Account__c), Contact(Id,Name,Email,Account.Name)
```
```
FIND {<street number + street name>} IN ALL FIELDS RETURNING Custom_Location__c(Id,Name,Street__c,City__c,State_Province__c,LW_Location_ID__c,Account__c)
```

Notes: an empty result (`totalSize:0` / `searchRecords:[]`) means no match = new in SF, not an
error. SOSL name search is fuzzy — tie-break weak name hits with **city/state** before asserting a
match (e.g. an Alabama "Bam's" is not a Georgia "Bam Bam's"). `Lead.IsConverted = true` ⇒ the truth
is the Account it became. `LW_account_ID__c` null on a matched Account ⇒ it's in SF but **not
provisioned in Laundroworks** — flag it. Build links as
`https://trycentssf.lightning.force.com/lightning/r/<Object>/<Id>/view`.

### Combinability — the Cents Location ID is the red flag

When an SF match lands on a location, pull the matched **`Custom_Location__c.Cents_Identifier__c`
("Cents Location ID")** — the authoritative signal — plus `Status__c` and Account `Type`:
```sql
SELECT Id, Name, Cents_Identifier__c, Status__c, Cents_Active__c, LW_Location_ID__c, Account__r.Type
FROM Custom_Location__c WHERE Account__c = '<account id>'
```
- **🚩 FLAG — hard to combine:** `Cents_Identifier__c` (Cents Location ID) **populated** = a live
  Cents POS identity; merging a new MOOPS record into it is very hard in SF. **Do NOT auto-create or
  auto-link — escalate for manual reconciliation.** (Usually also `Type=Customer`, `Status=Active`.)
- **Easy (convertible):** **no `Cents_Identifier__c`** (blank), typically `Type=Prospect` /
  `Status=Prospect`. `Cents_Products_Provisioned__c = "Cents"` alone is **not** a flag — Cents can be
  provisioned without a Cents Location ID; the ID is what matters.

## Step 4 — Admin Portal dedupe (Chrome connector's JavaScript tool — ALL contact fields)

The Admin `/customers` page loads the full customer list into the DOM (~2,000 rows). The on-page
"Filter customers" box matches **business name only** — it does NOT search the Main Contact
email/phone. The `/query-tool` page searches the provisioned location fleet (address/CUSTID) but has
no contact fields. So to match Admin on **email/phone/last-name/name** you read the whole list and
match in code, in one shot, with the JavaScript tool:

1. `navigate` to `https://admintools.mitechisys.com/customers` (wait for the table to render — it has
   >50 `tbody tr`).
2. Run `references/admin_dedupe.js` via `javascript_tool` (`action: javascript_exec`), with the
   `ORDERS` array at the top filled in from Step 2 (sor, email, phone, contact name, business name).
   It extracts every row's `{cust_id, name, Main-Contact name/email/phone}`, normalizes, and matches
   each order on email/phone/last-name/business-name — returning **only the matches** (compact), so
   the full list never has to come back into the conversation.

It returns `{ n_customers, results: { "<sor>": { verdict, matches:[{cust_id,name,signal,strength,detail,contact}] } } }`.
Verdict: email/phone hit = `existing`; name/last-name only = `potential`; none = `new`. Common
surnames ("Sanchez", "Roberts", "Martin") produce last-name noise — label them weak and rely on
email/phone/address for confidence.

(Optional address/CUSTID check: the `/query-tool` page can confirm whether a live location already
exists by `Location Address CONTAINS <street>` or `Customer ID EQUALS <custid>` — useful, but it
only covers provisioned locations and no contact fields.)

## Step 5 — Combine, present, and (optionally) build the board

For each order, give the bottom line with **both systems on one line** and the combinability flag.
Call out cross-system disagreement explicitly — it's the most actionable result:

- In **SF but not Admin** (matched SF account, `LW_account_ID__c` null, no Admin contact match) ⇒ in
  Salesforce, not provisioned in Laundroworks → link to the SF account, don't create a duplicate.
- In **Admin but not SF**, or matched under **different names** in each (one by name, one by address)
  ⇒ existing customer, records not cross-linked.
- 🚩 **Cents Location ID present** on the SF match ⇒ hard to combine; manual reconciliation.

Example:
> **SOR-26542 · Bubble Blast (Clermont FL)** — 🚩 Existing in Salesforce, hard to combine.
> Admin: no email/phone match (only "Sanchez" surname noise). Salesforce: exact address under
> "Laundromart of Four Corners" + owner's phone/email, **Cents Location ID 513** (live Cents).
> → Don't auto-create; reconcile manually. Also not in Laundroworks (needs provisioning).

To produce a visual board, assemble a `dedupe_results.json` (list of orders, each with `admin` and
`sf` blocks of matches + a `verdict`/`flag`) and run `python render_board.py dedupe_results.json
dedupe_board.html` (bundled in this folder). Present the HTML file.

## Guardrails

- **Read-only.** Never create, edit, or delete anything in Salesforce or MOOPS. SF *record creation*
  (Account/Location/Opportunity/emails) is a separate workflow — hand off, don't start it here.
- **Never auto-resolve.** Surface candidates with the matched signal; the human decides.
- **Filter junk** Admin rows by name/email (`delete`, `tbd`, `test`, `temp@temp`, `na@na`).
- **Tie-break weak name/address hits by city/state.** Don't call a different-state same-name a match.
- **Don't conflate PO with SO; don't pull pricing.** Neither is a dedupe signal.

## Reference

- `references/queue_extract.js` — Step 1: extracts ONLY in-scope SORs from the queue (compact JSON).
- `references/sor_extract.js` — Step 2: extracts ONLY the dedupe signals from one SOR (compact JSON).
- `references/sor_readers_extract.js` — Step 2: the Card Reader Kits table + Installation type from one SOR.
- `references/reader_match.js` — Step 2b: one `/reader_lookup/index` fetch + local regex match for all models.
- `references/sf_queries.md` — validated SOQL/SOSL patterns with real sample output.
- `references/admin_dedupe.js` — the live Admin extraction + match script for the JavaScript tool.
- `render_board.py` — builds `dedupe_board.html` from a `dedupe_results.json`.

For full model decoding, board types, and the kit/regex house style, defer to the **reader-kit-lookup**
skill — this step reuses its logic; it does not replace it.

> **Efficiency contract:** Steps 1, 2, 2b, and 4 all return compact JSON via `javascript_tool`. Do NOT
> `get_page_text` the queue, the SOR pages, or `/customers` — those full-page dumps are what made the
> old version burn usage. Fall back to `get_page_text` only for a single field that came back empty.
> The reader-kit pass costs one extra JS call per SOR (on the page already open) to read the kit
> table, and adds the `/reader_lookup/index` fetch ONLY when a machine is actually unassigned — if
> every kit is assigned, Step 2b is skipped entirely. Never the per-model search box.
