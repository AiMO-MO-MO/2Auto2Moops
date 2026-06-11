"""
Cards Only Order Playbook.

Handles new design, reprint, and generic card orders.
Reuses the card workflow from first_touch but skips scheduling,
missing parts, rule-based parts, and ITF.

Steps:
  1. Read SO (products, customer name)
  2. Read SOR (card design type, contact info, card qty)
  3. Set tag: "QTY Cards (Customer Name)"
  4. Set Order Type -> Cards Only, Shipment -> Drop shipment / Card Supplier
  5. Card workflow:
     - New design: clone -> human saves -> add to SO -> save -> design email
     - Reprint: save -> Create PO -> PO email (clear CC) -> human sends
     - Generic: save (no card workflow)
  6. Work State -> Placed (human does this + Accept SOR)
"""

import re as _re
import time

from core.browser import navigate_to_so
from core.moops import (
    read_products,
    read_customer_name,
    read_sor_data,
    read_so_end_customer,
    read_existing_customer_id,
    save_so,
    set_shipment,
    set_order_type,
    clean_name,
    action_set_tag,
)


def build_cards_tag(products: list, customer_name: str, sor_card_qty: int = 0) -> str:
    """
    Build a tag for card orders.
    Examples: "5000 Cards (KMRB Investments)", "1000 Generic Cards (Tyler Hazel)"
    """
    # Check for CARD-MD-* or CARD-01-02 (placeholder from conversion) on the order
    card_qty = 0
    is_generic = False
    card_desc_name = ""
    for p in products:
        pn = p["part_number"].upper()
        qty = int(p["qty"]) if str(p["qty"]).isdigit() else 0
        if pn.startswith("CARD-MD-"):
            card_qty += qty
            if "GEN" in pn:
                is_generic = True
            # Extract customer name from card description first line
            # e.g. "Laundry Depot II card\nLocation: ..." -> "Laundry Depot II"
            desc = p.get("description", "")
            if desc and not card_desc_name:
                first_line = desc.split("\n")[0].strip()
                # Strip trailing " card" or " Card"
                card_desc_name = _re.sub(r'\s+cards?\s*$', '', first_line, flags=_re.IGNORECASE).strip()
        elif pn == "CARD-01-02" and card_qty == 0:
            card_qty = qty

    # Use SOR card qty as last resort
    if card_qty == 0 and sor_card_qty > 0:
        card_qty = sor_card_qty

    # Customer name: prefer passed-in, fall back to card description
    name = clean_name(customer_name) if customer_name else clean_name(card_desc_name)
    prefix = "Generic Cards" if is_generic else "Cards"
    return f"{card_qty} {prefix} ({name})" if card_qty > 0 else f"Cards ({name})"


def read_so(page, so_id):
    """Navigate to SO and read basic data."""
    navigate_to_so(page, so_id)

    tag = page.locator('input[name="description"]').input_value().strip()
    print(f"Tag: {tag or '(empty)'}")

    customer_name = read_customer_name(page)
    print(f"Customer: {customer_name or '(not found)'}")

    products = read_products(page)
    print(f"Products: {len(products)}")
    for p in products:
        print(f"  {p['part_number']:30s} qty={p['qty']}")

    return {
        "tag": tag,
        "customer_name": customer_name,
        "products": products,
    }


def run(page, so_id, shortname=None):
    """Execute the cards-only order playbook."""
    print("\n" + "=" * 60)
    print(f"  CARDS ORDER PLAYBOOK -- SO-{so_id}")
    print("=" * 60)
    t_start = time.time()

    # Step 1: Read SO
    t0 = time.time()
    print("\n--- Step 1: Read SO ---")
    so_data = read_so(page, so_id)
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 2: Read SOR
    t0 = time.time()
    print("\n--- Step 2: Read SOR data ---")
    sor_data = read_sor_data(page)

    card_design = sor_data.get("card_design_type", "")
    print(f"Card design type: {card_design or 'none'}")

    contact_name = sor_data.get("contact_name", "")
    contact_email = sor_data.get("contact_email", "")
    if contact_name:
        print(f"Contact: {contact_name} / {contact_email}")

    # Get card qty from SOR data
    sor_card_qty = sor_data.get("card_qty", 0)
    if sor_card_qty:
        print(f"Card qty from SOR: {sor_card_qty}")
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 3: Set tag
    t0 = time.time()
    print("\n--- Step 3: Set tag ---")
    tag_value = build_cards_tag(so_data["products"], so_data["customer_name"],
                                 sor_card_qty=sor_card_qty)
    print(f"Tag: {tag_value}")
    if so_data.get("tag") == tag_value:
        print("[INFO] Tag already correct -- skip")
    else:
        action_set_tag(page, tag_value)
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 4: Set order type + shipment
    t0 = time.time()
    print("\n--- Step 4: Set order type + shipment ---")
    set_order_type(page, "Cards Only")
    set_shipment(page, method="Drop shipment", shipped_by="Card Supplier")
    print(f"  [{time.time() - t0:.1f}s]")

    # Resolve card owner while we're still on the SO page. Prefer the live SO field,
    # then the older notes parser, then the SOR's Existing End Customer.
    existing_cust = read_so_end_customer(page)
    if not existing_cust.get("id"):
        existing_cust = read_existing_customer_id(page)
    if not existing_cust or not existing_cust.get("id"):
        sor_existing_id = (sor_data.get("existing_end_customer_id", "") or "").strip()
        if sor_existing_id:
            sor_existing_name = (sor_data.get("existing_end_customer", "") or "").strip()
            sor_existing_name = _re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", sor_existing_name).strip()
            existing_cust = {"name": sor_existing_name, "id": sor_existing_id}
    cust_id = existing_cust.get("id", "") if existing_cust else ""
    if cust_id:
        print(f"[INFO] Existing customer: {existing_cust['name']} (ID: {cust_id})")

    # Save before card workflow (tag + order type + shipment). MOOPS won't save when the SO
    # carries a cust id (End Customer) but no location yet -- the same bug we clear in the
    # system setup. The card owner (cust_id) is already captured above, so clearing the SO
    # End-Customer field to unblock the save is safe.
    t0 = time.time()
    print("\n--- Save SO (before card workflow) ---")
    save_so(page, accept_sor=False, clear_customer_location_blocker=True)
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 5: Card workflow -- delegates entirely to _do_cards (same as system run).
    from run import _do_cards
    is_generic = any(
        p["part_number"].upper().startswith("CARD-MD-GEN")
        for p in so_data["products"]
    )
    card_result = "none"

    if is_generic:
        print("\n--- Generic cards — no card workflow needed ---")
        print("[INFO] Generic cards ship with the system or via EFS. Done.")
    else:
        # Pass the SOR we already read and the shortname override (if any).
        # _do_cards handles new/modify/reprint/exists/none uniformly.
        card_result = _do_cards(page, so_id, cust_id, sor=sor_data, shortname=shortname)

    # Summary
    elapsed = time.time() - t_start
    print(f"\n  Total: {elapsed:.1f}s")
    print("\n" + "=" * 60)
    print("  CARDS ORDER COMPLETE")
    print(f"  SO-{so_id}: {tag_value}")
    print(f"  Card workflow: {card_result}")
    print("  Remaining:")
    print("    Work State -> Placed -> Accept SOR")
    print("=" * 60)
