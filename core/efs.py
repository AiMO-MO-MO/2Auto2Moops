"""
EFS (eFulfillmentService) integration — product catalog, JS snippet builder, shipping mapping.

EFS is the 3PL warehouse for drop-shipping reader kits, cables, and accessories.
Not all MOOPS products exist in EFS — check EFS_PRODUCTS before routing.

EFS login is unreliable in Playwright. Instead of driving the EFS page directly,
we generate a JavaScript snippet the operator pastes into EFS's browser console.
"""

import os
import re

# ── EFS Product Catalog ──────────────────────────────────────────────────────
# Parts stocked at EFS (eFulfillmentService.com).
# If a part is NOT in this set, it ships from Shrewsbury/VUnics or Slack→SF.
# Updated from the EFS product list (screenshots May 2026).

EFS_PRODUCTS = {
    # Back plates / mounting
    "01-02-23", "01-02-26", "01-04-04", "01-05-50", "01-05-51", "01-05-55",
    "01-05-56", "01-06-65",
    # Cables
    "02-03-01", "02-03-33", "02-06-07", "02-06-30", "02-06-31", "02-06-32C",
    "02-06-50",
    # Accessories / misc
    "03-01-101", "03-01-102", "03-01-21", "03-01-32", "03-01-42", "03-01-46",
    "03-01-60", "03-01-77", "03-01-87", "03-01-95",
    # Reader boards (assemblies)
    "ASSY-02-02-02", "ASSY-02-02-08", "ASSY-02-02-09", "ASSY-02-02-10",
    "ASSY-02-02-11", "ASSY-02-02-12", "ASSY-02-03-11", "ASSY-CD-SK-AD1",
    # Cards
    "CARD-MD-GEN01-BOX200",
    # Kits
    "KIT-DEXTER01", "KIT-DOORACCESS-02", "KIT-MDBVENDING-01",
    "KIT-MEDECO-01", "KIT-POS-01", "KIT-VENDRITE-01",
}


def is_efs_product(part_number: str) -> bool:
    """Check if a part exists in the EFS catalog (ignoring -DS suffix)."""
    pn = part_number.upper().replace("-DS", "")
    return pn in EFS_PRODUCTS


# ── Kit → EFS component expansion ──────────────────────────────────────────
# Kits NOT stocked as whole units at EFS, but whose individual parts ARE.
# When a parts order includes one of these kits, expand into components
# so the EFS JS snippet fills the right rows.
#
# Format: "KIT-NAME": [("part_number", qty_per_kit), ...]

KIT_EFS_COMPONENTS = {
    "KIT-A35": [
        ("03-01-95", 1),   # PAX A35 pinpad
        ("01-02-23", 1),   # PAX A35 pinpad holder
    ],
    # KIT-P630 components (03-01-99 + 01-02-25) are NOT stocked at EFS.
    # KIT-A35-ATTACHMENT uses 01-02-24 which is NOT at EFS.
    # KIT-P630_ATTACHMENT uses 01-02-27 which is NOT at EFS.
    # These ship from VUnics.
}


def expand_kit_for_efs(part_number: str, qty: int) -> list:
    """
    If a kit can be broken into EFS-stocked components, return the expanded
    list. Otherwise return None (caller should use the normal routing).

    Args:
        part_number: e.g. "KIT-A35" or "KIT-A35-DS"
        qty: number of kits ordered

    Returns:
        list of {"part": str, "qty": int} for EFS, or None if not expandable.
    """
    pn = part_number.upper().replace("-DS", "")
    components = KIT_EFS_COMPONENTS.get(pn)
    if not components:
        return None
    return [{"part": comp_pn, "qty": comp_qty * qty} for comp_pn, comp_qty in components]


def route_products_for_efs(products: list) -> tuple:
    """
    Split a product list into EFS-eligible and non-EFS items.
    Kits in KIT_EFS_COMPONENTS are expanded into individual parts.
    Kits already in EFS_PRODUCTS ship as whole kits (no expansion).

    Args:
        products: list of {"part": str, "qty": int}

    Returns:
        (efs_products, other_products) — two lists of {"part": str, "qty": int}
    """
    efs = []
    other = []
    for p in products:
        pn = p["part"].upper().replace("-DS", "")
        qty = int(p["qty"])

        # 1. Direct EFS match (includes whole kits like KIT-DEXTER01)
        if pn in EFS_PRODUCTS:
            efs.append({"part": pn, "qty": qty})
        # 2. Kit expansion
        elif expanded := expand_kit_for_efs(pn, qty):
            efs.extend(expanded)
        # 3. Everything else → VUnics/other
        else:
            other.append({"part": p["part"], "qty": qty})

    return efs, other


