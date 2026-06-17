"""Read-only workflow planning for order reruns.

This module is intentionally pure: it takes already-read SO/SOR/task state and
returns what an optimized rerun should skip, do, or wait on. Browser reads and
browser writes stay outside this file.
"""


def classify_card_type(design: str) -> str:
    """Classify the SOR Card Design Type field into one of four actions."""
    d = (design or "").strip().lower()
    if not d:
        return "none"
    if d.startswith("new"):
        return "new"
    if d.startswith("modify"):
        return "modify"
    if d.startswith("reprint") or d.startswith("re-print") or d == "existing" or d.startswith("reorder"):
        return "reprint"
    return "none"


def _vac_flags(part_number: str) -> dict:
    pn = (part_number or "").upper().strip()
    if not pn.startswith("VAC"):
        return {"is_vac": False, "touch": False, "pinpad": False, "dispenser": False}
    try:
        body = pn[3:].split("-")
        yz = body[1] if len(body) > 1 else ""
        wm = body[2] if len(body) > 2 else ""
        return {
            "is_vac": True,
            "touch": body[0] in ("07", "08"),
            "pinpad": len(yz) >= 2 and yz[1] != "0",
            "dispenser": len(wm) >= 1 and wm[0] != "0",
        }
    except Exception:
        return {"is_vac": True, "touch": False, "pinpad": False, "dispenser": False}


def audit_hardware_requirements(so_data: dict, sor_data: dict) -> dict:
    """Pure check for whether system hardware companion parts already exist."""
    products = so_data.get("products", [])
    parts = {(p.get("part_number", "") or "").upper() for p in products}
    is_route = bool(so_data.get("is_route"))
    processor = sor_data.get("processor_type", "")
    expected_pinpad = "KIT-A35" if (
        "FORTIS" in processor.upper() or "EBT" in processor.upper() or processor.strip() == "2"
    ) else "KIT-P630"

    vac_qty = touch_qty = pinpad_qty = dispenser_qty = 0
    for p in products:
        flags = _vac_flags(p.get("part_number", ""))
        if not flags["is_vac"]:
            continue
        qty = int(p.get("qty", 0) or 0)
        vac_qty += qty
        if flags["touch"]:
            touch_qty += qty
        if flags["pinpad"]:
            pinpad_qty += qty
        if flags["dispenser"]:
            dispenser_qty += qty

    missing = []
    present = []
    if pinpad_qty:
        (present if expected_pinpad in parts else missing).append(f"pinpad kit {expected_pinpad}")
    if touch_qty:
        (present if "03-01-34" in parts else missing).append("thermal paper 03-01-34")
    if dispenser_qty and not is_route:
        (present if "CARD-03-01" in parts else missing).append("system cards CARD-03-01")
    if vac_qty and not is_route:
        (present if "SVC-LAUNDROMAT" in parts else missing).append("activation SVC-LAUNDROMAT")

    missing_associations = []
    skipped_associations = []
    for m in so_data.get("missing", []):
        part = (m.get("associated_part") or m.get("part") or "").strip()
        source = (m.get("part_number") or m.get("source") or "").strip()
        desc = (m.get("description") or m.get("desc") or "").strip()
        qty = (m.get("qty") or "").strip() if isinstance(m.get("qty"), str) else m.get("qty")
        part_upper = part.upper()
        desc_upper = desc.upper()
        source_upper = source.upper()

        if not part:
            continue
        label = f"{part} qty={qty} (from {source})"
        if part_upper in parts:
            skipped_associations.append(f"{label} already on order")
        elif part_upper in {"CARD-03-01", "03-01-34", expected_pinpad}:
            skipped_associations.append(f"{label} covered by companion-part rules")
        elif "OLD VERSION" in desc_upper:
            skipped_associations.append(f"{label} old version")
        elif part_upper == "01-05-56" and "CR-" in source_upper and "-126" in source_upper:
            skipped_associations.append(f"{label} X-series/USX blocker plate not needed")
        elif part_upper.startswith("02-06-78"):
            skipped_associations.append(f"{label} long cable review-only")
        elif part_upper == "01-03-03":
            skipped_associations.append(f"{label} pedestal review-only")
        else:
            missing_associations.append(label)

    missing.extend(missing_associations)

    return {
        "ready": not missing,
        "missing": missing,
        "present": present,
        "missing_associations": missing_associations,
        "skipped_associations": skipped_associations,
        "vac_qty": vac_qty,
        "touch_qty": touch_qty,
        "pinpad_qty": pinpad_qty,
        "dispenser_qty": dispenser_qty,
        "expected_pinpad": expected_pinpad,
    }


