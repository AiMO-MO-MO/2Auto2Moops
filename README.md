# 2AUTO2MOOPS

Human-guided operational workflow automation for Laundroworks order fulfillment.

## Architecture

Humans decide → Python manages state → Playwright executes → Browser is just a tool.

- **Layer 1:** Human reviews SOR/SO, picks a playbook
- **Layer 2:** State tracker (CSV/SQLite) manages the work queue
- **Layer 3:** Playwright executes predefined playbooks against SO IDs

See `docs/architecture.md` for full details.

## Playbooks

| Playbook | Order Type | Status |
|----------|-----------|--------|
| Card Order — New Design | Cards Only | Building |
| Card Order — Reprint | Cards Only | Planned |
| Parts Order | Parts/Readers Only | Planned |
| System Order — First Touch | Laundromat System | Planned |
| System Order — Finalization | Laundromat System | Planned |

## Setup

```bash
pip install playwright
playwright install chromium
```

## Usage

```bash
# Phase 1: Single SO test
python run.py --so-id 19472 --playbook card_new_design

# Phase 2: Dry run (no real actions)
python run.py --so-id 19472 --playbook card_new_design --dry-run

# Phase 3: Live execution
python run.py --so-id 19472 --playbook card_new_design --live
```

## Project Structure

```
2AUTO2MOOPS/
├── run.py                  — Entry point
├── CLAUDE.md               — Full project context
├── README.md
├── playbooks/              — Predefined workflow scripts
│   ├── card_new_design.py
│   ├── card_reprint.py
│   ├── parts_order.py
│   └── system_first_touch.py
├── core/
│   ├── browser.py          — Playwright session management
│   ├── moops.py            — MOOPS page actions (navigate, click, fill)
│   ├── queue.py            — SO queue / state tracker
│   └── logger.py           — Step-by-step execution logging
├── docs/
│   └── architecture.md     — Full architecture doc
└── chrome_profile/         — Persistent browser session
```

## Development Rules

- Don't start from dashboards
- Don't automate login/auth flows
- Don't mix decision logic inside Playwright
- Don't batch until single-order works perfectly
- Test Phase 1 → Phase 2 → Phase 3 → Phase 4 in order
