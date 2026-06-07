"""
Final Touch Playbook — Pre-ship audit and task completion.

Reads task checklist, determines what's actionable, executes in optimal order.

Workflow routing based on remaining tasks:
  - Card tasks (3,4,5): MOOPS-only — check card state, send email, create PO
  - ITF (6): Jira form — opens ITF, blocks 7-10
  - Portal tasks (7,8,10): Admin Portal + LaundroPortal verification
  - Config (9): Manual — download, rename, upload

Execution is batched by destination to minimize navigation:
  Phase 1: MOOPS reads (SO page — one load)
  Phase 2: SOR reads (only if card/ITF/portal tasks need it)
  Phase 3: Card actions (MOOPS — email, PO)
  Phase 4: ITF (Jira — only if task 6 is To Do)
  Phase 5: Portal verification (admin tools → LaundroPortal → back)
  Phase 6: MOOPS save (one save with all updates)
"""

import re
import time

from core.browser import navigate_to_so
from core.moops import (
    read_products,
    read_customer_name,
    read_existing_customer_id,
    read_internal_notes,
    read_sale_or_route,
    read_so_log,
    read_sor_data,
    read_sor_link,
    read_schedule_capacity,
    read_task_states,
    set_task_checklist,
    build_tag,
    action_set_tag,
    action_set_assembly_week,
    create_card_po,
    open_po_email,
    open_card_design_email,
    open_itf_form,
    save_so,
    TASK_LABELS,
)
from core.schedule import calculate_order_weight, pick_assembly_week, planned_week_for_sor, print_schedule
from core.portal import verify_provisioning


def _read_assembly_week(page):
    """Current Assembly Week value on the SO ('' if not set)."""
    try:
        container = page.locator('text=Assembly Week').first.locator('..')
        di = container.locator('input[type="date"], input[type="text"]').first
        if di.count() > 0:
            return (di.input_value() or "").strip()
    except Exception:
        pass
    return ""


# ── Helpers ─────────────────────────────────────────────────


def _detect_card_state(products):
    """Returns (has_card, card_part, is_approved)."""
    for p in products:
        pn = p["part_number"].upper()
        if pn.startswith("CARD-MD-") and pn != "CARD-03-01":
            desc = p.get("description", "").upper()
            return True, p["part_number"], "PLACEHOLDER" not in desc
    return False, "", False


def _get_vac_count(products):
    """Count total VACs on the order."""
    return sum(
        int(p["qty"]) for p in products
        if p["part_number"].upper().startswith("VAC") and str(p["qty"]).isdigit()
    )


def _run_card_po(page, so_id, card_part):
    """Create PO for an approved card, open PO email, set Purchase State."""
    navigate_to_so(page, so_id)
    po_url = create_card_po(page, card_part)
    if not po_url:
        return False, ""

    po_page = page
    for p in page.context.pages:
        if "purchase" in p.url:
            po_page = p
            po_page.bring_to_front()
            break

    open_po_email(po_page)

    try:
        # Already on PO page after email — no reload needed
        time.sleep(1)
        po_page.locator('select[name="purchase_state_id"]').select_option(label="Ordered")
        print("[ACTION] Purchase State set to: Ordered")
        po_page.evaluate("""() => {
            const els = document.querySelectorAll('button, a, input[type="submit"]');
            for (const el of els) {
                if (el.textContent.trim() === 'Save' || el.textContent.trim().startsWith('Save')) {
                    el.click(); return true;
                }
            }
            return false;
        }""")
        time.sleep(3)
        print("[ACTION] PO saved")
    except Exception as e:
        print(f"[WARNING] Could not set Purchase State: {e}")

    return True, po_url


def _get_cust_id(existing_cust, page=None, so_id=None):
    """Extract customer ID string from existing_cust dict or fallback to SO read."""
    if isinstance(existing_cust, dict) and existing_cust.get("id"):
        return existing_cust["id"]
    if existing_cust and not isinstance(existing_cust, dict):
        return str(existing_cust)
    # Fallback: re-read from SO
    if page and so_id:
        navigate_to_so(page, so_id)
        fallback = read_existing_customer_id(page)
        if isinstance(fallback, dict):
            return fallback.get("id", "")
    return ""


