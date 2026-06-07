# 2AUTO2MOOPS — handoff snapshot (2026-06-04)

Distilled current state for a fresh chat. (Read `OPERATOR_RUNBOOK.md` + `CLAUDE.md` first;
full SF spec in `docs/salesforce.md`.)

## How it works
Human-guided Playwright automation for Laundroworks order fulfillment in MOOPS. **Claude
gives `python run.py` commands; Matt runs them locally** (Claude never drives the browser).
**"run log" = read `run.log`** (only has output Matt tees in, or via Start-Transcript on the
persistent `2auto>` console). Never claim something is tested/working without Matt's stated
result. Restart the console after code edits (no hot-reload).

## Main command — `s <id>` (the system run)
ONE idempotent run, first AND second touch (no separate command). `_do_system` → always
`first_touch.run(no_itf=True)` + `_post_first_touch` (the provisioning chain). Idempotency =
per-step gating:
- Reads checklist first; **task 1 (Hardware) Completed ⇒ skip parts**; tag/schedule are
  check-or-skip; customer resolution **creates the customer if missing**; chain runs only
  To-Do tasks.
- Chain order: customer (API user + Stripe feature) → location → **user+intro** → **Stripe**
  (skipped for Fortis/EBT) → cards → **VAC config files (task 9)** → End Customer on SO →
  task checklist (1,2 + 7/8/9/10 from what actually ran + card lane).
- All fill-only; Matt saves at each pause.

## Working (built this session)
- Idempotent `s <id>`; existing-customer gap-fill (API user/Stripe).
- Location: Access-Sharing → 01 (grouped) vs 02 (new group) series; ONE-PASS (no SO re-nav —
  seats threaded, address from the SOR read).
- User + **intro email clicks the OK confirm dialog** (was leaving "Not sent"). ONE-PASS.
- Stripe: confirm → JS-click "Add New Merchant" → refresh in place → grant account access;
  **skipped entirely for Fortis/EBT**.
- Cards decision tree: new design (clone+email), reprint (Create PO + PO email), exists
  (no duplicate clone), none → task states 3/4/5 set accordingly.
- **VAC config files (task 9)**: per-VAC "Get Config" download (Chrome blocks .cfg; Playwright
  captures it), one per VAC UNIT, patch the **KioskName** line to VAC0n, save `VAC0n.cfg`,
  upload to File Resources.
- Task checklist derived from completed workflows.
- Intake board shows VAC count / reader count / required date / proposed schedule + dedup
  candidates with what they matched on + a "This order" contact block.
- Dedup (Admin): email > phone > last-name > name; SOR "Existing End Customer" field is
  authoritative.

## Pending / next
- **Dealer-record link (MOOPS/Admin)** — a NEW customer must be added to the dealer record
  before its cust id can be set as End Customer on the SO. Currently MANUAL; the run flags it
  ("add <cust_id> to the dealer record"). This is what blocks the cust id landing on the SO.
  Needs an `inspect-form` of the dealer page. **Highest-value next build.**
- **Salesforce — `sf <id>` (standalone, NOT in `s`)** — Playwright UI only (no connector, no
  REST API). Built: order→plan + IT email + dedupe (sf-search typeahead, address-first). New-
  form CREATE fills are STUBBED pending `inspect-form` of New Account / Location
  (Custom_Location__c) / Contact / Opportunity. Spec: `docs/salesforce.md`. Completes task 6,
  then IT email + reassign opp owner to Mark. **Okta SSO is aggressive** — run in the kept-open
  authenticated console; guard for `cents.okta.com` bounces.

## Key decisions / constraints
- Pipeline: dedupe lives in **intake** (Admin + SF candidates); SF is the **last** step
  (after MOOPS), treated like creating an LP account. Only **dedupe + card proofs** are
  non-automated. SF dedupe signal: **address first, then email**.
- No SF connector/REST API → SF is Playwright on Lightning (shadow-DOM; inspector pierces it).
- **Dev quirk:** the OneDrive mount serves the sandbox **truncated** copies of just-edited
  files, so `py_compile` fails on unrelated lines — rely on the file tools + Matt's local run.

## Code map (key)
`run.py` (CLI/verbs + `_do_system`, `_do_provision_chain`, `_do_addloc/_do_adduser/_do_cards/
_do_config_files`), `playbooks/first_touch.py` (the `s` playbook), `playbooks/salesforce.py`
(standalone SF), `core/moops.py` (page actions, incl. VAC config + End Customer + tasks),
`core/provisioning.py` (create-customer, API user, location, user, Stripe, intro, inspectors),
`core/portal.py` (Admin/LP, dedup scrape), `core/dedup.py` (matcher), `playbooks/intake.py`.
