"""
Parts/Readers Only Order Playbook.

Two fulfillment paths based on what's on the order:
  1. EFS (3PL) — product exists in EFS warehouse → set 3PL, handle -DS swap, JS clipboard
  2. VUnics/Shrewsbury — everything else → set VUnics, tag, done

Large reader orders (35+ kits on VUnics): upgrade to System - Laundromat, add to assembly schedule.
"""

import time

from core.browser import navigate_to_so
from core.moops import (
    read_products,
    read_customer_name,
    read_missing_parts,
    read_sor_data,
    read_shipping_to,
    clean_name,
    save_so,
    set_shipment,
    action_add_part,
    action_set_tag,
)
from core.efs import (
    EFS_PRODUCTS,
    KIT_EFS_COMPONENTS,
    is_efs_product,
    expand_kit_for_efs,
    map_sor_to_efs_shipping,
    build_efs_js_snippet,
    copy_to_clipboard,
)


# ── Tag builder for parts orders ─────────────────────────────────────────────

# Readable names for kit prefixes (used in tag generation)
KIT_NAMES = {
    "KIT-POS": "POS Kit",
    "KIT-DOORACCESS": "Door Access Kit",
    "KIT-VENDRITE": "Vendrite Kit",
    "KIT-MDBVENDING": "MDB Vending Kit",
    "KIT-MEDECO": "Medeco Kit",
    "KIT-DEXTER": "Dexter Kit",
    "KIT-ALLIANCE": "Alliance Kit",
}

# Short names for common individual parts (used in tag generation)
PART_NAMES = {
    "03-01-95": "A35 Pinpad",
    "03-01-99": "P630 Terminal",
    "03-01-101": "S700 Pinpad",
    "03-01-34": "Paper Rolls",
    "03-01-21": 'Monitor 15"',
    "01-02-23": "A35 Holder",
    "01-02-25": "P630 Holder",
    "01-05-56": "Blocker Plate",
    "03-01-43": "Wire Splicer",
    "01-03-03": "VAC Pedestal",
    "02-06-30": "MDC Cable",
    "02-06-78W": "C-Series Washer Cable",
    "02-06-78D": "C-Series Dryer Cable",
    "ASSY-CD-SK-AD1": "SyncoTek",
    "ASSY-02-02-10": "Generic Serial",
    "ASSY-02-02-11": "Generic Relay",
}


def _short_desc(desc: str, max_words: int = 3) -> str:
    """Truncate a MOOPS description to the first few meaningful words."""
    if not desc:
        return ""
    first_line = desc.split("\n")[0].strip()
    first_line = first_line.split("Shipped from")[0].strip()
    first_line = first_line.split("Target Machines")[0].strip()
    words = first_line.split()[:max_words]
    return " ".join(words).rstrip(",.")


def build_parts_tag(products: list, customer_name: str) -> str:
    """
    Build a tag for parts/readers orders using product descriptions.
    Examples: "Monitor 15" Touch screen (Ame Laundromat)", "22 Reader Kits (In House Laundromat)"

    Uses the description field from read_products. Falls back to part number if no description.
    BOM-aware: cables/plates whose qty matches reader qty are implicit (from kit explosion),
    so they're skipped.
    """
    # First pass: count readers and track quantities
    reader_qty = 0
    reader_qtys = set()
    for p in products:
        pn = p["part_number"].upper()
        qty = int(p["qty"]) if str(p["qty"]).isdigit() else 0
        if pn.startswith("CR-"):
            reader_qty += qty
            reader_qtys.add(qty)

    # Second pass: build descriptive tag
    parts_summary = []
    if reader_qty > 0:
        parts_summary.append(f"{reader_qty} Reader Kit{'s' if reader_qty > 1 else ''}")

    # BOM quantities: individual reader qtys + total (covers split kits)
    bom_qtys = reader_qtys | {reader_qty} if reader_qty > 0 else set()

    for p in products:
        pn = p["part_number"].upper()
        qty = int(p["qty"]) if str(p["qty"]).isdigit() else 0
        desc = p.get("description", "").strip()
        # Clean description: take first line, strip "Shipped from..." suffix
        if desc:
            desc = desc.split("\n")[0].strip()
            desc = desc.split("Shipped from")[0].strip()

        if pn.startswith("CR-"):
            continue  # Already counted

        # Wire splicers (03-01-43) are broken-out BOM, never a tag headline -- skip.
        if pn.replace("-DS", "") == "03-01-43":
            continue

        # Skip BOM parts whose qty matches any reader qty or total (implicit from kit explosion)
        if qty in bom_qtys and (pn.startswith("02-06-") or pn.startswith("01-05-")
                                    or pn.startswith("01-04-") or pn.startswith("03-01-")):
            continue

        # Cards: use fixed label, not description
        if pn.startswith("CARD-MD-GEN"):
            parts_summary.append(f"{qty} Generic Cards")
            continue
        elif pn.startswith("CARD-MD-"):
            parts_summary.append(f"{qty} Cards")
            continue

        # Kits: use short names from KIT_NAMES, not verbose descriptions
        if pn.startswith("KIT-"):
            matched = False
            for prefix, name in KIT_NAMES.items():
                if pn.replace("-DS", "").startswith(prefix):
                    parts_summary.append(f"{qty} {name}s" if qty > 1 else name)
                    matched = True
                    break
            if not matched:
                # Unknown kit — use short description or part number
                short = _short_desc(desc) if desc else pn
                parts_summary.append(f"{qty} {short}" if qty > 1 else short)
            continue

        # Known parts: use short name from PART_NAMES
        base_pn = pn.replace("-DS", "").upper()
        if base_pn in PART_NAMES:
            short = PART_NAMES[base_pn]
            parts_summary.append(f"{qty} {short}" if qty > 1 else short)
        elif desc:
            short = _short_desc(desc)
            label = f"{qty} {short}" if qty > 1 else short
            parts_summary.append(label)
        else:
            parts_summary.append(f"{qty} {pn}" if qty > 1 else pn)

    tag = ", ".join(parts_summary[:3])
    name = clean_name(customer_name)
    if name:
        tag += f" ({name})"
    return tag


