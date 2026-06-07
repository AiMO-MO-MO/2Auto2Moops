# Idempotent System Run — Design (for review)

> Goal: collapse first-touch / final-touch into **one** task-driven run, `system <id>`,
> that is **safe to re-run anytime**. Same 10 tasks as today — the only change is *how*
> they're initiated: the run **does** them (no-ITF: we provision), it never delegates to
> the ITF Jira form. Every step is **check-or-skip**: do it only if it isn't already done.
>
> This replaces the first/final split. The old ITF-era `final-touch` is **retired** (no ITF
> escape hatch).

## The objective

Convert a sales-order request into a **production-ready, buildable order** so the customer
can power the system on and start using it. "Done" = the 10 tasks complete. **One touch
finishes the whole order** *unless* it's waiting on a **new card design** — that's the only
real-world wait. The machine's job is to do everything it *can* on each pass and never redo
what's done.

**One touch completes everything when:** we already have the card image, it's a reorder /
reprint (existing design), or there are no cards. **A pending new card design** is the only
thing that forces a second pass — it blocks the card tasks and the config file until the
art is approved.

## Lifecycle (the big picture)

```
   ┌──────────┐     ┌──────────┐      ┌─────────────────────┐      ┌──────────────┐
   │  INTAKE  │ ──▶ │ DETERMINE│ ──▶  │   INITIATE + MAIN   │ ──▶  │  LOOK-BACK   │
   │ + DEDUPE │     │ (human)  │      │   WORKFLOW (run)    │      │  (re-run)    │
   └──────────┘     └──────────┘      └─────────────────────┘      └──────────────┘
   read-only        HTML board        idempotent system run        same run again,
   scan SORs,       new vs existing   do everything possible       finishes what the
   dedupe each      pick the account  in one pass                  dependencies unblocked
```

1. **Intake (read-only, batch).** Scan the SOR queue as a **batch**; **system orders only**
   for now. For each SOR, return **all possible matches** — not a single verdict — with the
   signal a human needs to investigate: matched cust id(s), each candidate's own contact, and
   city/state, on the **HTML board**. Intake never auto-picks; it surfaces everything and the
   human decides.
2. **Determine + initiate (human gate).** Matt reads the board, decides new vs existing, and
   **creates the System order**. *A System SO existing IS the go signal* — that's the human
   saying "this one's good to run." Intake and the workflow stay **decoupled**; the human
   bridges them by making the SO. The new/existing call is expressed on the SO itself:
   End Customer **set** = existing (the run reuses it), **blank** = new (the run creates it).
3. **Main workflow.** Run `system <id>` against the SO — do every task the dependencies
   currently allow, in one pass.
4. **Look-back (re-run).** Later, once the card design is approved, run `system <id>`
   **again** (manually). It restarts the same workflow and completes the remainder
   **without overriding anything already done**.

> **Handoff (decided):** the bridge from intake to the run is **manual** — a System SO
> existing is the human's go signal, and the dedupe determination is carried by the SO's
> End Customer field (set = existing, blank = new). No `intake_plan.json` consumption needed
> for now; the run reads the SO. This keeps intake and the workflow cleanly separate.

The whole reason steps 3 and 4 are the *same run* is the check-or-skip design below: there
is no separate "finish-up" playbook — you just re-run the main workflow.

## Dependencies — why it isn't always one touch

There is **one** cross-time wait: a **new card design awaiting artwork/approval**. While an
order waits on new card art it can't finish the **card tasks (3, 4, 5)** or the **config
file (task 9)** — the config needs the final card image. That, and only that, forces a
look-back pass.

Everything else is **same-pass**, handled by ordering the steps within one run:

| Step | Needs first | Note |
|------|-------------|------|
| API user + Stripe feature (Admin) | customer exists | set on the Cust ID |
| Location (LaundroPortal) | customer exists | |
| Portal user | location | |
| **Stripe** (payment processing, task 7) | Stripe feature on Cust ID + location + **portal user** | **after the user** — the user is assigned bank / account access. Configurable in the same pass; just needs a human to do the step. Not a time wait. |
| Intro email (task 10) | portal user saved | |
| Card add / ownership | customer exists | |

So: **card image in hand → reorder/reprint → no cards** all finish in one touch. A pending
**new card design** is the lone reason to come back.

## Principles

1. **Read once.** One SO read, one SOR read, one task-list read per run. No step re-reads.
2. **Check-or-skip every step.** Detect current state; act only on the gap. Re-running a
   finished order should change nothing and say so.
3. **Never overwrite.** Tag, assembly week, parts, customer link, location — fill if
   missing, leave alone if present.