def build_system_rerun_plan(so_data: dict, sor_data: dict, tasks: dict,
                            end_customer: dict) -> dict:
    """Build the plug-and-play step plan for the current system/route SO state.

    Returns:
        {
          "skip": [...],
          "actionable": [...],
          "blocked": [...],
          "is_route": bool,
          "card_type": "new|modify|reprint|none",
        }
    """
    card_type = classify_card_type(sor_data.get("card_design_type", ""))
    is_route = bool(so_data.get("is_route"))
    products = so_data.get("products", [])
    sor_existing = sor_data.get("existing_end_customer", "")
    sor_existing_id = sor_data.get("existing_end_customer_id", "")
    effective_customer_id = end_customer.get("id") or sor_existing_id
    effective_customer_name = end_customer.get("name") or sor_existing
    vac_qty = sum(
        int(p.get("qty", 0) or 0)
        for p in products
        if (p.get("part_number", "") or "").upper().startswith("VAC")
    )
    card_po_done = any(
        (p.get("part_number", "") or "").upper().startswith("CARD-MD-") and p.get("has_po")
        for p in products
    )
    inputs = []
    hardware = audit_hardware_requirements(so_data, sor_data)

    def _present(value) -> bool:
        return bool(str(value or "").strip())

    def _input_status(step: str, ready: bool, detail: str):
        inputs.append({"step": step, "ready": ready, "detail": detail})

    def _ready_action(step: str, ready: bool, action: str, wait: str, detail: str):
        _input_status(step, ready, detail)
        if ready:
            actionable.append(action)
        else:
            blocked.append(wait)

    actionable = []
    blocked = []
    skip = []

    tag = so_data.get("tag", "")
    if tag:
        skip.append(f"Tag already set: {tag}")
    else:
        actionable.append("Tag missing -> set tag")

    if so_data.get("assembly_week"):
        skip.append(f"Assembly week already set: {so_data['assembly_week']}")
    else:
        actionable.append("Assembly week missing -> pick/set week")

    task_status = {n: t.get("status", "") for n, t in tasks.items()}
    if task_status.get(1) == "Completed":
        skip.append("Task 1 Completed -> skip hardware/parts step")
    elif hardware["ready"]:
        skip.append("Hardware companion parts already present -> do not add parts")
        actionable.append("Task 1 To Do -> verify/mark hardware checklist only")
        _input_status(
            "Task 1 hardware",
            True,
            "present=" + ", ".join(hardware["present"] or ["none required"]),
        )
    else:
        actionable.append("Task 1 not Completed -> add missing hardware companion parts")
        _input_status(
            "Task 1 hardware",
            bool(products),
            f"missing={', '.join(hardware['missing'])}; "
            f"present={', '.join(hardware['present'] or ['none'])}; "
            f"moops_missing_parts={len(so_data.get('missing', []))}",
        )

    location_action_ready = False

    if is_route:
        skip.append("Route order -> skip SaaS/payment/location/user provisioning")
        if all(task_status.get(n) == "N/A" for n in range(3, 11)):
            skip.append("Route task checklist already marked N/A for tasks 3-10")
        else:
            actionable.append("Route task checklist needs 1-2 Completed and 3-10 N/A")
    else:
        location_name = sor_data.get("location_name", "")
        location_address = sor_data.get("location_address", "")
        contact_name = sor_data.get("contact_name", "")
        contact_email = sor_data.get("contact_email", "")
        contact_phone = sor_data.get("contact_phone", "")
        customer_name = so_data.get("customer_name", "")
        has_customer_identity = _present(location_name) or _present(customer_name)
        has_location = _present(location_address)
        has_contact = _present(contact_name) and (_present(contact_email) or _present(contact_phone))
        has_end_customer = bool(effective_customer_id)
        location_task_done = task_status.get(8) == "Completed"

        location_action_ready = (
            task_status.get(8) != "Completed"
            and has_customer_identity
            and has_location
        )

        if task_status.get(8) == "Completed":
            skip.append("Task 8 Completed -> skip location creation")
        else:
            _ready_action(
                "Task 8 location",
                location_action_ready,
                "Task 8 To Do -> add/verify Portal location",
                "Task 8 To Do but location/customer inputs are incomplete",
                f"customer_name={'yes' if has_customer_identity else 'no'}, "
                f"location_address={'yes' if has_location else 'no'}, vac_qty={vac_qty}",
            )

        if task_status.get(7) == "Completed":
            skip.append("Task 7 Completed -> skip payment account configuration")
        else:
            processor = (sor_data.get("processor_type", "") or "").upper()
            is_fortis = ("FORTIS" in processor) or ("EBT" in processor) or processor.strip() == "2"
            if is_fortis:
                skip.append("Task 7 payment -> Fortis/EBT processor, skip Stripe setup")
            else:
                _ready_action(
                "Task 7 payment",
                    has_end_customer and has_location and location_task_done,
                    "Task 7 To Do -> configure payment account if supported",
                    "Task 7 To Do but Portal location is not completed yet",
                    f"end_customer={'yes' if has_end_customer else 'no'}, "
                    f"location_address={'yes' if has_location else 'no'}, "
                    f"task8_location_done={'yes' if location_task_done else 'no'}"
                    + ("" if location_task_done else "; waiting on task 8 location"),
                )

        if task_status.get(10) == "Completed":
            skip.append("Task 10 Completed -> skip final Portal user/intro email")
        elif effective_customer_id:
            skip.append("Existing customer identified -> skip final Portal user/intro email")
        else:
            _ready_action(
                "Task 10 user/intro",
                has_contact,
                "Task 10 To Do -> final Portal user/intro email",
                "Task 10 To Do but contact name/email/phone are incomplete",
                f"contact_name={'yes' if _present(contact_name) else 'no'}, "
                f"contact_email={'yes' if _present(contact_email) else 'no'}, "
                f"contact_phone={'yes' if _present(contact_phone) else 'no'}",
            )

        if task_status.get(9) == "Completed":
            skip.append("Task 9 Completed -> skip SO End Customer/config workflow")
        elif has_end_customer and location_task_done:
            actionable.append("Task 9 To Do -> link SO End Customer/location if needed, then upload VAC config files")
            source = "SO End Customer linked" if end_customer.get("id") else "SOR Existing End Customer"
            _input_status("Task 9 config", True, source)
        elif has_end_customer:
            blocked.append("Task 9 To Do but Portal location is not completed yet")
            source = "SO End Customer linked" if end_customer.get("id") else "SOR Existing End Customer"
            _input_status("Task 9 config", False, f"{source}; waiting on task 8 location")
        elif location_action_ready:
            blocked.append("Task 9 To Do but customer/location will be created in this run")
            _input_status("Task 9 config", False, "new customer/location; waiting on task 8 location")
        elif location_task_done:
            # Location is already provisioned (task 8 Completed) but the End Customer isn't linked
            # on the SO yet -- e.g. the customer was just added to the dealer record by hand. This
            # IS work: the chain resolves the already-created customer (dedup-grab), links the End
            # Customer + location, then uploads config. Mark ACTIONABLE so the run doesn't
            # short-circuit to "nothing to do" (Matt: a re-run after the dealer add must finish 9).
            actionable.append("Task 9 To Do -> resolve/link SO End Customer, then upload VAC config files")
            _input_status("Task 9 config", True, "location provisioned; resolve + link End Customer, then config")
        else:
            blocked.append("Task 9 To Do but End Customer is not linked")
            _input_status("Task 9 config", False, "End Customer not linked")

        card_task_states = {task_status.get(3), task_status.get(4), task_status.get(5)}
        if card_po_done:
            skip.append("Card PO exists on CARD-MD row -> skip card workflow")
        elif card_task_states <= {"N/A", "Completed"}:
            skip.append("Card tasks 3-5 already resolved -> skip card workflow")
        elif card_type in ("new", "modify", "reprint"):
            actionable.append(f"Card workflow may be needed from SOR card type: {card_type}")

        if task_status.get(6) == "To Do":
            # Task 6 is now automated in the system run: the chain posts the order to the
            # #moops-matt-mark Slack channel (Mark then does the SF account/location/opportunity
            # work + intro email). It's actionable work, not a Salesforce-only WAIT -- otherwise a
            # touched order with only task 6 left short-circuits to "nothing to do" and never posts.
            actionable.append("Task 6 To Do -> post SaaS handoff to #moops-matt-mark (Slack)")

    hard_blocked = []
    for blocker in blocked:
        if "Salesforce" in blocker:
            continue
        if "Portal location is not completed yet" in blocker and location_action_ready:
            continue
        if "customer/location will be created in this run" in blocker and location_action_ready:
            continue
        if "End Customer is not linked" in blocker:
            # The chain resolves the customer (dedup-grab existing / create) and links the End
            # Customer; if the dealer link is still pending it flags + skips config. It does NOT
            # need to hard-stop the whole run -- let it proceed and handle it.
            continue
        hard_blocked.append(blocker)

    return {
        "skip": skip,
        "actionable": actionable,
        "blocked": blocked,
        "hard_blocked": hard_blocked,
        "is_route": is_route,
        "card_type": card_type,
        "inputs": inputs,
        "effective_customer_id": effective_customer_id,
        "effective_customer_name": effective_customer_name,
        "hardware": hardware,
    }