# ── DS swap helper ───────────────────────────────────────────────────────────

def manual_ds_swap(page, products: list) -> None:
    """
    When the -DS swap dialog doesn't fire, manually delete non-DS parts
    and add their -DS equivalents with the same qty/price.

    Re-queries the DOM each iteration because deleting/adding rows
    invalidates previous locator references.
    """
    swapped = 0
    max_passes = 10  # Safety limit

    for _ in range(max_passes):
        # Re-query rows fresh each pass
        rows = page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all()
        found = False
        for row in rows:
            try:
                pn_loc = row.locator('th[scope="row"] a')
                if pn_loc.count() == 0:
                    continue
                pn = pn_loc.first.inner_text().strip()
                if pn.endswith("-DS"):
                    continue  # Already swapped
                base = pn.upper().replace("-DS", "")
                if base not in EFS_PRODUCTS:
                    continue

                # Found one to swap — handle it then break to re-query
                found = True
                ds_pn = pn + "-DS"
                inputs = row.locator('input').all()
                qty = inputs[0].input_value().strip() if len(inputs) >= 1 else "1"
                price = inputs[1].input_value().strip() if len(inputs) >= 2 else ""

                # Delete non-DS row
                delete_btn = row.locator('a:has(svg), button:has(svg), svg[data-icon="trash-alt"]').first
                if delete_btn.count() > 0:
                    delete_btn.click()
                    time.sleep(1)
                    try:
                        confirm = page.locator('button').filter(has_text="OK")
                        if confirm.count() > 0 and confirm.first.is_visible():
                            confirm.first.click()
                            time.sleep(0.5)
                    except Exception:
                        pass
                    print(f"[ACTION] Deleted {pn}")

                # Add -DS version
                action_add_part(page, ds_pn, int(qty) if qty.isdigit() else 1)

                # Set price if we had one
                if price and price not in ("0", "0.00"):
                    new_rows = page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all()
                    for nr in new_rows:
                        try:
                            nr_pn = nr.locator('th[scope="row"] a')
                            if nr_pn.count() > 0 and nr_pn.first.inner_text().strip() == ds_pn:
                                nr_inputs = nr.locator('input').all()
                                if len(nr_inputs) >= 2:
                                    nr_inputs[1].click()
                                    nr_inputs[1].fill(price)
                                    print(f"[ACTION] Set {ds_pn} price to {price}")
                                break
                        except Exception:
                            continue
                print(f"[ACTION] Manual swap: {pn} -> {ds_pn} qty={qty}")
                swapped += 1
                break  # Re-query DOM from the top
            except Exception as e:
                print(f"[WARNING] Manual swap failed: {e}")
                break

        if not found:
            break  # All rows are either -DS or non-EFS

    if swapped:
        print(f"[ACTION] Manual DS swap complete: {swapped} product(s) swapped")


# ── Main playbook ────────────────────────────────────────────────────────────

