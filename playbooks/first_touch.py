"""
First Touch Playbook — System - Laundromat orders.

Full end-to-end sequence:
  1. Read SO (products, tag, missing parts, customer name, VAC decode)
  2. Read SOR (processor type, required date, EXPEDITED, card design type, contact)
  3. Check schedule + auto-pick assembly week (FIFO under 35, emergency buffer for EXPEDITED)
  4. Set tag (product table order, not alphabetical)
  5. Set assembly week
  6. Add rule-based parts + analyze missing parts
  7. Save
  8. ITF form (Jira service desk)
  9. Card workflow (if new design): clone → human saves → add to SO → save → email
  10. Set task checklist (auto-detects card state) + save
"""

import time

from core.browser import navigate_to_so
from core.moops import (
    decode_vac,
    determine_pinpad_kit,
    read_sale_or_route,
    read_sor_data,
    read_schedule_capacity,
    read_existing_customer_id,
    read_internal_notes,
    find_reference_so,
    read_so_end_customer,
    read_missing_parts,
    generate_card_shortname,
    clone_temp_card,
    open_card_design_email,
    open_itf_form,
    save_so,
    build_tag,
    action_set_tag,
    action_set_assembly_week,
    action_add_required_parts,
    action_add_card_to_so,
    action_set_system_tasks,
    read_task_states,
)
import re

from core.schedule import (
    calculate_order_weight,
    pick_assembly_week,
    print_schedule,
    planned_week_for_sor,
)


def read_so(page, so_id):
    """Navigate to SO and read everything in one shot."""
    from core.moops import read_products, read_customer_name, read_missing_parts

    navigate_to_so(page, so_id)

    tag = page.locator('input[name="description"]').input_value().strip()
    print(f"Tag: {tag or '(empty)'}")

    # Current assembly week (for check-or-skip -- don't re-schedule if already set)
    assembly_week = ""
    try:
        aw_container = page.locator('text=Assembly Week').first.locator('..')
        aw_input = aw_container.locator('input[type="date"], input[type="text"]').first
        if aw_input.count() > 0:
            assembly_week = (aw_input.input_value() or "").strip()
    except Exception:
        pass
    print(f"Assembly week: {assembly_week or '(none)'}")

    customer_name = read_customer_name(page)
    print(f"Customer: {customer_name or '(not found)'}")

    products = read_products(page)
    print(f"Products: {len(products)}")
    for p in products:
        print(f"  {p['part_number']:30s} qty={p['qty']}")

    missing = read_missing_parts(page)
    if missing:
        print(f"Missing parts: {len(missing)}")
        for m in missing:
            print(f"  {m['part_number']:20s} -> {m['associated_part']:15s} qty={m['qty']:5s} {m['description'][:40]}")
    else:
        print("Missing parts: none")

    vac_summary = []
    for p in products:
        if p["part_number"].upper().startswith("VAC"):
            d = decode_vac(p["part_number"])
            vac_summary.append({**d, "qty": p["qty"]})
            print(f"  VAC decode: {p['part_number']} -> cabinet={d['cabinet']} pinpad={d['needs_pinpad']} "
                  f"touch={d['is_touchscreen']} dispenser={d['needs_card_dispenser']}")

    sale_route = read_sale_or_route(page)
    print(f"Sale/Route: {sale_route or '(not set)'}")

    return {
        "tag": tag,
        "assembly_week": assembly_week,
        "customer_name": customer_name,
        "products": products,
        "missing": missing,
        "vac_summary": vac_summary,
        "is_route": sale_route.lower() == "route",
    }


