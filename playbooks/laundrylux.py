"""
Laundrylux stock VAC order -- single run (NOT the provisioning chain).

Laundrylux (the dealer) buys VACs in bulk for stock. We only do HARDWARE + CONFIG FILES, all
owned by the stock customer 01643 ("Laundrylux (Stock VACs)"). Convention (Matt):
  - Orders are VAC-only; no customer/user/Stripe/cards/portal provisioning.
  - 2 VACs per location, cycling 0100001, 0100002, 0100003, ...
  - KioskName restarts at VAC01/VAC02 within each location.
  - The .cfg pulls LOCATION-SPECIFIC data, so we re-link the End Customer location on the SO and
    RE-DOWNLOAD for each pair -- we don't just patch the LocationID in one file.

Steps:
  1. Tag + assembly week + missing hardware companion parts (task 1). Save.
  2. For each location k: set End Customer 01643 + location 010000k, save, download that pair's
     2 configs (VAC01/VAC02 for that location).
  3. Upload all configs to the SO File Resources, save, verify.
  4. Mark tasks 1 & 9 Completed; the provisioning tasks (2-8, 10) are N/A for a stock VAC order.

Human still SAVEs nothing extra here -- the End-Customer link auto-saves like the system run.
This is a single run, intentionally decoupled from `system <id>`.
"""

import math
import os
from pathlib import Path

from core.browser import navigate_to_so
from core.moops import (
    read_products,
    read_customer_name,
    read_sor_data,
    read_schedule_capacity,
    read_config_file_resources,
    build_tag,
    action_set_tag,
    action_set_assembly_week,
    action_add_required_parts,
    set_so_end_customer,
    download_location_vac_configs,
    upload_files_to_so,
    set_task_checklist,
    save_so,
    _clear_customer_id_if_blocking,
)
from core.schedule import calculate_order_weight, pick_assembly_week, print_schedule

LL_STOCK_CUST_ID = "01643"            # Laundrylux (Stock VACs)
FIRST_LOCATION_BASE = 100001          # -> "0100001", "0100002", ...
VACS_PER_LOCATION = 2


def _location_id(index: int) -> str:
    """0-based location index -> '0100001', '0100002', ... (7-char MOOPS location id)."""
    return f"0{FIRST_LOCATION_BASE + index:06d}"


