"""
action_log.py -- append-only audit trail of what each run actually did.

`run.log` only holds the CURRENT run (it's the live tee to Matt's terminal, overwritten each
run), so there's no durable history. This module appends ONE JSON line per completed run to
`action_log.jsonl` in the project root -- a permanent, reviewable record of every write the tool
made: which SO, what type of run, the write actions, and the key ids it created (customer,
location, PO, card part, intro email, Stripe merchant).

SAFEGUARD CONTRACT: logging must never break or slow a run. Every call is wrapped so any failure
(disk, permissions, OneDrive lock) is swallowed with a notice -- the run continues regardless.
This module NEVER reads/writes MOOPS or changes any behavior; it only records.

One line per run keeps it greppable: `grep 20191 action_log.jsonl` shows everything done to that SO.
"""

import datetime
import json
import os

# Project root (one level up from core/). Sits next to run.log / vac_configs.
ACTION_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "action_log.jsonl"
)


def append_action_log(so_id, run_type, actions=None, **extra):
    """Append one run record to action_log.jsonl. Never raises.

    so_id     -- the Sales Order id the run touched (str/int)
    run_type  -- 'system' | 'route' | 'parts' | 'cards' | 'card-modify' | 'laundrylux' | ...
    actions   -- list of human-readable write-action strings (the run's "DID:" lines)
    extra     -- any key ids worth keeping: customer, location, location_key, po, card_part,
                 card_result, intro_email, stripe_merchant, tag, ship, missing, etc.
                 Empty/None values are dropped so records stay compact.
    """
    try:
        rec = {
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "so_id": str(so_id) if so_id not in (None, "") else "",
            "run_type": run_type,
            "actions": list(actions or []),
        }
        for k, v in extra.items():
            if v not in (None, "", [], {}):
                rec[k] = v
        with open(ACTION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # logging must never break a run
        print(f"[action-log] could not append ({e}) -- run unaffected.")


def read_action_log(so_id=None):
    """Return ledger records (list of dicts), newest first. If so_id is given, only that SO's
    records. Read-only; returns [] if the log doesn't exist yet or can't be read."""
    want = str(so_id).strip() if so_id not in (None, "") else None
    recs = []
    try:
        if not os.path.exists(ACTION_LOG):
            return []
        with open(ACTION_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if want is None or str(r.get("so_id", "")).strip() == want:
                    recs.append(r)
    except Exception as e:
        print(f"[action-log] could not read ({e}).")
        return []
    recs.reverse()  # newest first
    return recs


def print_history(so_id):
    """Pretty-print every run the tool recorded against an SO (newest first)."""
    recs = read_action_log(so_id)
    if not recs:
        print(f"[history] No recorded runs for SO-{so_id} in the action log "
              f"(only runs after the log was added are captured).")
        return
    print(f"\n=== Action history for SO-{so_id} ({len(recs)} run(s), newest first) ===")
    for r in recs:
        print(f"\n  {r.get('ts', '?')}  --  {r.get('run_type', '?')}"
              + (f"  (customer {r['customer']})" if r.get("customer") else "")
              + (f"  [{r['flavor']}]" if r.get("flavor") else ""))
        for a in r.get("actions", []):
            print(f"      - {a}")
        for k, v in r.items():
            if k in ("ts", "run_type", "so_id", "actions", "customer", "flavor"):
                continue
            print(f"      · {k}: {v}")
    print("\n  (Tool actions only -- excludes manual edits made in MOOPS after the run.)")