def run(page, so_id):
    """Execute the parts/readers order playbook."""
    print("\n" + "=" * 60)
    print(f"  PARTS ORDER PLAYBOOK -- SO-{so_id}")
    print("=" * 60)
    t_start = time.time()

    # Step 1: Read SO
    t0 = time.time()
    print("\n--- Step 1: Read SO ---")
    navigate_to_so(page, so_id)

    tag = page.locator('input[name="description"]').input_value().strip()
    print(f"Tag: {tag or '(empty)'}")

    customer_name = read_customer_name(page)
    products = read_products(page)
    missing = read_missing_parts(page)
    shipping = read_shipping_to(page)

    print(f"Products: {len(products)}")
    for p in products:
        print(f"  {p['part_number']:30s} qty={p['qty']}")
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 2: Read SOR shipping data
    t0 = time.time()
    print("\n--- Step 2: Read SOR shipping data ---")
    sor_data = read_sor_data(page)
    sor_shipping = sor_data.get("shipping_method", "")
    sor_comments = sor_data.get("shipping_comments", "")
    # The Required Delivery Date (e.g. "Next Day") signals urgency even when the shipping-method
    # field is blank -- feed it to the mapper so Next Day/Overnight doesn't fall through to Ground.
    required_raw = sor_data.get("required_date_raw", "")
    efs_ship_via = map_sor_to_efs_shipping(sor_shipping, f"{sor_comments} {required_raw}")
    print(f"SOR shipping: '{sor_shipping}' (req: '{required_raw}') -> EFS Ship Via: '{efs_ship_via}'")
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 3: Route products
    print("\n--- Step 3: Check product availability ---")
    has_readers = any(p["part_number"].upper().startswith("CR-") for p in products)
    efs_products = []
    non_efs_products = []
    for p in products:
        pn = p["part_number"]
        pn_upper = pn.upper()
        qty = int(p["qty"]) if str(p["qty"]).isdigit() else 0
        if qty <= 0:
            continue
        # Generic cards under 1000 → EFS as BOX200
        if pn_upper.startswith("CARD-MD-GEN") and qty < 1000:
            box_qty = -(-qty // 200)  # ceiling division
            efs_products.append({"part": "CARD-MD-GEN01-BOX200", "qty": box_qty})
            print(f"  [EFS]         {pn:25s} qty={qty} -> CARD-MD-GEN01-BOX200 x{box_qty}")
        elif is_efs_product(pn):
            efs_products.append({"part": pn, "qty": qty})
            print(f"  [EFS]         {pn:25s} qty={qty}")
        # Kit expansion: kit not in EFS as whole unit, but components are
        elif pn_upper.replace("-DS", "") in KIT_EFS_COMPONENTS:
            expanded = expand_kit_for_efs(pn, qty)
            efs_products.extend(expanded)
            comp_str = " + ".join(f"{c['part']} x{c['qty']}" for c in expanded)
            print(f"  [EFS EXPAND]  {pn:25s} qty={qty} -> {comp_str}")
        else:
            non_efs_products.append({"part": pn, "qty": qty})
            print(f"  [NOT IN EFS]  {pn:25s} qty={qty}")

    # Reader kits always ship VUnics — BOM cables go with them
    if has_readers:
        use_efs = False
        use_vunics = True
        print(f"\n[INFO] Reader kit order -- ships from VUnics")
    elif len(efs_products) > 0 and len(non_efs_products) == 0:
        use_efs = True
        use_vunics = False
    elif efs_products and non_efs_products:
        use_efs = False
        use_vunics = False
        print(f"\n[INFO] Mixed order: {len(efs_products)} EFS + {len(non_efs_products)} non-EFS")
        print("[INFO] Review shipment routing -- may need split shipment")
    else:
        # Default: non-EFS, non-reader, non-Cents → VUnics
        use_efs = False
        use_vunics = True
        print(f"\n[INFO] Ships from VUnics")

    # Step 4: Set tag
    t0 = time.time()
    print("\n--- Step 4: Set tag ---")
    if tag:
        print(f"Tag already set: '{tag}' -- keeping it")
        tag_value = tag
    else:
        cust_name = customer_name or shipping.get("company", "")
        tag_value = build_parts_tag(products, cust_name)
        print(f"Tag: {tag_value}")
        action_set_tag(page, tag_value)
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 5: Set shipment
    t0 = time.time()
    if use_efs:
        print("\n--- Step 5: Set Shipment By -> 3PL (EFS) ---")
        # Drive the SO Shipment Method off the COMPUTED ship-via (which already factors in the
        # Required Delivery Date) -- not just sor_shipping. Otherwise a blank shipping-method
        # field with a 'Next Day' required date sets the SO to Ground (EFS overnight, SO Ground).
        if "OVERNIGHT" in efs_ship_via.upper():
            set_shipment(page, method="Next Day", shipped_by="3PL - EFS")
        else:
            set_shipment(page, method="Ground", shipped_by="3PL - EFS")

        # Handle -DS swap dialog
        time.sleep(0.3)
        swapped = False
        try:
            dialog = page.locator('text=Proceed with swap')
            dialog.wait_for(state="visible", timeout=500)
            page.locator('button').filter(has_text="OK").first.click()
            print("[ACTION] Clicked OK on part swap dialog (-DS conversion)")
            time.sleep(1.5)
            swapped = True
        except Exception:
            print("[INFO] No swap dialog -- checking if manual swap needed")

        if not swapped:
            manual_ds_swap(page, efs_products)
    elif use_vunics:
        print("\n--- Step 5: Set Shipment By -> VUnics ---")
        if "OVERNIGHT" in efs_ship_via.upper():
            set_shipment(page, method="Next Day", shipped_by="VUnics")
        elif "FREIGHT" in f"{sor_shipping} {required_raw}".upper():
            set_shipment(page, method="Freight (Skid)", shipped_by="VUnics")
        else:
            set_shipment(page, method="Ground", shipped_by="VUnics")
    else:
        print("\n--- Step 5: Shipment By ---")
        if non_efs_products:
            print("[INFO] Non-EFS products detected -- set Shipment By manually")
            print("[INFO] Options: VUnics (Shrewsbury), TRG, or 3PL")
        else:
            print("[INFO] No products to ship")
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 6: Missing parts
    if missing:
        print(f"\n--- Step 6: Missing parts ({len(missing)}) ---")
        for m in missing:
            print(f"  {m['part_number']:20s} -> {m['associated_part']:15s} qty={m['qty']:5s} {m['description'][:40]}")
        print("[INFO] Review missing parts -- add manually if needed with --add-part")

    # Step 7: Save
    t0 = time.time()
    print("\n--- Step 7: Save SO ---")
    save_so(page, accept_sor=False)
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 9: Post-save output by fulfillment path
    if use_efs:
        js_snippet = build_efs_js_snippet(so_id, shipping, efs_products, efs_ship_via)
        clipboard_ok = copy_to_clipboard(js_snippet)

        print("\n" + "=" * 50)
        print(f"  EFS ORDER -- SOP-{so_id}")
        print("=" * 50)
        print(f"  Ship Via:  {efs_ship_via}")
        attn = shipping.get("attn_name", "")
        print(f"  To:        {attn}, {shipping.get('company', '')}")
        print(f"  Address:   {shipping.get('address', '')}, {shipping.get('city', '')}, "
              f"{shipping.get('state', '')} {shipping.get('zip', '')}")
        print(f"  Phone:     {shipping.get('phone', '')}")
        print(f"  Products:")
        for p in efs_products:
            print(f"    {p['part']:25s}  qty={p['qty']}")
        if clipboard_ok:
            print(f"\n  >>> JS auto-fill COPIED TO CLIPBOARD <<<")
            print(f"  EFS Console -> Ctrl+V -> Enter")
            print(f"  (clobbered your clipboard? run `recopy` to put it back.)")
        else:
            print(f"\n  JS auto-fill (paste in EFS browser console):")
            print(js_snippet)
        print("=" * 50)

    elif use_vunics:
        print("\n" + "=" * 50)
        print(f"  VUNICS ORDER -- SOP-{so_id}")
        print("=" * 50)
        print(f"  Ships from VUnics (Shrewsbury)")
        print("=" * 50)

    # Summary
    elapsed = time.time() - t_start
    print(f"\n  Total: {elapsed:.1f}s")
    print("\n" + "=" * 60)
    print("  PARTS ORDER COMPLETE")
    print(f"  SO-{so_id}: {tag_value}")
    if use_efs:
        print(f"  Ship via EFS: {efs_ship_via}")
    elif use_vunics:
        print(f"  Ship via VUnics")
    if missing:
        print(f"  Missing parts ({len(missing)}):")
        for m in missing:
            print(f"    {m['part_number']} -> {m['associated_part']} qty={m['qty']}")
    print("  Remaining:")
    if use_efs:
        print("    Fill EFS form in browser")
        print("    Verify Order -> Submit")
    print("    Work State -> Placed -> Accept SOR")
    print("=" * 60)
