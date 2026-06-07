"""
Assembly week scheduling — capacity reading, FIFO picking, date parsing.

Capacity rules:
  - 45 VACs/week hard max
  - 35 VACs/week soft cap (leave 10 for EXPEDITED/emergency)
  - Weights: VAC01-06 = 0.5, VAC07-08 = 1.0 (touchscreen = full slot)
  - FIFO: first available week with room under soft cap
  - EXPEDITED orders can use the 35-45 emergency buffer
  - Required date: work backwards (delivery date minus ~2 weeks shipping)
"""

import json
import os
import re
from datetime import datetime, timedelta

from core.moops import decode_vac

_INTAKE_PLAN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intake_plan.json")


def planned_week_for_sor(sor_id):
    """Return the assembly week date (YYYY-MM-DD) intake precomputed for this SOR,
    or '' if none. Lets first-touch reuse the intake scheduling decision instead of
    re-reading capacity on every order."""
    if not sor_id:
        return ""
    try:
        with open(_INTAKE_PLAN, encoding="utf-8") as fh:
            plan = json.load(fh)
    except Exception:
        return ""
    for o in plan.get("orders", []):
        if str(o.get("sor_id")) == str(sor_id) and o.get("assembly_week_date"):
            return o["assembly_week_date"]
    return ""


def calculate_order_weight(products: list) -> float:
    """
    Calculate weighted VAC slots for an order.
    VAC01-06 = 0.5, VAC07-08 = 1.0 per unit.
    Accepts products with key 'part_number' or 'part'.
    """
    total = 0.0
    for p in products:
        pn = (p.get("part_number") or p.get("part", "")).upper()
        qty = int(p.get("qty", 0)) if str(p.get("qty", 0)).isdigit() else 0
        if pn.startswith("VAC"):
            d = decode_vac(pn)
            weight = 1.0 if d["is_touchscreen"] else 0.5
            total += qty * weight
    return total


def parse_week_monday(week_label: str) -> str | None:
    """
    Parse a schedule week label like 'June 1 - June 7' into Monday date (YYYY-MM-DD).
    The first date in the range is the Monday.
    """
    match = re.match(r'(\w+)\s+(\d+)', week_label.strip())
    if not match:
        return None

    month_str, day_str = match.group(1), match.group(2)
    now = datetime.now()

    # Parse month name (try abbreviated then full)
    month_num = None
    for fmt in ["%b", "%B"]:
        try:
            month_num = datetime.strptime(month_str, fmt).month
            break
        except ValueError:
            continue
    if month_num is None:
        return None

    try:
        parsed = datetime(now.year, month_num, int(day_str))
    except ValueError:
        return None

    # If >90 days in the past, assume next year
    if parsed < now - timedelta(days=90):
        parsed = parsed.replace(year=now.year + 1)

    return parsed.strftime("%Y-%m-%d")


def pick_assembly_week(schedule: list, required_date: str = None,
                       is_expedited: bool = False, order_weight: float = 0.0):
    """
    Auto-pick the best assembly week.

    Capacity tiers:
      - 30 = soft cap (FIFO target — move to next week once hit)
      - 40 = yellow zone (emergency / expedited buffer)
      - 45 = hard max

    Returns (monday_date_str, week_label, reason) or (None, None, reason).
    """
    cap = 45 if is_expedited else 30

    available = []
    for wk in schedule:
        monday = parse_week_monday(wk["week"])
        if not monday:
            continue
        remaining = cap - wk["total"]
        if remaining >= order_weight:
            available.append((monday, wk["week"], remaining, wk["total"]))

    if not available:
        return None, None, "No weeks with enough capacity found"

    today = datetime.now().date()

    if required_date:
        try:
            req = datetime.strptime(required_date, "%Y-%m-%d").date()
        except ValueError:
            return None, None, f"Could not parse required_date: {required_date}"

        target = req - timedelta(weeks=2)

        best = None
        for monday_str, label, remaining, current_load in available:
            monday_dt = datetime.strptime(monday_str, "%Y-%m-%d").date()
            if monday_dt <= target and monday_dt >= today:
                if best is None or monday_dt > datetime.strptime(best[0], "%Y-%m-%d").date():
                    best = (monday_str, label, remaining, current_load)

        if best:
            return best[0], best[1], (
                f"Required delivery {required_date}, ship by ~{target}, "
                f"week {best[1]} has {best[3]:.0f}/{cap} ({best[2]:.0f} slots open)"
            )

        future = [(m, l, r, c) for m, l, r, c in available
                  if datetime.strptime(m, "%Y-%m-%d").date() >= today]
        if future:
            earliest = min(future, key=lambda x: x[0])
            return earliest[0], earliest[1], (
                f"WARNING: No week on or before target {target} with capacity. "
                f"Earliest available: {earliest[1]} ({earliest[3]:.0f}/{cap})"
            )
        return None, None, "No available weeks on or after today"

    else:
        # FIFO — first available week, at least next week
        target_earliest = today + timedelta(days=7)
        for monday_str, label, remaining, current_load in sorted(available, key=lambda x: x[0]):
            monday_dt = datetime.strptime(monday_str, "%Y-%m-%d").date()
            if monday_dt >= target_earliest:
                return monday_str, label, (
                    f"FIFO — first available week under {cap}. "
                    f"Week {label} has {current_load:.0f}/{cap} ({remaining:.0f} slots open)"
                )
        for monday_str, label, remaining, current_load in sorted(available, key=lambda x: x[0]):
            monday_dt = datetime.strptime(monday_str, "%Y-%m-%d").date()
            if monday_dt >= today:
                return monday_str, label, (
                    f"No week 4+ weeks out available. "
                    f"Earliest: {label} ({current_load:.0f}/{cap})"
                )
        return None, None, "No available weeks found"


def print_schedule(schedule: list) -> None:
    """Print schedule capacity table to stdout."""
    print(f"\n{'Week':<30s} {'Weighted VACs':>14s}  Status")
    print("-" * 60)
    for wk in schedule:
        total = wk["total"]
        if total >= 45:
            status = "FULL (45)"
        elif total >= 40:
            status = "RED — emergency only"
        elif total >= 30:
            status = "YELLOW — at soft cap"
        else:
            status = f"{30 - total:.0f} slots to cap"
        print(f"{wk['week']:<30s} {total:>10.1f}/45   {status}")