# ── Shipping mapping ─────────────────────────────────────────────────────────

def map_sor_to_efs_shipping(sor_method: str, sor_comments: str = "") -> str:
    """
    Map SOR shipping method to EFS Ship Via option.
    Returns: 'FedEx Standard Overnight' or 'FedEx Ground'
    """
    combined = f"{sor_method} {sor_comments}".upper()
    if "NEXT DAY" in combined or "OVERNIGHT" in combined:
        return "FedEx Standard Overnight"
    # Ground, Freight, 2-3 Days, or anything else → Ground
    return "FedEx Ground"


# ── JS snippet builder ───────────────────────────────────────────────────────

def build_efs_js_snippet(so_id: int, shipping: dict, products: list,
                         efs_ship_via: str) -> str:
    """
    Build a JavaScript IIFE that auto-fills the EFS new-order form.
    Operator pastes this into EFS browser console (F12 → Console → Ctrl+V → Enter).

    Args:
        so_id: Sales Order ID (used as EFS order reference SOP-{so_id})
        shipping: dict with keys: attn_name, company, address, city, state, zip, phone
        products: list of {"part": str, "qty": int} — only EFS-eligible products
        efs_ship_via: EFS shipping option string
    """
    # Parse name into first/last
    attn = shipping.get("attn_name", "")
    name_parts = attn.split(None, 1) if attn else ["", ""]
    first_name = name_parts[0] if len(name_parts) > 0 else ""
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    # Escape single quotes for JS string literals
    esc = lambda s: s.replace("'", "\\'")
    company = esc(shipping.get("company", ""))
    address = esc(shipping.get("address", ""))
    city = esc(shipping.get("city", ""))
    state = esc(shipping.get("state", ""))
    zipcode = esc(shipping.get("zip", ""))
    phone = esc(shipping.get("phone", ""))
    first_name = esc(first_name)
    last_name = esc(last_name)

    # Build product-fill JS (strip -DS suffix — EFS uses base part numbers)
    product_js = ""
    for p in products:
        base_pn = p["part"].replace("-DS", "").upper()
        product_js += f"""
    for(const row of document.querySelectorAll('tr')){{
        const tds=row.querySelectorAll('td');
        if(tds.length<2)continue;
        if(tds[0].textContent.trim().toUpperCase()==='{base_pn}'){{
            const inp=row.querySelector('input[type="text"]');
            if(inp){{inp.value='{p["qty"]}';}}
            break;
        }}
    }}"""

    return f"""(function(){{
    const f=(n,v)=>{{const el=document.querySelector('input[name="'+n+'"]');if(el)el.value=v;}};
    f('custPhone','{phone}');
    f('custBillFName','{first_name}');
    f('custBillLName','{last_name}');
    f('custBillCompany','{company}');
    f('custBillAddress1','{address}');
    f('custBillCity','{city}');
    f('custBillZip','{zipcode}');
    f('orderNum','SOP-{so_id}');
    const cb=document.querySelector('input[name="orderReqSignature"]');if(cb)cb.checked=true;
    const sel=document.querySelector('select[name="custBillStateID"]');
    if(sel){{const opt=Array.from(sel.options).find(o=>o.text.includes('({state})'));if(opt)sel.value=opt.value;}}
    const sv=document.querySelector('select[name="orderShipBy"]');
    if(sv){{const opt=Array.from(sv.options).find(o=>o.text.includes('{efs_ship_via}'));if(opt)sv.value=opt.value;}}
    {product_js}
    console.log('EFS filled: SOP-{so_id}');
}})();"""


def copy_to_clipboard(text: str) -> bool:
    """
    Copy text to Windows clipboard. Uses type-pipe-clip for reliability.
    Returns True if successful.
    """
    try:
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tmp = os.path.join(script_dir, "_efs_snippet.js")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.system(f'type "{tmp}" | clip')
        return True
    except Exception:
        return False


def recopy_last_snippet() -> bool:
    """Re-copy the last EFS snippet (_efs_snippet.js) to the clipboard -- safety net for
    when the clipboard gets overwritten before pasting into EFS. True if a snippet existed."""
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = os.path.join(script_dir, "_efs_snippet.js")
    if not os.path.exists(tmp):
        print("[RECOPY] No saved EFS snippet (_efs_snippet.js missing) -- re-run the parts order.")
        return False
    os.system(f'type "{tmp}" | clip')
    print("[RECOPY] Last EFS snippet re-copied to clipboard. EFS console -> Ctrl+V -> Enter.")
    return True
