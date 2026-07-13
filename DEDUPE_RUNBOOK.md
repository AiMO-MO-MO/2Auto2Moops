# SOR Dedupe — Runbook

**Why:** before converting a system order, check whether the customer already exists in
**Laundroworks/Admin** and in **Salesforce**, so we don't create a duplicate account.
Duplicate SF accounts / mismatched Laundroworks records are what break billing downstream —
this catches them *before* the order is kicked off.

The board shows you the evidence; **you make the new-vs-existing call.** It never auto-decides.

---

## One-time setup
- Python env with Playwright installed (the repo's normal setup).
- Be logged into **MOOPS / Admin** in the tool's `chrome_profile` — the same login you use to
  run orders. (Admin dedupe reads this live; no separate login.)
- For the Salesforce step you need a **Cowork chat with the Salesforce connector enabled and
  this project folder connected.** (Salesforce is only reachable through Claude — see below.)

---

## Each time you work the queue

1. **Terminal — run intake.** Start the console and run intake:
   ```
   python run.py
   2auto> intake
   ```
   This scrapes the Submitted/In-Review queue and checks **Admin/Laundroworks live**, then writes
   `dedupe_data.js` + `dedupe_keys.json` and opens `dedupe_board.html`.
   At this point the board shows Admin matches; the Salesforce panel says **"not run."**

2. **Salesforce step — run it through Claude.** In a Cowork chat (Salesforce connector + this
   folder connected), say:
   > **Run the SF dedupe step per DEDUPE_RUNBOOK.md.**

   Claude reads `dedupe_keys.json`, runs **one** Salesforce search, and writes `sf_data.js`.
   (This is the only part that uses Claude — one batched call.)

3. **Reload `dedupe_board.html`** in your browser. Both Admin and Salesforce are now filled in.

4. **Read each order and decide** — existing (use the account / CUSTID shown) or new.

> Restart the console (`quit`, then `python run.py`) if the code was updated — it only loads
> code at launch.

---

## Reading the board
- Each card is one waiting **system** order, with its contact and address.
- Two panels per card: **Admin Portal (live)** and **Salesforce (live)**. Each lists candidate
  matches and **what they matched on**.
- **Strong** = matched on address / email / phone. **Weak** = name only (treat as a maybe).
- **🚩 Cents Location ID** on a Salesforce match = a live Cents identity — reconcile manually,
  don't just create.
- Empty panel = no match in that system.
- Match trust order (Salesforce): **address > email > contact name > store name.** Store-name-only
  hits are the weakest and often just same-name businesses elsewhere.
- Watch for **dealer contacts**: a phone/name hit to a distributor (e.g. an Alliance rep) means
  the *dealer*, not the end customer.

---

## SF step — instructions for Claude

*(This is what "Run the SF dedupe step" means. Follow exactly; keep it to one Salesforce call.)*

1. **Read `dedupe_keys.json`** in the project root — a small list of
   `{sor_no, address, email, contact_name, store_name, phone}` per waiting system order.

2. **Run ONE batched SOSL** via the Salesforce connector's `find` tool. Build a single
   `FIND {...} IN ALL FIELDS RETURNING ...` whose search terms are, across all orders:
   - **address** — the distinctive `street# + street name` fragment (e.g. `"7017 Liberty"`).
     **Skip generic ones** like `"351 Broadway"`.
   - **email** — each non-blank email, quoted.
   - **contact_name** — each, quoted (e.g. `"Stephen Dumas"`).
   - **store_name** — the full store name, quoted. **Skip bare generic chain names**
     (`"Laundromax"`, `"Clean Rite Center"`, `"Laundry King"`) unless distinctive — they flood.
   - **phone** — 10 digits, no formatting (e.g. `3478865347`).

   RETURNING (safe fields only, with per-object LIMIT so it can't flood):
   ```
   RETURNING Account(Id, Name, BillingStreet, BillingCity, BillingState LIMIT 50),
             Contact(Id, Name, Email, Phone, MobilePhone, Account.Name LIMIT 50),
             Custom_Location__c(Id, Name LIMIT 50)
   ```
   `Custom_Location__c` has **only Id and Name** as safe fields — do NOT add other fields
   (there is no `Account_del__c` etc.; guessing one errors the whole call).

3. **Map results back to each SOR** by verifying the actual key (email equals, phone last-10
   equals, address street# + street present, name matches). Priority **address > email >
   contact name > store name**. An address hit on a **Custom_Location__c** means the *site
   already exists in SF* — the strongest signal.

4. **Write a FRESH `sf_data.js`** in the project root — replace the whole file, covering
   **every** order in `dedupe_keys.json` (don't append to a prior run's file). Keyed by `sor_no`:
   ```js
   window.SF_DATA = {
     "SOR-XXXXX": { matches: [
       { account: "<name>",
         url: "https://trycentssf.lightning.force.com/lightning/r/Account/<Id>/view",
         strength: "strong" | "weak",
         matched_on: "address" | "email" | "phone" | "name" | combos,
         note: "<short human note: city/state, dealer?, Cents Location ID, verify parent, etc.>",
         cust_id: "<LW id if known, optional>",
         cents_location_id: "<if the SF location has one, optional>" }
     ]}
   };
   ```
   - For a **location-only** hit, use the `Custom_Location__c/<Id>/view` URL and note "open to
     see the parent account."
   - Orders you searched but found nothing for → `{ matches: [] }` (so the board shows "new,"
     not "not run").
   - Flag **dealer** phone/name hits and **🚩 Cents Location ID** in the note.

5. Do **not** edit `dedupe_board.html` or `dedupe_data.js` — only write `sf_data.js`.
