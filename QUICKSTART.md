# 2AUTO2MOOPS - Operator Quick Start

## Setup (one time)

```
cd 2AUTO2MOOPS
pip install -r requirements.txt
playwright install chromium
```

First run opens a browser — log into MOOPS manually. The session is saved in `chrome_profile/` and reused on future runs.

## Common Commands

### System Order (full first-touch)
```
python run.py --so-id 19700 --first-touch
```
Does everything: reads SOR, picks assembly week, sets tag, adds parts (CARD-03-01, SVC, paper, pinpad kit, splicers), analyzes missing parts, saves, handles card workflow if new design, opens ITF form. Pauses before save and before card operations for your review.

Override assembly week: `--first-touch --assembly-week 2026-07-06`

### Parts/Readers Order
```
python run.py --so-id 19700 --parts-order
```
Reads the SO, figures out if products go to EFS (3PL), VUnics (warehouse), or Slack (SF fulfillment). Sets tag, sets shipment, handles -DS swap for EFS, generates JS clipboard snippet or Slack message.

### Check Schedule
```
python run.py --so-id 19700 --check-schedule
```
Shows weighted VAC capacity per week. 35 = soft cap, 45 = hard max.

### Cards Only Order
```
python run.py --so-id 19700 --cards-order
```
Sets tag, Order Type = Cards Only, Shipment = Drop ship / Card Supplier, saves, then runs card workflow (clone, add to SO, design email) for new designs. Pass a specific shortname: `--cards-order THELNDRY`

### Individual Actions (compose as needed)
```
python run.py --so-id 19700 --set-tag auto
python run.py --so-id 19700 --add-part 03-01-34 --qty 2
python run.py --so-id 19700 --add-missing --read-sor
python run.py --so-id 19700 --assembly-week 2026-07-06
python run.py --so-id 19700 --set-tasks
python run.py --so-id 19700 --clone-card THELNDRY
python run.py --so-id 19700 --add-card-to-so CARD-MD-THELNDRY --save --card-email CARD-MD-THELNDRY
python run.py --so-id 19700 --itf
python run.py --so-id 19700 --save
```

## What Each Order Type Needs

| Type | Command | What it does |
|------|---------|-------------|
| System - Laundromat | `--first-touch` | Full playbook: tag, assembly week, parts, cards, ITF |
| Parts/Readers Only | `--parts-order` | Tag, shipment routing (EFS/VUnics/Slack) |
| Laundry Cards | `--cards-order` | Tag, drop ship, card clone + design email |
| Route (Multi-Housing) | `--first-touch` (auto-detects) | Lighter playbook: no ITF, no CARD-03-01, no SVC |

## EFS Orders (parts going to 3PL)

After `--parts-order` saves, a JS snippet is copied to your clipboard.
1. Open EFS in your browser: https://fcp.efulfillmentservice.com
2. Go to New Order (clientID=4612)
3. Press F12 -> Console tab
4. Ctrl+V to paste the JS snippet
5. Press Enter — form fills automatically
6. Click "Verify Order" and review, then Submit

## File Structure

```
run.py              <- CLI entry point (thin dispatcher)
core/
  browser.py        <- Playwright browser launch + navigation
  moops.py          <- All MOOPS page actions (read/write products, save, tasks, cards, ITF)
  schedule.py       <- Assembly week scheduling (capacity, FIFO picking)
  efs.py            <- EFS product catalog, JS snippet builder, shipping mapping
playbooks/
  first_touch.py    <- System order first-touch playbook
  parts_order.py    <- Parts/readers order playbook
  cards_order.py    <- Cards-only order playbook
assets/
  Placeholder.png   <- Card placeholder image (used during clone)
```

## Key Contacts

- **Matt** - Built this, knows the full workflow
- **Oleg Stepanov** - Reader kit mappings, DB config
- **Marc Mullings** - Install troubleshooting
- **Mark** - SF account dedup, creates opportunity + case after first touch
- **Andrew** - Route order management (Jira)