4. **Fill-only / human saves.** Unchanged: the run fills and pauses; you save.
5. **No ITF.** Task 6 and 7–10 are completed by our own provisioning, never by opening
   the Jira form.

## Run order + skip conditions

| # | Step | Do it when… | SKIP when… |
|---|------|-------------|------------|
| 1 | Read SO + SOR + tasks | always (once) | — |
| 2 | **Tag** | tag field empty | tag already set |
| 3 | **Assembly week** | week field empty | week already set |
| 4 | **Parts** (rule-based + missing) | part not on SO | part already on order |
| 5 | **Customer resolve** | see Customer resolution below | End Customer already a real cust id |
| 6 | **API user (POS) + Stripe feature** (Admin) | customer has no API user | API user exists |
| 7 | **Location** (LaundroPortal) | customer has no matching location | location already exists |
| 8 | **Portal user** (LaundroPortal) | no portal user | user exists |
| 9 | **Stripe** (LaundroPortal) | location made + portal user exists, no merchant | merchant already present |
| 10 | **Intro email** (Admin) | user saved, intro not sent | intro already sent |
| 11 | **Card** | New-design card on SOR + no `CARD-MD-*` on SO | card already on SO, or no card on SOR |
| 12 | **End Customer on SO** | cust id / location not linked on SO | already linked |
| 13 | **Task checklist** | always (derive from real state) | — |