# ── Main ────────────────────────────────────────────────────


def run(page, so_id):
    """Execute the final touch playbook."""
    print("\n" + "=" * 60)
    print(f"  FINAL TOUCH PLAYBOOK -- SO-{so_id}")
    print("=" * 60)
    t_start = time.time()

    # ================================================================
    # PHASE 1: Read SO state (one page load)
    # ================================================================
    t0 = time.time()
    print("\n--- Phase 1: Read SO ---")
    navigate_to_so(page, so_id)

    tag = page.locator('input[name="description"]').input_value().strip()
    print(f"Tag: {tag or '(empty)'}")

    customer_name = read_customer_name(page)
    print(f"Customer: {customer_name or '(not found)'}")

    # Route detection
    sale_route = read_sale_or_route(page)
    is_route = sale_route.lower() == "route"

    # Task states
    tasks = read_task_states(page)
    todo = {n for n, t in tasks.items() if t["status"] == "To Do"}
    done = {n for n, t in tasks.items() if t["status"] == "Completed"}
    na = {n for n, t in tasks.items() if t["status"] == "N/A"}

    # Products + card state (always needed for VAC count + card detection)
    products = read_products(page)
    has_card, card_part, card_approved = _detect_card_state(products)
    vac_count = _get_vac_count(products)

    # SO log (needed for card email check — cheap read, already on page)
    so_log = read_so_log(page)
    card_email_sent = any("card design email" in e["message"].lower() for e in so_log)

    print(f"  [{time.time() - t0:.1f}s]")

    # ================================================================
    # PHASE 1.5: Tag + Schedule -- CHECK-OR-SKIP (idempotent re-touch).
    # Never overwrite an existing value; fill ONLY if missing. This is what makes
    # final-touch safe on a partially-processed order (Matt: "don't overwrite the
    # tag, do it if it isn't there; check the schedule, if none then schedule").
    # ================================================================
    pre_actions = []
    print("\n--- Phase 1.5: Tag + schedule (check-or-skip) ---")
    changed = False
    if tag:
        print(f"[SKIP] Tag already set: {tag}")
    else:
        new_tag = build_tag(products, customer_name, is_route=is_route)
        action_set_tag(page, new_tag)
        tag = new_tag
        changed = True
        pre_actions.append(f"Tag set: {new_tag}")

    cur_week = _read_assembly_week(page)
    if cur_week:
        print(f"[SKIP] Assembly week already set: {cur_week}")
    else:
        href = read_sor_link(page)
        m = re.search(r"/order-requests/(\d+)", href or "")
        chosen = planned_week_for_sor(m.group(1)) if m else ""
        if chosen:
            print(f"[PLAN] Assembly week from intake plan: {chosen}")
        else:
            schedule = read_schedule_capacity(page)  # navigates to /orders
            print_schedule(schedule)
            chosen, _, reason = pick_assembly_week(
                schedule, order_weight=calculate_order_weight(products))
            navigate_to_so(page, so_id)              # back to the SO to set it
        if chosen:
            action_set_assembly_week(page, chosen)
            changed = True
            pre_actions.append(f"Assembly week set: {chosen}")
        else:
            print("[WARN] Could not pick an assembly week -- set it manually.")

    if changed:
        save_so(page, accept_sor=False)
    for a in pre_actions:
        print(f"  ✓ {a}")

    # ================================================================
    # ROUTE HANDLING: Set tasks 3-8,10 to N/A, keep only 9
    # ================================================================
    if is_route:
        print(f"\nSale/Route: Route")
        route_na = {3, 4, 5, 6, 7, 8, 10} & todo
        if route_na:
            print(f"[ROUTE] Setting {len(route_na)} tasks to N/A...")
            navigate_to_so(page, so_id)
            set_task_checklist(page, {n: "N/A" for n in route_na})
            save_so(page, accept_sor=False)
            for n in sorted(route_na):
                print(f"  ✓ Task {n}: {TASK_LABELS[n]} → N/A")
            todo -= route_na

    # ================================================================
    # PLAN: Determine what workflows are needed
    # ================================================================
    print(f"\nTasks remaining: {sorted(todo) if todo else 'none'}")
    for num in sorted(todo):
        print(f"  ○ Task {num:2d}: {TASK_LABELS[num]}")

    if not todo:
        elapsed = time.time() - t_start
        print(f"\nAll tasks complete or N/A. [{elapsed:.1f}s]")
        if pre_actions:
            print(f"  Did this pass: {', '.join(pre_actions)}")
        print("=" * 60)
        return

    # Classify what's needed
    need_card_work = bool({3, 4, 5} & todo) and has_card
    need_itf = 6 in todo
    itf_done = 6 in done
    need_portal = bool({7, 8, 10} & todo) and itf_done

    # SOR is needed for: card email (contact info), card PO (contact), ITF, portal (processor type)
    need_sor = (
        (3 in todo and has_card and not card_email_sent) or
        (5 in todo and card_approved) or
        need_itf or
        need_portal
    )

    if has_card:
        status = "APPROVED" if card_approved else "PLACEHOLDER"
        print(f"\n[INFO] Card {card_part}: {status}")
    if card_email_sent:
        print("[INFO] Card design email already sent (SO log)")
    if vac_count:
        print(f"[INFO] VAC count: {vac_count}")

    # Track all changes for single save at end
    updates = {}       # {task_num: "Completed" or "N/A"}
    blocked = {}       # {task_num: "reason"}
    actions = []       # ["Task N → Completed (reason)"]

    # ================================================================
    # PHASE 2: SOR reads (one navigation, only if needed)
    # ================================================================
    sor_data = None
    notes_data = None
    existing_cust = None

    if need_sor:
        t0 = time.time()
        print("\n--- Phase 2: Read SOR + notes ---")
        sor_data = read_sor_data(page)
        notes_data = read_internal_notes(page)
        existing_cust = read_existing_customer_id(page)
        print(f"  [{time.time() - t0:.1f}s]")

    # ================================================================
    # PHASE 3: Card workflows (MOOPS — tasks 3, 4, 5)
    # ================================================================
    if need_card_work:
        print("\n--- Phase 3: Card workflows ---")

        # Task 3: Card design email
        if 3 in todo:
            if not has_card:
                updates[3] = "N/A"
                actions.append("Task 3 → N/A (no card)")
            elif card_email_sent:
                updates[3] = "Completed"
                actions.append("Task 3 → Completed (email in SO log)")
            else:
                contact_name = sor_data.get("contact_name", "") if sor_data else ""
                contact_email = sor_data.get("contact_email", "") if sor_data else ""
                t0 = time.time()
                open_card_design_email(page, card_part,
                                       contact_name=contact_name, contact_email=contact_email)
                print(f"  [card email {time.time() - t0:.1f}s]")
                print("\n[PAUSE] Review and send the email. Press Enter when done, or Ctrl+C.")
                try:
                    input()
                    updates[3] = "Completed"
                    actions.append("Task 3 → Completed (email sent)")
                except KeyboardInterrupt:
                    print("\n[INFO] Email not confirmed — task 3 stays To Do")
                    blocked[3] = "Card design email opened but not confirmed"

        # Task 4: Card approval
        if 4 in todo:
            if not has_card:
                updates[4] = "N/A"
                actions.append("Task 4 → N/A (no card)")
            elif card_approved:
                updates[4] = "Completed"
                actions.append(f"Task 4 → Completed ({card_part} approved)")
            else:
                blocked[4] = f"{card_part} still PLACEHOLDER — waiting on customer"

        # Task 5: Card PO (depends on 4)
        if 5 in todo:
            if not has_card:
                updates[5] = "N/A"
                actions.append("Task 5 → N/A (no card)")
            elif 4 in blocked:
                blocked[5] = "Blocked by task 4 (card not approved)"
            elif card_approved or 4 in updates:
                t0 = time.time()
                success, po_url = _run_card_po(page, so_id, card_part)
                if success:
                    updates[5] = "Completed"
                    actions.append(f"Task 5 → Completed (PO created)")
                else:
                    blocked[5] = "Could not create PO"
                print(f"  [card PO {time.time() - t0:.1f}s]")
    else:
        # No card on order — mark card tasks N/A if they're To Do
        for n in [3, 4, 5]:
            if n in todo and not has_card:
                updates[n] = "N/A"
                actions.append(f"Task {n} → N/A (no card)")

    # ================================================================
    # PHASE 4: ITF (Jira — task 6)
    # ================================================================
    if need_itf:
        t0 = time.time()
        print("\n--- Phase 4: ITF ---")

        if not notes_data:
            notes_data = read_internal_notes(page)
        if existing_cust is None:
            existing_cust = read_existing_customer_id(page)

        card_design = sor_data.get("card_design_type", "") if sor_data else ""
        processor_type = sor_data.get("processor_type", "") if sor_data else ""

        navigate_to_so(page, so_id)
        open_itf_form(page, so_id, notes_data, vac_count,
                      existing_customer=existing_cust if existing_cust else None,
                      card_design_type=card_design, processor_type=processor_type)

        updates[6] = "Completed"
        actions.append("Task 6 → Completed (ITF opened)")
        print(f"  [{time.time() - t0:.1f}s]")

        # Tasks 7-10 are now waiting on IT
        for n in [7, 8, 9, 10]:
            if n in todo:
                blocked[n] = "ITF just sent — waiting on IT to provision"

    # ================================================================
    # PHASE 5: Portal verification (tasks 7, 8, 10)
    # ================================================================
    elif need_portal:
        t0 = time.time()
        print("\n--- Phase 5: Portal verification ---")

        cust_id = _get_cust_id(existing_cust, page, so_id)
        processor = sor_data.get("processor_type", "") if sor_data else ""

        if cust_id:
            pv = verify_provisioning(page, cust_id, vac_count,
                                     processor_type=processor)
            checks = pv.get("checks", {})

            # Map check results to task updates
            for task_num, check_key in [(7, "task_7"), (8, "task_8"), (10, "task_10")]:
                if task_num not in todo:
                    continue
                c = checks.get(check_key, {})
                if c.get("status") == "pass":
                    updates[task_num] = "Completed"
                    actions.append(f"Task {task_num} → Completed ({c['detail']})")
                elif c.get("status") == "warning":
                    blocked[task_num] = c.get("detail", "Check portal")
                else:
                    blocked[task_num] = c.get("detail", "Check portal manually")

            # Navigate back to MOOPS
            navigate_to_so(page, so_id)
            print(f"  [portal verification {time.time() - t0:.1f}s]")
        else:
            for n in [7, 8, 10]:
                if n in todo:
                    blocked[n] = "No Customer ID — can't verify portal"

        # Task 9: Config files — always manual for now
        if 9 in todo:
            blocked[9] = "Config file download — manual"

    else:
        # ITF not done and not being done now — tasks 7-10 blocked
        for n in [7, 8, 9, 10]:
            if n in todo:
                if n == 9:
                    blocked[n] = "Config file download — manual"
                else:
                    blocked[n] = "Blocked — ITF (task 6) not sent"

    # ================================================================
    # PHASE 6: Save (one save with all task updates)
    # ================================================================
    if updates:
        t0 = time.time()
        print("\n--- Phase 6: Save ---")
        navigate_to_so(page, so_id)
        set_task_checklist(page, updates)
        save_so(page, accept_sor=False)
        print(f"  [{time.time() - t0:.1f}s]")

    # ================================================================
    # SUMMARY
    # ================================================================
    elapsed = time.time() - t_start
    print(f"\n  Total: {elapsed:.1f}s")
    print("\n" + "=" * 60)
    print(f"  FINAL TOUCH -- SO-{so_id}")
    print(f"  Tag: {tag}")

    all_actions = pre_actions + actions
    if all_actions:
        print(f"\n  Completed ({len(all_actions)}):")
        for a in all_actions:
            print(f"    ✓ {a}")

    if blocked:
        print(f"\n  Remaining ({len(blocked)}):")
        for num, reason in sorted(blocked.items()):
            print(f"    ○ Task {num}: {TASK_LABELS[num]}")
            print(f"      → {reason}")

    if not blocked:
        print("\n  ** ALL TASKS COMPLETE **")
    print("=" * 60)
