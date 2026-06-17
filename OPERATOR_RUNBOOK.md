# 2AUTO2MOOPS — AI Operator Runbook

> STOP. Read this ENTIRE file before responding to ANY message about orders or run.py.
> Domain knowledge is in CLAUDE.md. Deep reference in docs/reference.md.

## CRITICAL — How This Works (read first, every time)

Claude CANNOT run this code. It runs on Matt's local machine with Playwright controlling a real browser. There is no way around this. Claude's ONLY job is:

1. Give Matt the exact `python run.py ...` command to copy-paste into his local PowerShell terminal
2. Wait for Matt to paste back the terminal output
3. Interpret that output and give the next command or tell him what's done

That's it. Nothing else. Specifically:

- DO NOT run `python run.py` in the bash sandbox — Playwright needs a real browser, the sandbox has none
- DO NOT open MOOPS in Chrome tools — the script handles all browser interaction via Playwright
- DO NOT try to log into any website — the script uses Matt's existing browser session
- DO NOT ask "should I open the browser" or "want me to check" — just give the command
- DO NOT say "let me check" and then run bash commands — give the command to Matt

If Matt says "parts order 19697" the correct response is:

```
python run.py parts 19697
```

Not a paragraph. Not a question. The command. Then wait for output.

## Command shorthand (ALWAYS use this form)

Grammar: `python run.py <type> [touch] <id>`. Type is a single letter; touch is
`first`/`final` (required for system & route, omitted for parts/cards). Do NOT hand
Matt `--so-id`/`--parts-order` style flags -- always the shorthand:

| Say | Command |
|-----|---------|
| System first touch | `python run.py s first 19697` |
| System final touch | `python run.py s final 19697` |
| Route first / final | `python run.py r first 19697`  /  `r final 19697` |
| Parts/Readers | `python run.py p 19697` |
| Cards order | `python run.py c 19697`  (or `c 19697 SHORTNAME`) |
| Card modify | `python run.py m 19697` |
| Laundrylux stock | `python run.py ll 19697`  (VAC-only: hardware + per-location configs, cust 01643) |
| Read only | `python run.py read 19697` |
| Intake board | `python run.py intake` |
| Inspect one SOR | `python run.py inspect 27678` |

Letters: `s`=system, `r`=route, `p`=parts, `c`=cards, `m`=cardmod.
The old `--so-id ... --flag` form still works, but never give it to Matt. Shorthand only.
No `$env:PYTHONDONTWRITEBYTECODE` -- run.py already sets `sys.dont_write_bytecode`.

When Matt says "continue from where you left off" with no new info, say nothing — no response needed.

## Rule 1: Don't Ask — Determine

The script reads the SO and SOR. Don't ask Matt "is this a system order?" — read the output:

- VACs → `--first-touch`
- No VACs, just kits/parts → `--parts-order`
- Cards only → `--cards-order`
- Route (from Order Type) → `--first-touch` (auto-detects)

## Rule 2: Standard Flow

**Step 1 — Read:** `python run.py --so-id {ID}` (if Matt already told you the type, skip to Step 2)
**Step 2 — Run playbook:** Based on products in output or what Matt said
**Step 3 — Handle output:**

- EFS → "Open EFS console (Ctrl+Shift+J), type `allow pasting`, Ctrl+V, Enter"
- Slack → Matt posts generated message to #ops-moops-orders
- VUnics → Done after save
- First-touch → Remind: Work State → Placed, Accept SOR, notify Mark
- Cards (new design) → After card saved: `--add-card-to-so CARD-MD-X --save --card-email CARD-MD-X`
- Cards (reprint) → Pauses before Create PO, then PO email (clear CC), human sends

**Step 4 — Manual reminders:** Work State → Placed, Accept SOR, Required Date on SO, notify Mark (system).

### Final Touch (pre-ship audit)
`python run.py --so-id {ID} --final-touch`
- Reads task checklist, completes what it can (card email, card PO, ITF), flags blocked items
- System orders only. Run week before assembly.
- Tasks 7-10 currently flag for manual portal verification

## Individual Actions

```
--set-tag auto | "custom"     --add-part PART --qty N      --add-missing --read-sor
--add-splicers                --assembly-week YYYY-MM-DD   --set-tasks
--check-schedule              --clone-card SHORTNAME       --add-card-to-so CARD-MD-X
--card-email CARD-MD-X        --itf                        --save
```

## Never Do

- Run Python in the bash sandbox — Playwright needs a real browser
- Open MOOPS in Chrome tools — the script handles the browser
- Ask Matt info the script already reads
- Suggest manual steps the script handles
- Create POs — real money, human-confirmed only
- Automate login flows
- Give long explanations when a command is all that's needed

## Always Do

- Give exact copy-paste commands immediately — no preamble
- Interpret output and give next step immediately
- Know the codebase — read files to debug, don't guess
- Track what's done vs. remaining per order
- Regenerate EFS snippets if clipboard lost (see `core/efs.py`)
- When Matt gives an SO ID and order type, respond with the command — nothing else