Order rationale: customer exists before anything is owned by it; **user before Stripe** (the
user gets assigned bank / account access during Stripe setup); card after the cust id (so
it's owned correctly); End Customer + task checklist last (so they reflect everything).

## Pause points (human checkpoints)

The run is fill-only — it never submits. It **pauses for you to review and save** at each
write. We keep more pauses now and **remove them as the run proves out** (a pause level /
flag, not code edits each time). The irreversible ones stay the longest.

| Checkpoint | Type | Why |
|------------|------|-----|
| **Create Customer — pause BEFORE you save** | **HARD** | once the Cust ID is saved you can't delete it — verify before committing |
| Location filled — pause before save | soft | confirm street / city / state / location id |
| Card part added — **the run saves it to the SO** | (save) | the card must persist on the order before email / PO |
| **Before creating the card PO — pause** | **HARD** | the PO goes to the supplier; confirm first |
| Stripe — inherent pause | (manual) | you do the assign-bank-access step by hand anyway |
| End Customer on SO — pause before save | soft | money-path link; confirm cust id + location |
| Intro email — pause before send | soft | review recipient before it goes out |

**HARD** = stays until explicitly turned off (irreversible / money). **soft** = removed as we
get comfortable. Most of these pauses already exist in the current chain functions; the run
just keeps invoking them through a single "pause level" setting.

## Customer resolution (Step 5)

In priority order:
1. **End Customer already set** on the SO (a real cust id, not `00000`-style placeholder)
   → use it; this is an existing/repeat customer. Don't create.
2. **Replacement/exchange** — notes reference another SO → inherit that SO's End Customer
   (verify mode: location already exists, provision nothing new).
3. **Dedup STRONG** (email/phone exact) → use the matched cust id. WEAK (name only) →
   surface candidates with contact + city/state, **pause for you to decide** (don't auto-pick).
4. **None** → create the customer (guard: never create a blank-name customer).

## Task-state rules (no-ITF) — derive, don't assume

| Task | Completed when | To Do when | N/A when |
|------|----------------|-----------|----------|
| 1 Hardware verified | always (pre-done) | — | — |
| 2 End-customer info | always (pre-done) | — | — |
| 3 Connected w/ end-customer | card design email in SO log | New-design card, no email yet | no card on order |
| 4 Card approval | card not PLACEHOLDER | card still PLACEHOLDER | no card |
| 5 Card proofs / PO | PO created | approved, no PO yet | no card |
| 6 SaaS / contract | *(Salesforce — see Next phase; NOT auto-completed yet)* | stays To Do until the SF track is built | — |
| 7 Payment-processing | Stripe merchant configured | not yet | — |
| 8 Location in Portal | location added in LP | not yet | — |
| 9 VAC config files | config file uploaded to SO | **waiting on card artwork** (new card) / not uploaded | — |
| 10 Portal user + intro | user created + intro sent | not yet | — |

Routes: 3–8,10 = N/A, only 9 in play (no ITF, no provisioning) — unchanged from today.

## What stays manual (flagged, not done)

- **Task 6 SaaS contract** — lives in **Salesforce**; not in the current flow. Stays To Do
  until the SF track (Next phase). The run leaves it alone.
- **Task 9 config file** — waits on the card image for new-design orders; never auto-completed.
- **Dealer-record link** — add the new customer to the dealer's MOOPS record (new customers only).
- **Saves** — every fill still pauses for you.
- **Stripe merchant choice** — New-vs-Existing is a judgment call; we initiate, you choose.

## Detection sources (how "already done" is read)

- Tag / assembly week / parts → SO page fields + product table.
- Customer / API user / intro status → Admin customer page (`read_admin_portal`).
- Location / Stripe merchant / portal user → LaundroPortal (per-customer, read-only).
- Card state → product table (`CARD-MD-*`, PLACEHOLDER = unapproved) + SO log (email sent).
- End Customer link → SO End-Customer validity widgets.

## Open questions for you

1. ~~Task 6 meaning.~~ **DECIDED:** task 6 (SaaS contract) lives in **Salesforce**, not our
   Admin/LP flow. The run does **not** auto-complete it — it stays To Do until the SF track
   (Next phase) is built.
2. ~~Final-touch.~~ **DECIDED:** retire it — no ITF escape hatch needed.
3. ~~Weak-dedup gate.~~ **DECIDED:** no gate. Intake returns **all possible matches** per
   order for the human to investigate; the human's decision is making the SO (End Customer
   set/blank). No confidence threshold, no auto-pick.
4. ~~LP-read cost.~~ **DECIDED (default):** read per run; no session cache for now — keep it simple.
5. ~~Dry preview.~~ **DECIDED (default):** no separate `--dry` verb; the run reports
   skip/do per step as it goes.
6. ~~Intake → run handoff.~~ **DECIDED:** manual — a System SO existing is the go signal;
   the new/existing call rides on the SO's End Customer field (set = existing, blank = new).
   The run reads the SO; intake and the workflow stay decoupled. No plan consumption for now.
7. ~~Look-back trigger.~~ **DECIDED:** manual re-run once the card design is approved. No
   re-surfacing lane for now.

**All open questions resolved — design is locked.** Build per the order below.

## Reuse map — this is orchestration, not new step logic

Every step already exists. The idempotent run = **read state → decide do/skip → call the
existing function → derive task states**. The only genuinely new code is the skip-detection
and the task-state derivation; the actions are all reused.

| Step | Reuse |
|------|-------|
| Tag | `build_tag`, `action_set_tag` |
| Schedule | `planned_week_for_sor` / `read_schedule_capacity` + `pick_assembly_week`, `action_set_assembly_week` |
| Parts | `action_add_required_parts` (already skips queued) |
| Customer resolve | `read_existing_customer_id`, `find_reference_so` + `read_so_end_customer`, `dedup.match_customer`, `provisioning.fill_create_customer` / `next_customer_id` |
| API user + Stripe feature | `provisioning.fill_api_user` (skips if exists), `check_customer_setup` |
| Location | `provisioning.fill_location`, `next_location_id` |
| Portal user | `provisioning.fill_user` |
| Stripe | `provisioning.open_stripe` |
| Intro | `provisioning.send_intro_email` |
| Card | `_do_cards` (`clone_temp_card` + `action_add_card_to_so` + `open_card_design_email`) |
| End Customer | `set_so_end_customer` |
| Tasks | `read_task_states`, `action_set_system_tasks` / `set_task_checklist` |
| Detection | `read_admin_portal`, `current_portal_customer`, `customer_location_summary`, product table, SO log |

The current `_do_provision_chain` + `first_touch.run(no_itf=…)` are most of the skeleton —
the work is turning their always-do steps into check-or-skip and deriving task states from
what the detectors find, **not** rewriting the steps.

## Next phase — Salesforce (not now, but it's where this goes)

The current flow provisions in **Admin Portal + LaundroPortal**. The **SaaS contract (task 6)
lives in Salesforce**, and we haven't built any SF automation yet. Next track:

1. **Dedupe across Admin *and* Salesforce.** Same matcher (`dedup.match_customer`), a second
   scraper for SF (browser-only, no REST). Surface both sources on the board so the
   new/existing call considers SF accounts too.
2. **Find-or-create account + location in SF** — mirror what we do in Admin Portal
   (resolve existing → reuse; else create), then the SaaS-contract piece can complete and
   task 6 moves from manual to done.

Out of scope for the idempotent-run build below; called out so the task-6 gap is intentional,
not forgotten. The run is designed so adding the SF step later is just another check-or-skip
node + a task-state rule — no rework.

## Build order (once approved)

1. Tag + schedule check-or-skip into the main run (move from final-touch). *(small, testable)*
2. Card step: add-if-needed / skip-if-present, inside the run.
3. Chain steps: location & Stripe & user skip-if-exists detection.
4. Task checklist: derive states from real provisioning (replace the fixed first-touch map).
5. End Customer link + dealer-record flag.
6. Retire/relabel final-touch.
