# 2AUTO2MOOPS

Human-guided automation for **Laundroworks (LW) order fulfillment in MOOPS**. You review each
order and press **Save**; the tool does the repetitive filling in between — tag, assembly-week
schedule, missing parts, customer/location/user provisioning, cards, config files, and the task
checklist.

**Fill-only by design.** Every step fills the MOOPS / Admin Portal / LaundroPortal forms and then
**pauses for a human to review and Save**. The tool never submits on its own. Real orders, real
money, real shipping — a person stays in the loop at every write. PO creation and payments are
always human-confirmed.

## How it works

| Layer | What |
|-------|------|
| Human review | You read the SOR/SO and decide; you Save each filled form |
| State / planning | The run snapshots the SO + SOR once and builds a task-driven plan |
| Execution | Python + Playwright drives a real Chrome against your logged-in session |

Playwright is the only dependency. Business logic is kept separate from page interaction.

## Setup

```bash
pip install playwright
playwright install chromium
```

The tool uses your existing browser session (`chrome_profile/`), so you must be able to reach
MOOPS (`moops.mitechisys.com`), the Admin Portal (`admintools.mitechisys.com`), and LaundroPortal
(`portal.mitechisys.com`) while signed in.

## Running

Launch the persistent console — the browser opens once and stays open across commands:

```bash
python run.py
```

You get a `2auto>` prompt. Point a command at a Sales Order id. The full menu prints on launch;
the main ones:

**Main runs**
- `system <id>` (`s`) — the idempotent system/laundromat run: snapshot the SO + SOR, then do
  whatever is still **To Do** — resolve the customer, tag, schedule, hardware, Portal location,
  user, Stripe (skipped for Fortis), intro email, cards, End-Customer link, config files, task
  checklist. Routes / Multi-Housing are auto-detected.
- `parts <id>` (`p`) — parts / readers order (EFS / VUnics / Slack).
- `cards <id> [name]` (`c`) — cards-only order.
- `cardmod <id>` (`m`) — card address/design change on an existing card.
- `ll <id>` — Laundrylux stock VAC order.

**Read / inspect**
- `read <id>` — read an SO · `intake` — scan the Submitted/In-Review queue → dedupe board + plan ·
  `history <id>` — what the tool recorded against an SO · `inspect <sor>` · `plan <id>` ·
  `schedule <id>`.

Add `-v` to any run for full step-by-step output. Each run ends with a **summary of what it
actually did**. Single-step workflow verbs (`createcust`, `addloc`, `adduser`, `apiuser`,
`stripe`, `intro`, `card`, `tasks`, `settasks`) are listed in the console menu.

**Operating model:** give the tool an SO id → it fills the steps, pausing for you to review and
Save → you read the output and continue. Console output is tee'd to `run.log`. Restart the console
(`quit` → `python run.py`) after any code change — it does **not** hot-reload.

## Project structure

```
2AUTO2MOOPS/
├── run.py                 — CLI + persistent `2auto>` console (verb dispatch + chain orchestration)
├── CLAUDE.md              — domain cheat sheet (VAC decoder, kits, task checklist, selectors, conventions)
├── OPERATOR_RUNBOOK.md    — how the operator + Claude work together
├── STATUS.md              — session log / current state
├── core/
│   ├── browser.py         — Playwright launch + navigation
│   ├── moops.py           — MOOPS page actions (tag, parts, cards, config, tasks…)
│   ├── schedule.py        — assembly-week capacity + FIFO picking
│   ├── efs.py             — EFS catalog, kit expansion, JS snippet builder
│   ├── portal.py          — Admin Portal / LaundroPortal reads
│   ├── provisioning.py    — create customer, API user, location, user, Stripe, intro
│   ├── dedup.py           — customer matcher (email > phone > name)
│   ├── order_plan.py      — pure task-driven planner
│   ├── shipping.py        — post-ship helpers
│   └── action_log.py      — append-only audit log (`action_log.jsonl`) + `history`
├── playbooks/             — first_touch, parts_order, cards_order, intake, laundrylux, salesforce
├── docs/                  — architecture, domain, lessons, idempotent-run design, reference
├── skills/moops-dedupe/   — read-only dedupe skill (Admin Portal + Salesforce)
└── chrome_profile/        — persistent browser session (gitignored)
```

## Working on this project in Claude (Cowork)

You can open this repo in the **Claude desktop app (Cowork mode)** to run orders and to update the
code or docs.

1. **Open the folder in Claude.** When prompted, pick the `2AUTO2MOOPS` folder (or use the folder
   picker to connect it). Claude automatically reads `CLAUDE.md` and `OPERATOR_RUNBOOK.md` for full
   project context.
2. **To run an order:** launch `python run.py` in your terminal yourself. Give Claude the SO id;
   it replies with the exact `2auto>` command to paste. Paste the output back — or tee it to
   `run.log` and just say **"run log"** — and Claude interprets it and gives the next step. Claude
   **cannot** run the browser automation itself (Playwright needs your real, logged-in Chrome); it
   gives commands and reads results.
3. **To change code or docs:** describe what you want. Claude reads the real files and edits them
   in place. **Restart the console after any code change** (`quit` → `python run.py`).
4. **To push updates to GitHub:** Claude hands you the `git` commands; you run them locally.

## Rules of the road

- Fill-only; never auto-submit. PO creation, Stripe merchants, and intro emails are human-confirmed.
- Don't automate login/auth flows.
- Keep decision logic out of the page-interaction layer (NetSuite migration is planned).
- Read the real code and the live page before changing selectors — never guess.