def run(page, so_id, assembly_week=None, no_itf=False, dedup_test=False):
    """Execute the full first-touch playbook.

    no_itf=True    -> Step 8 fills the Admin Portal Create Customer form (no submit)
                      instead of opening the ITF Jira form (the eliminate-ITF path).
    dedup_test=True -> Step 3 also prints a customer dedup verdict (TEST scaffolding;
                      print-only, takes no action -- will not live here long-term).
    Both default off; the proven `s first <id>` flow is unchanged.
    """
    print("\n" + "=" * 60)
    print(f"  SYSTEM RUN (s {so_id}) -- SO-{so_id}")
    print("=" * 60)
    t_start = time.time()
    created_cust_id = ""  # set when no_itf creates a customer; returned for the chain
    dedup_result = None   # dedup_test: full match detail, surfaced again at end of run
    verify_only = False   # replacement/exchange: reuse existing customer, don't re-provision
    ref_location_id = ""  # existing location inherited from the referenced SO (verify mode)
    replacement_ref = ""  # the SO id this order replaces, if any (for the dedup summary)

    # Step 1: Read SO
    t0 = time.time()
    print("\n--- Step 1: Read SO ---")
    so_data = read_so(page, so_id)
    is_route = so_data.get("is_route", False)
    if is_route:
        print(">> ROUTE ORDER DETECTED — no SVC, no CARD-03-01, no ITF")
    order_weight = calculate_order_weight(so_data["products"])
    print(f"Order weight: {order_weight:.1f} weighted VAC slots")

    # Existing customer (the dealer) -- read now (still on SO page) so it's
    # available for the route tag in Step 4. Reused for ITF / card workflow below.
    existing_cust = read_existing_customer_id(page)
    cust_id = existing_cust.get("id", "") if existing_cust else ""
    # The End Customer FIELD on the SO is the authoritative new/existing signal (the human
    # sets it when they investigate intake). read_existing_customer_id only parses the
    # notes, so ALSO read the live field -- prevents creating a duplicate when the cust id
    # is already linked on the order.
    if not cust_id:
        field_cust = read_so_end_customer(page)
        if field_cust.get("id"):
            existing_cust = {"name": field_cust.get("name", ""), "id": field_cust["id"]}
            cust_id = field_cust["id"]
            print(f"[INFO] End Customer already set on SO: {existing_cust['name']} ({cust_id}) "
                  f"-- treating as existing, will not create")
    if cust_id:
        print(f"[INFO] Existing customer: {existing_cust['name']} (ID: {cust_id})")
    # Task 1 (Hardware verified) Completed => this SO was already first-touched. On that
    # second/look-back pass we must NOT re-run parts (don't re-add SVC + pinpad). Tag/schedule
    # already check-or-skip; customer resolution + the chain still run (idempotent).
    try:
        hardware_done = read_task_states(page).get(1, {}).get("status") == "Completed"
    except Exception:
        hardware_done = False
    if hardware_done:
        print("[INFO] Hardware verified (task 1) already Completed -- second touch: parts step "
              "will be skipped.")
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 2: Read SOR
    t0 = time.time()
    print("\n--- Step 2: Read SOR data ---")
    sor_data = read_sor_data(page)
    processor_type = sor_data["processor_type"]
    kit = determine_pinpad_kit(processor_type)
    print(f"Processor: '{processor_type}' -> Pinpad kit: {kit}")

    required_date = sor_data.get("required_date", "")
    is_expedited = sor_data.get("is_expedited", False)
    if required_date:
        exp_flag = " *** EXPEDITED ***" if is_expedited else ""
        print(f"Required delivery date: {required_date}{exp_flag}")
    else:
        print("Required delivery date: none")

    card_design = sor_data.get("card_design_type", "")
    print(f"Card design type: {card_design or 'none (no cards on SOR)'}")

    contact_name = sor_data.get("contact_name", "")
    contact_email = sor_data.get("contact_email", "")
    if contact_name:
        print(f"Contact: {contact_name} / {contact_email}")
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 3: Schedule + assembly week
    t0 = time.time()
    print("\n--- Step 3: Schedule + Assembly Week ---")

    week_already_set = bool(so_data.get("assembly_week"))
    if assembly_week:
        print(f"Assembly week override: {assembly_week}")
        chosen_week = assembly_week
        chosen_label = assembly_week
        pick_reason = "Manual override via --assembly-week"
    elif week_already_set:
        # CHECK-OR-SKIP: already scheduled -- keep it, don't re-read capacity or overwrite.
        chosen_week = so_data["assembly_week"]
        chosen_label = chosen_week
        pick_reason = "Already set on the SO -- check-or-skip (not re-scheduled)"
        print(f"Assembly week already set: {chosen_week} -- keeping it (check-or-skip)")
    else:
        # Reuse the week intake already computed for this SOR (same pick_assembly_week
        # logic) instead of re-reading capacity every order. Fall back to a live read.
        sor_id = ""
        m = re.search(r"/order-requests/(\d+)", sor_data.get("sor_url", ""))
        if m:
            sor_id = m.group(1)
        planned = planned_week_for_sor(sor_id)
        if planned:
            chosen_week = planned
            chosen_label = planned
            pick_reason = f"From intake plan (SOR-{sor_id}) -- schedule not re-read"
            print(f"Using intake-planned assembly week: {planned} (SOR-{sor_id})")
        else:
            schedule = read_schedule_capacity(page)
            print_schedule(schedule)
            chosen_week, chosen_label, pick_reason = pick_assembly_week(
                schedule, required_date=required_date,
                is_expedited=is_expedited, order_weight=order_weight,
            )

    if chosen_week:
        print(f"\n>> PICKED: {chosen_label} ({chosen_week})")
        print(f"   Reason: {pick_reason}")
    else:
        print(f"\n>> COULD NOT AUTO-PICK ASSEMBLY WEEK")
        print(f"   Reason: {pick_reason}")
        print(f"   Re-run with --assembly-week YYYY-MM-DD to set manually")
    print(f"  [{time.time() - t0:.1f}s]")

    # TEST: customer dedup at the schedule step. Print-only, takes no action; this
    # is scaffolding to validate matching during real runs and is not its long-term
    # home (that's intake's gate). /customers is scraped once and cached per session.
    if dedup_test:
        from core import dedup, portal
        print("\n--- [TEST] Customer dedup ---")
        if cust_id:
            # The SO's Existing End Customer field is authoritative -- when it's set,
            # the verdict IS existing; don't fuzzy-match (which can read NEW and mislead).
            print(f"  SO Existing End Customer: {existing_cust.get('name', '')} ({cust_id})")
            print(f"  Verdict: EXISTING  {cust_id}  (from SO Existing End Customer)")
        else:
            need_nav = portal._CUSTOMER_CACHE is None
            customers = portal.scrape_admin_customers(page, use_cache=True)
            signals = {
                "customer_name": so_data.get("customer_name", ""),
                "contact_name": sor_data.get("contact_name", ""),
                "contact_email": sor_data.get("contact_email", ""),
                "contact_phone": sor_data.get("contact_phone", ""),
            }
            res = dedup.match_customer(signals, customers)
            res["signals"] = signals
            dedup_result = res  # surfaced again as a detailed summary at end of run
            ids = ", ".join(m["cust_id"] for m in res["matches"][:4])
            print(f"  Verdict: {res['verdict'].upper()}  {ids or '(no candidates)'}")
            if need_nav:
                navigate_to_so(page, so_id)  # restore SO page after the /customers scrape

    # Step 4: Set tag -- CHECK-OR-SKIP. Never overwrite an existing tag; set only if empty.
    t0 = time.time()
    tag_value = so_data.get("tag", "")  # default to the current tag (used in the summary)
    if so_data.get("tag"):
        print(f"\n--- Step 4: Tag already set ('{so_data['tag']}') -- skip (check-or-skip) ---")
    else:
        print("\n--- Step 4: Set tag ---")
        dealer_name = existing_cust.get("name", "") if existing_cust else ""
        tag_value = build_tag(so_data["products"], so_data["customer_name"],
                              dealer_name=dealer_name, is_route=is_route)
        if tag_value:
            print(f"Auto-generated tag: {tag_value}")
            action_set_tag(page, tag_value)
        else:
            print("[WARNING] Could not generate tag -- no VACs found?")
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 5: Set assembly week -- CHECK-OR-SKIP. Don't re-set if already scheduled.
    t0 = time.time()
    if week_already_set:
        print(f"\n--- Step 5: Assembly week already set ({so_data['assembly_week']}) -- skip (check-or-skip) ---")
    elif chosen_week:
        print(f"\n--- Step 5: Set assembly week ---")
        action_set_assembly_week(page, chosen_week)
    else:
        print(f"\n--- Step 5: Skipped (no assembly week chosen) ---")
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 6: Add parts + analyze missing -- SKIP on a second touch (hardware already verified),
    # so we don't re-run missing-parts or re-add SVC/pinpad on an order whose parts are done.
    t0 = time.time()
    if hardware_done:
        print("\n--- Step 6: Parts -- skip (Hardware verified = Completed; not re-running parts) ---")
        added_parts = []
    else:
        print("\n--- Step 6: Add parts + analyze missing ---")
        added_parts = action_add_required_parts(page, processor_type=processor_type, is_route=is_route) or []
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 7: Save
    t0 = time.time()
    print("\n--- Step 7: Save SO ---")
    save_so(page, accept_sor=False)
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 8: ITF form
    from run import _card_type
    needs_new_card = _card_type(card_design) in ("new", "modify")
    shortname = ""
    card_part = ""

    t0 = time.time()
    if is_route:
        print("\n--- Step 8: ITF skipped (Route order) ---")
    elif no_itf and existing_cust and existing_cust.get("id"):
        # EXISTING customer (SOR names an end-customer id) -> don't create one.
        created_cust_id = existing_cust["id"]
        cust_id = existing_cust["id"]  # card owned by the existing customer
        print(f"\n--- Step 8: Existing customer {existing_cust.get('name','')} "
              f"({existing_cust['id']}) -- skipping Create Customer ---")
    elif no_itf:
        from core import provisioning
        notes_data = read_internal_notes(page)

        # Replacement/exchange: the customer already exists on a referenced SO.
        # Inherit its End Customer instead of minting a new (blank) one.
        replacement_ref = find_reference_so(notes_data.get("comments", ""))
        ref_cust = {}
        if replacement_ref and replacement_ref != str(so_id):
            print(f"\n--- Step 8: Replacement/exchange of SO-{replacement_ref} "
                  f"-- looking up its End Customer ---")
            navigate_to_so(page, replacement_ref)
            ref_cust = read_so_end_customer(page)
            navigate_to_so(page, so_id)

        if ref_cust.get("id"):
            cust_id = ref_cust["id"]
            created_cust_id = ref_cust["id"]
            existing_cust = {"name": ref_cust["name"], "id": ref_cust["id"]}
            ref_location_id = ref_cust.get("location_id", "")
            verify_only = True  # location/customer already exist -> verify, don't re-create
            print(f"[INFO] Reusing existing customer {ref_cust['name']} ({ref_cust['id']})"
                  + (f" / location {ref_location_id}" if ref_location_id else "")
                  + f" from SO-{replacement_ref}. Verify, don't re-create.")
        elif [m for m in (dedup_result or {}).get("matches", []) if m.get("strength") == "strong"]:
            # STRONG dedupe safety net: exact email/phone match.
            # ONE match -> ask Matt to confirm, then proceed as existing (no second run needed).
            # MULTIPLE matches -> ambiguous; still stop for manual resolution.
            strong = [m for m in dedup_result["matches"] if m.get("strength") == "strong"]
            ids = "; ".join(f"{m['cust_id']} {m.get('name','')} [{m.get('signal')}={m.get('detail')}]"
                            for m in strong)
            print("\n--- Step 8: STRONG dedupe match ---")
            if len(strong) == 1:
                m = strong[0]
                print(f"[CONFIRM] Exact match: {m['cust_id']} {m.get('name','')} "
                      f"[{m.get('signal')}={m.get('detail')}]")
                print("          Press Enter to proceed with provisioning as this existing customer,")
                print(f"          or Ctrl+C to stop and resolve manually.")
                try:
                    input()
                except (EOFError, KeyboardInterrupt):
                    print(f"\n[STOP] Stopped. Set the SO's End Customer to {m['cust_id']}, "
                          f"then re-run `system {so_id}`.")
                else:
                    # Confirmed -- treat as existing customer and proceed.
                    created_cust_id = m["cust_id"]
                    cust_id = m["cust_id"]
                    existing_cust = {"id": m["cust_id"], "name": m.get("name", "")}
                    print(f"[INFO] Proceeding as existing customer {m['cust_id']} -- chain will run.")
            else:
                print(f"[STOP] Multiple strong matches -- ambiguous: {ids}")
                print(f"       Set the SO's End Customer to the right one, then re-run `system {so_id}`.")
                print("       (Create manually only if it's truly new.)")
        else:
            c_name = notes_data.get("location_name", "") or so_data.get("customer_name", "")
            if not c_name or c_name.strip().lower() in ("", "(not found)"):
                # Never auto-create a blank customer. Flag for manual handling.
                if replacement_ref:
                    print(f"\n--- Step 8: Replacement of SO-{replacement_ref} but it has NO "
                          f"End Customer set -- can't inherit ---")
                print("[ABORT] No customer name on the SO/SOR and none inherited -- NOT creating a "
                      "blank customer. Link the End Customer manually (or fix the reference) and re-run.")
            else:
                print("\n--- Step 8: Create Customer (fill only, no submit) ---")
                c_contact = notes_data.get("contact_name", "") or contact_name
                c_email = notes_data.get("contact_email", "") or contact_email
                c_phone = notes_data.get("contact_phone", "")
                cid = provisioning.next_customer_id(page)
                provisioning.fill_create_customer(page, {
                    "so_id": so_id,
                    "customer_name": c_name,
                    "contact_name": c_contact,
                    "contact_email": c_email,
                    "contact_phone": c_phone,
                    "is_route": is_route,
                }, cust_id=cid, preview=False)
                print("\n[PAUSE] Review the Create Customer form and Save it in the browser, "
                      "then press Enter to continue (or Ctrl+C to stop).")
                try:
                    input()
                except KeyboardInterrupt:
                    print("\n[INFO] Continuing without confirming customer save.")
                if cid:
                    cust_id = cid  # own the card (step 9) under the NEW customer, not the dealer
                    created_cust_id = cid
                    print(f"[INFO] Card will be owned by new customer {cid} (verify it matches what you saved)")
        navigate_to_so(page, so_id)  # restore SO page for steps 9-10
    else:
        print("\n--- Step 8: IT Provisioning Form ---")
        notes_data = read_internal_notes(page)
        vac_count = sum(
            int(p["qty"]) for p in so_data["products"]
            if p["part_number"].upper().startswith("VAC") and str(p["qty"]).isdigit()
        )
        open_itf_form(page, so_id, notes_data, vac_count,
                      existing_customer=existing_cust if existing_cust else None,
                      card_design_type=card_design, processor_type=processor_type)
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 9: Card workflow -- deferred to the provisioning chain when no_itf, so cards
    # run LAST (after location / user / stripe), per the desired order.
    if needs_new_card and no_itf:
        print("\n--- Step 9: Cards deferred to the provisioning chain (after location/user/stripe) ---")
    elif needs_new_card:
        shortname = generate_card_shortname(so_data["customer_name"])
        print(f"\n[INFO] Card workflow: {card_design} — CARD-MD-{shortname}")

        t0 = time.time()
        print(f"\n--- Step 9a: Clone card ---")
        card_part = clone_temp_card(page, shortname, end_customer_id=cust_id)
        print(f"  [{time.time() - t0:.1f}s]")

        t0 = time.time()
        print(f"\n--- Step 9b: Add {card_part} to SO ---")
        navigate_to_so(page, so_id)
        action_add_card_to_so(page, card_part)
        print(f"  [{time.time() - t0:.1f}s]")

        t0 = time.time()
        print(f"\n--- Step 9c: Save SO ---")
        save_so(page, accept_sor=False)
        print(f"  [{time.time() - t0:.1f}s]")

        t0 = time.time()
        print(f"\n--- Step 9d: Card design email ---")
        open_card_design_email(page, card_part,
                               contact_name=contact_name, contact_email=contact_email)
        print(f"  [{time.time() - t0:.1f}s]")

        print("\n[PAUSE] Review and send the email. Press Enter when done, or Ctrl+C to stop.")
        try:
            input()
        except KeyboardInterrupt:
            print("\n[INFO] Continuing without confirming email send.")

    # Step 10: Set task checklist -- deferred to the provisioning chain when no_itf, so it
    # runs AFTER the card + cust id + location exist (correct card/task-3 detection).
    t0 = time.time()
    if no_itf:
        print("\n--- Step 10: Task checklist deferred to the provisioning chain (runs at the end) ---")
    else:
        print("\n--- Step 10: Set task checklist ---")
        navigate_to_so(page, so_id)
        action_set_system_tasks(page, is_route=is_route)
        save_so(page, accept_sor=False)
    print(f"  [{time.time() - t0:.1f}s]")

    # Summary
    elapsed = time.time() - t_start
    print(f"\n  Total: {elapsed:.1f}s")
    print("\n" + "=" * 60)
    order_type = "ROUTE" if is_route else "SYSTEM"
    print(f"  SYSTEM RUN COMPLETE ({order_type})")
    print(f"  SO-{so_id}: {tag_value}")
    if chosen_week:
        print(f"  Assembly week: {chosen_label} ({chosen_week})")
    print(f"  Pinpad kit: {kit} (processor: {processor_type or 'Stripe default'})")
    if card_design:
        if needs_new_card and no_itf:
            print(f"  Card: {card_design} -- deferred to provisioning chain")
        else:
            print(f"  Card: {card_design} -- CARD-MD-{shortname if needs_new_card else 'N/A'}")
    if added_parts:
        print(f"  Parts added:")
        for part, qty in added_parts:
            print(f"    {part} qty={qty}")
    # Compare initial vs final missing parts
    initial_missing = so_data.get("missing", [])
    final_missing = read_missing_parts(page)
    final_keys = {(m["part_number"], m["associated_part"]) for m in final_missing} if final_missing else set()
    resolved = [m for m in initial_missing if (m["part_number"], m["associated_part"]) not in final_keys]
    if resolved:
        print(f"  Missing parts resolved ({len(resolved)}):")
        for m in resolved:
            print(f"    {m['associated_part']} qty={m['qty']} (from {m['part_number']})")
    if final_missing:
        print(f"  Missing parts remaining ({len(final_missing)}):")
        for m in final_missing:
            print(f"    {m['associated_part']} qty={m['qty']} (from {m['part_number']})")
    print("  Remaining:")
    print("    Work State -> Placed -> Accept SOR")
    print("=" * 60)
    return {"cust_id": created_cust_id, "needs_new_card": needs_new_card,
            "existing": bool(existing_cust and existing_cust.get("id")),
            "dedup": dedup_result,
            "verify_only": verify_only,
            "ref_location_id": ref_location_id,
            "replacement_ref": replacement_ref,
            "sor_data": sor_data}