def run(page, so_id):
    print("\n" + "=" * 60)
    print(f"  LAUNDRYLUX STOCK VAC ORDER -- SO-{so_id}")
    print("=" * 60)

    navigate_to_so(page, so_id)
    customer_name = read_customer_name(page) or "Laundrylux"
    products = read_products(page)
    sor = read_sor_data(page)

    # Flat list of VAC units in product-table order (expand each VAC row by qty).
    units = []
    for p in products:
        pn = (p.get("part_number") or "").upper()
        if pn.startswith("VAC"):
            try:
                qty = int(p.get("qty") or 1)
            except Exception:
                qty = 1
            units.extend([p["part_number"]] * max(qty, 1))
    if not units:
        print("[LL] No VAC parts on this order -- nothing to do (Laundrylux orders are VAC-only).")
        return
    n_locations = math.ceil(len(units) / VACS_PER_LOCATION)
    print(f"[LL] {len(units)} VAC unit(s) -> {n_locations} location(s), {VACS_PER_LOCATION} per location.")

    # Current tag / assembly week (check-or-skip; don't overwrite if already set).
    cur_tag = (page.locator('input[name="description"]').input_value() or "").strip()
    cur_week = ""
    try:
        aw = page.locator('text=Assembly Week').first.locator('..').locator(
            'input[type="date"], input[type="text"]').first
        cur_week = (aw.input_value() or "").strip() if aw.count() else ""
    except Exception:
        cur_week = ""

    # Resolve the assembly week BEFORE touching fields (capacity read navigates away).
    chosen_week = ""
    if not cur_week:
        try:
            schedule = read_schedule_capacity(page)
            print_schedule(schedule)
            chosen_week, _label, reason = pick_assembly_week(
                schedule,
                required_date=sor.get("required_date", ""),
                is_expedited=bool(sor.get("is_expedited")),
                order_weight=calculate_order_weight(products),
            )
            print(f"[LL] Assembly week -> {chosen_week or '(none picked)'} ({reason})")
        except Exception as e:
            print(f"[LL] Could not auto-pick assembly week ({e}) -- set it manually.")
        navigate_to_so(page, so_id)

    # MOOPS won't save an SO that has a cust id but no location yet -- clear it before filling.
    if _clear_customer_id_if_blocking(page):
        print("[LL] Cleared End-Customer field (no location yet) so tag/week/parts can save.")

    changed = False
    if not cur_tag:
        tag_value = build_tag(products, customer_name)
        print(f"\n--- Tag: {tag_value} ---")
        action_set_tag(page, tag_value)
        changed = True
    else:
        print(f"\n--- Tag already set ({cur_tag}) -- skip ---")

    if not cur_week and chosen_week:
        print(f"\n--- Assembly week: {chosen_week} ---")
        action_set_assembly_week(page, chosen_week)
        changed = True
    elif cur_week:
        print(f"\n--- Assembly week already set ({cur_week}) -- skip ---")

    print("\n--- Missing hardware companion parts ---")
    added = action_add_required_parts(page, processor_type=sor.get("processor_type", "")) or []
    if added:
        changed = True

    if changed:
        print("\n--- Save SO (tag / assembly week / hardware) ---")
        save_so(page, accept_sor=False, clear_customer_location_blocker=False)
    else:
        print("\n--- No tag/week/parts changes to save ---")

    # Config loop: one location at a time. Re-link the location + re-download each pair, because
    # the .cfg carries location-specific data (so a single download patched per unit is NOT valid).
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "vac_configs", f"SO{so_id}")
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    for old in out_path.glob("*.cfg"):
        try:
            old.unlink()
        except Exception:
            pass

    all_paths = []
    for L in range(n_locations):
        loc = _location_id(L)
        pair = units[L * VACS_PER_LOCATION:(L + 1) * VACS_PER_LOCATION]
        # KioskName restarts at VAC01 per location.
        loc_units = [(i + 1, part) for i, part in enumerate(pair)]
        print(f"\n--- Location {loc}: link End Customer {LL_STOCK_CUST_ID} + {len(pair)} VAC(s) ---")
        set_so_end_customer(page, LL_STOCK_CUST_ID, location_id=loc, save=True)
        # [VERIFY-LIVE] The .cfg must reflect THIS location. If the End-Customer block accumulates
        # locations instead of switching, the download may carry the wrong LocationID -- check the
        # first pair's .cfg before trusting the whole run.
        paths = download_location_vac_configs(page, so_id, out_dir, loc, loc_units)
        all_paths.extend(paths)

    if not all_paths:
        print("[LL] No config files downloaded -- aborting upload; nothing marked.")
        return

    print(f"\n--- Upload {len(all_paths)} config file(s) to the SO ---")
    navigate_to_so(page, so_id)
    before = read_config_file_resources(page)
    upload_files_to_so(page, all_paths)
    save_so(page, accept_sor=False, clear_customer_location_blocker=False)
    after = before
    for _ in range(6):
        after = read_config_file_resources(page)
        if len(after) > len(before):
            break
        page.wait_for_timeout(1000)
    verified = len(after) > len(before)
    print(f"[LL] File Resources: {len(before)} -> {len(after)} .cfg "
          f"({'verified' if verified else 'NOT confirmed -- re-attach'})")

    # Tasks: hardware (1) + config (9) only; the rest are N/A for a stock VAC order.
    print("\n--- Task checklist (1 & 9 Completed; provisioning tasks N/A) ---")
    statuses = {1: "Completed", 9: "Completed" if verified else "To Do"}
    for n in (2, 3, 4, 5, 6, 7, 8, 10):
        statuses[n] = "N/A"
    set_task_checklist(page, statuses)
    save_so(page, accept_sor=False, clear_customer_location_blocker=False)

    print("\n" + "=" * 60)
    print(f"  LAUNDRYLUX RUN COMPLETE -- SO-{so_id}: {n_locations} location(s), "
          f"{len(all_paths)} config(s) {'attached' if verified else 'NOT confirmed'}.")
    print("=" * 60)
