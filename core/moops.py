"""
MOOPS page actions -- low-level Playwright helpers for interacting with SO pages.

Each function does ONE thing and logs what it did.
These are the building blocks that playbooks compose together.
"""

import re
import time

from playwright.sync_api import Page

MOOPS_BASE = "https://moops.mitechisys.com"

STATE_ABBREVIATIONS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}


def abbreviate_state(address: str) -> str:
    """Replace full state names with abbreviations in an address string."""
    for full, abbr in STATE_ABBREVIATIONS.items():
        # Match full state name (case-insensitive) surrounded by comma/space boundaries
        pattern = r'(?<=,\s)' + re.escape(full) + r'(?=[,\s]|$)'
        address = re.sub(pattern, abbr, address, flags=re.IGNORECASE)
    return address


def timed(func):
    """Decorator that prints elapsed time for any function call."""
    def wrapper(*args, **kwargs):
        t0 = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - t0
        print(f"  [{func.__name__} {elapsed:.1f}s]")
        return result
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# VAC decoder helpers
# ---------------------------------------------------------------------------

def decode_vac(part_number: str) -> dict:
    """
    Decode a VAC part number into its components.
    Format: VACXX-YZ-WM
      XX = cabinet type (01=Mini, 02=Cashless, 03=Cash front, 04=Cash back, 07=Touch back, 08=Touch front)
      Y  = bill acceptor (0=None, 1-6 = various)
      Z  = credit/debit pinpad (0=None, 1=IPP320, 2=PAX S300, 3=VX820, 4=Ingenico AU)
      W  = card dispenser (0=None, 1=110-card, 2=260-card)
      M  = cell modem (0=None, 1=4G modem)
    """
    pn = part_number.upper().strip()
    result = {
        "raw": pn,
        "cabinet": "",
        "bill_acceptor": "0",
        "pinpad_digit": "0",
        "card_dispenser": "0",
        "modem": "0",
        "needs_pinpad": False,
        "needs_card_dispenser": False,
        "is_touchscreen": False,
    }
    if not pn.startswith("VAC"):
        return result

    # Split: VAC07-42-20 -> ["VAC07", "42", "20"]
    segments = pn.split("-")
    cabinet = segments[0][3:]  # "07" from "VAC07"
    result["cabinet"] = cabinet
    result["is_touchscreen"] = cabinet in ("07", "08")

    if len(segments) >= 2 and len(segments[1]) >= 2:
        result["bill_acceptor"] = segments[1][0]
        result["pinpad_digit"] = segments[1][1]
        result["needs_pinpad"] = segments[1][1] != "0"

    if len(segments) >= 3 and len(segments[2]) >= 2:
        result["card_dispenser"] = segments[2][0]
        result["modem"] = segments[2][1]
        result["needs_card_dispenser"] = segments[2][0] != "0"

    return result


def determine_pinpad_kit(processor_type: str) -> str:
    """
    Determine pinpad kit based on SOR processor type.
    '' or '6' = Stripe -> KIT-P630
    '2' = Fortis (EBT) -> KIT-A35

    NOTE: Stripe kit changed from KIT-S700 to KIT-P630 (May 2026).
    Update this if the kit changes again.
    """
    pt = processor_type.upper()
    if "FORTIS" in pt or "EBT" in pt or processor_type == "2":
        return "KIT-A35"
    return "KIT-P630"


# Pinpad hardware by processor family (part numbers from CLAUDE.md's EFS kit map). A combo VAC's
# missing-parts lists BOTH families; only the order's processor family is correct, so the run
# skips the other. (Combo full-kit-vs-attachment over-add is fixed in action_add_required_parts
# rule 5c: a pinpad ATTACHMENT in missing parts drops the rule-based full kit.)
_PINPAD_PARTS_A35 = {"KIT-A35", "KIT-A35-ATTACHMENT", "03-01-95", "01-02-23", "01-02-24"}
_PINPAD_PARTS_P630 = {"KIT-P630", "KIT-P630_ATTACHMENT", "KIT-P630-ATTACHMENT",
                      "03-01-99", "01-02-25", "01-02-27"}


# ---------------------------------------------------------------------------
# Read actions (extract data from the page)
# ---------------------------------------------------------------------------

def read_sale_or_route(page: Page) -> str:
    """Read the Sale/Route dropdown value. Returns 'Sale', 'Route', or ''."""
    try:
        sel = page.locator('select').all()
        for s in sel:
            options = s.locator('option').all_inner_texts()
            if 'Sale' in options and 'Route' in options:
                val = s.input_value()
                # input_value returns the option value, get the label
                selected = s.locator('option:checked')
                if selected.count() > 0:
                    return selected.first.inner_text().strip()
                return val.strip()
    except Exception:
        pass
    return ""


def read_order_type(page: Page) -> str:
    """Read the Order Type dropdown (select[name='sales_type_id']) selected label, e.g.
    'System - Laundromat', 'System - Multi Housing', 'Parts', 'Cards Only'. Multi-Housing is a
    route (only hardware is done; no provisioning). Returns '' if not found."""
    try:
        sel = page.locator('select[name="sales_type_id"]')
        if sel.count() == 0:
            return ""
        opt = sel.first.locator('option:checked')
        if opt.count() > 0:
            return (opt.first.inner_text() or "").strip()
        return (sel.first.input_value() or "").strip()
    except Exception:
        return ""


def read_tag(page: Page) -> str:
    """Read the current Tag field value."""
    try:
        inputs = page.locator('input[type="text"]').all()
        for inp in inputs:
            try:
                val = inp.input_value()
                parent_text = inp.evaluate('el => el.closest("tr, div, td")?.innerText || ""')
                if "Tag" in parent_text and "Tax" not in parent_text:
                    return val.strip()
            except Exception:
                continue
    except Exception:
        pass
    return ""


def read_products(page: Page) -> list:
    """Read all products currently on the SO, including description."""
    products = []
    rows = page.locator('tr[id^="existing_part_order_"]').all()
    for row in rows:
        try:
            pn_loc = row.locator('th[scope="row"] a')
            if pn_loc.count() == 0:
                continue
            part_number = pn_loc.first.inner_text().strip()
            part_href = pn_loc.first.get_attribute("href") or ""
            qty = 0
            inp_loc = row.locator('input')
            if inp_loc.count() > 0:
                qty_str = inp_loc.first.input_value().strip()
                qty = int(qty_str) if qty_str.isdigit() else 0
            row_id = row.get_attribute("id") or ""
            # Read description from the editable-control cell
            desc = ""
            desc_loc = row.locator('td.description')
            if desc_loc.count() == 0:
                desc_loc = row.locator('td.editable-control')
            if desc_loc.count() > 0:
                desc = desc_loc.first.inner_text().strip()
            po_link = ""
            po_href = ""
            try:
                po = row.locator('a[href*="purchase"], a[href*="po"], a').filter(has_text="PO-").first
                if po.count() > 0:
                    po_link = po.inner_text().strip()
                    po_href = po.get_attribute("href") or ""
            except Exception:
                pass
            products.append({
                "part_number": part_number,
                "href": part_href,
                "qty": qty,
                "row_id": row_id,
                "description": desc,
                "po_link": po_link,
                "po_href": po_href,
                "has_po": bool(po_link),
            })
        except Exception:
            continue
    return products


def read_missing_parts(page: Page) -> list:
    """Read the 'Missing part associations detected' section."""
    missing = []
    try:
        header = page.locator('text=Missing part associations detected')
        if header.count() == 0:
            print("[READ] No missing parts section found")
            return missing

        tables = page.locator('table').all()
        for table in tables:
            headers = table.locator('th').all_inner_texts()
            if 'Associated Part' in headers or 'Part Number' in ' '.join(headers):
                rows = table.locator('tbody tr').all()
                if not rows:
                    rows = table.locator('tr').all()[1:]
                for row in rows:
                    # Missing parts table uses th for first two cols, td for the rest
                    cells = row.locator('th, td').all()
                    if len(cells) >= 4:
                        part_number = cells[0].inner_text().strip()
                        associated = cells[1].inner_text().strip()
                        description = cells[2].inner_text().strip()
                        qty = cells[3].inner_text().strip()
                        missing.append({
                            "part_number": part_number,
                            "associated_part": associated,
                            "description": description,
                            "qty": qty,
                        })
                break
    except Exception as e:
        print(f"[READ] Error reading missing parts: {e}")
    return missing


def read_vacs(page: Page) -> list:
    """Read VAC parts from the product table."""
    vacs = []
    for p in read_products(page):
        if p["part_number"].upper().startswith("VAC"):
            vacs.append(p)
    return vacs


def read_customer_name(page: Page) -> str:
    """Read customer/location name from Internal Mitech Notes."""
    try:
        notes = page.locator('textarea[name="notes_to_admin"]').input_value()
        for line in notes.splitlines():
            if line.strip().startswith("Location Name:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


@timed
def read_internal_notes(page: Page) -> dict:
    """
    Parse all fields from Internal Mitech Notes (textarea name="notes_to_admin").

    The notes contain all SOR data copied over at conversion:
      Location Name, Location Address, New Contact Name/Email/Phone,
      Card Design Type, Card Design Contact, Comments, etc.

    Returns dict with parsed fields. Multi-line values (like address) are
    joined with ", " for single-line use.
    """
    result = {
        "location_name": "",
        "location_address": "",
        "contact_name": "",
        "contact_email": "",
        "contact_phone": "",
        "card_design_type": "",
        "card_design_contact": "",
        "comments": "",
    }

    try:
        notes = page.locator('textarea[name="notes_to_admin"]').input_value()
    except Exception:
        print("[READ] Could not read Internal Mitech Notes")
        return result

    if not notes:
        return result

    lines = notes.splitlines()

    # Known single-line fields (key: prefix to match)
    single_fields = {
        "Location Name:": "location_name",
        "New Contact Name:": "contact_name",
        "New Contact Email:": "contact_email",
        "New Contact Phone:": "contact_phone",
        "Card Design Type:": "card_design_type",
        "Card Design Contact:": "card_design_contact",
        "Comments:": "comments",
    }

    # Parse line by line
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Check single-line fields
        matched = False
        for prefix, key in single_fields.items():
            if line.startswith(prefix):
                result[key] = line[len(prefix):].strip()
                matched = True
                break

        # Location Address is multi-line: collect until next known field
        # Skip the business name line (matches location_name) and "United States"
        # Abbreviate state names (e.g. "New York" → "NY")
        if line.startswith("Location Address:"):
            addr_parts = []
            val = line[len("Location Address:"):].strip()
            if val:
                addr_parts.append(val)
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                # Stop if we hit another known field
                if any(next_line.startswith(p) for p in single_fields) or next_line.startswith("Location Address:") or next_line.startswith("Existing End Customer") or next_line.startswith("---"):
                    break
                if next_line:
                    addr_parts.append(next_line)
                i += 1
            # Clean up: remove business name if it's the first line
            loc_name = result.get("location_name", "")
            if addr_parts and loc_name and addr_parts[0].lower().startswith(loc_name.lower()[:10]):
                addr_parts = addr_parts[1:]
            # Remove "United States" if last part
            if addr_parts:
                last = addr_parts[-1]
                last = re.sub(r',?\s*United States\s*$', '', last, flags=re.IGNORECASE).strip()
                if last:
                    addr_parts[-1] = last
                else:
                    addr_parts = addr_parts[:-1]
            raw_addr = ", ".join(addr_parts)
            result["location_address"] = abbreviate_state(raw_addr)
            continue

        i += 1

    # Log what we found
    for key, val in result.items():
        if val:
            print(f"[READ] Notes — {key}: {val}")

    return result


def read_existing_customer_id(page: Page) -> dict:
    """
    Parse Existing End Customer info from Internal Mitech Notes.
    Format: "Existing End Customer: 833 Village Laundromat (01707)"

    Returns dict:
        {"name": "833 Village Laundromat", "id": "01707"}
    or empty dict if not found.

    Used for repeat customers -- card ownership should use the real customer ID,
    not Mitech. Also means no new Admin Portal user needed.
    """

    try:
        notes = page.locator('textarea[name="notes_to_admin"]').input_value()
        for line in notes.splitlines():
            stripped = line.strip()
            if stripped.startswith("Existing End Customer"):
                # Format: "Existing End Customer: Name (ID)"
                # or "Existing End Customer: Name (ID) - extra info"
                after_colon = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
                if not after_colon:
                    continue
                # Extract ID from parentheses at end: "Name (01707)"
                id_match = re.search(r'\((\d{3,6})\)', after_colon)
                if id_match:
                    cust_id = id_match.group(1)
                    cust_name = after_colon[:id_match.start()].strip()
                    print(f"[READ] Existing End Customer: '{cust_name}' (ID: {cust_id})")
                    return {"name": cust_name, "id": cust_id}
                else:
                    # No parenthesized ID -- return name only
                    print(f"[READ] Existing End Customer (no ID): '{after_colon}'")
                    return {"name": after_colon, "id": ""}
    except Exception as e:
        print(f"[READ] Error reading existing customer: {e}")
    return {}


def find_reference_so(text: str) -> str:
    """If the notes describe a replacement/exchange of another order, return that
    SO id (digits only); '' otherwise. Only fires when a replacement keyword AND an
    SO reference are both present, so it won't grab unrelated SO mentions.

    e.g. 'This is for replacement/exchange for order SO-16664' -> '16664'
    """
    t = text or ""
    if not re.search(r'replacement|exchange|replaces?\b|swap\b', t, re.I):
        return ""
    m = re.search(r'\bSO[-\s#]?(\d{4,6})\b', t, re.I)
    return m.group(1) if m else ""


def read_so_end_customer(page: Page) -> dict:
    """Read the End Customer actually set on the current SO page (the validity
    search widgets), not the notes. Returns {id, name, location_id} -- blank parts
    if nothing is set. Used to inherit a replacement order's customer from the SO
    it references.
    """
    out = {"id": "", "name": "", "location_id": ""}
    # Guard with count() FIRST (instant) before input_value(): when the End-Customer widget isn't
    # on this SO page, a bare .first.input_value() waits the 30s default for an element that will
    # never appear -- two of them = a 60s "freeze" on the snapshot. count()==0 -> skip immediately;
    # if present, cap the read at 2s. (set_so_end_customer already guards this way.)
    cust = page.locator('#validity_customer-search')
    if cust.count():
        try:
            v = (cust.first.input_value(timeout=2000) or "").strip()
            m = re.match(r'(\d{3,6})\s*-\s*(.+)', v)
            if m:
                out["id"], out["name"] = m.group(1), m.group(2).strip()
        except Exception:
            pass
    loc = page.locator('[id^="validity_$location_filter"]')
    if loc.count():
        try:
            lv = (loc.first.input_value(timeout=2000) or "").strip()
            lm = re.match(r'(\d{5,9})\s*-', lv)
            if lm:
                out["location_id"] = lm.group(1)
        except Exception:
            pass
    if out["id"]:
        print(f"[READ] SO End Customer: '{out['name']}' ({out['id']})"
              + (f" location {out['location_id']}" if out["location_id"] else ""))
    return out


def read_card_end_customer(page: Page, card_part_number: str, href: str = "") -> dict:
    """Read End-Customer ownership from an existing CARD-MD part page."""
    out = {"id": "", "name": ""}
    if not card_part_number:
        return out
    url = href or f"{MOOPS_BASE}/part?part_number={card_part_number}"
    if url.startswith("/"):
        url = f"{MOOPS_BASE}{url}"
    print(f"[READ] Card owner lookup: {card_part_number}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
    except Exception as e:
        print(f"[CARDS] Could not open {card_part_number} for owner lookup ({e})")
        return out

    candidates = []
    for sel in (
        "#validity_portal_customer_search",
        'input[name*="portal_customer"]',
        'input[id*="customer"]',
    ):
        try:
            loc = page.locator(sel)
            if loc.count():
                val = (loc.first.input_value(timeout=1500) or "").strip()
                if val:
                    candidates.append(val)
        except Exception:
            pass
    if not candidates:
        try:
            text = page.locator("body").first.inner_text(timeout=3000)
            candidates.append(text)
        except Exception:
            pass

    for val in candidates:
        m = re.search(r'(\d{4,6})\s*[-)]\s*([A-Za-z][^\n\r()]*)?', val)
        if m:
            out["id"] = m.group(1)
            out["name"] = (m.group(2) or "").strip(" -")
            break
        m = re.search(r'([A-Za-z][^\n\r()]*)\((\d{4,6})\)', val)
        if m:
            out["name"] = m.group(1).strip()
            out["id"] = m.group(2)
            break
    if out["id"]:
        print(f"[READ] Card owner: {out['id']} {out.get('name', '')}".rstrip())
    else:
        print(f"[CARDS] Could not read End-Customer from {card_part_number}.")
    return out


@timed
def read_schedule_capacity(page: Page) -> list:
    """
    Navigate to the Sales Orders page and read weighted VAC capacity per week.
    Parses the Open System Sales Orders tables (id^="placed-system-orders"),
    counts VACs from the Tag/PO column using same logic as the Vac Count bookmarklet.

    Weights: VAC01-06 = 0.5, VAC07-08 = 1.0
    Soft cap: 35 (leave room for emergencies). Hard cap: 45.

    Returns list of dicts sorted by week:
        [{"week": "May 25 - May 31", "total": 36.0, "counts": {"VAC07": 30, "VAC02": 7, ...}}, ...]
    """


    weights = {
        "VAC01": 0.5, "VAC02": 0.5, "VAC03": 0.5, "VAC04": 0.5,
        "VAC05": 0.5, "VAC06": 0.5, "VAC07": 1.0, "VAC08": 1.0,
    }

    # Save current URL to navigate back
    original_url = page.url

    # Navigate to sales orders page
    sales_url = f"{MOOPS_BASE}/orders"
    print(f"[NAV] Going to Sales Orders page: {sales_url}")
    page.goto(sales_url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2000)

    weeks = []
    tables = page.locator('table[id^="placed-system-orders"]').all()
    print(f"[READ] Found {len(tables)} system order week tables")

    for table in tables:
        # Week label is in the heading element before the table
        week_label = ""
        try:
            heading = table.evaluate('el => el.previousElementSibling ? el.previousElementSibling.innerText : ""')
            week_label = heading.strip()
        except Exception:
            pass

        # Get all text from the table (same as bookmarklet: table.innerText)
        try:
            table_text = table.inner_text()
        except Exception:
            continue

        counts = {}
        weighted_total = 0.0
        for vac_type in weights:
            # Match patterns like "3 Vac07" or "10 VAC02" (case insensitive)
            pattern = r'(\d+)\s+' + vac_type
            matches = re.findall(pattern, table_text, re.IGNORECASE)
            raw_count = sum(int(m) for m in matches)
            if raw_count > 0:
                counts[vac_type] = raw_count
                weighted_total += raw_count * weights[vac_type]

        weeks.append({
            "week": week_label,
            "total": weighted_total,
            "counts": counts,
        })
        print(f"[READ] {week_label}: {weighted_total} weighted VACs {counts}")

    # Navigate back
    if original_url and original_url != sales_url:
        print(f"[NAV] Returning to: {original_url}")
        page.goto(original_url, wait_until="domcontentloaded", timeout=20000)

    return weeks


def read_so_log(page: Page) -> list:
    """
    Read the Sales Order Log table on the SO page.
    DOM: <table> with <caption>Sales Order Log</caption>, rows in <tbody>.
    Returns list of dicts: [{"date": "...", "user": "...", "message": "..."}, ...]
    """
    entries = []
    try:
        log_table = page.locator('table:has(caption:text("Sales Order Log"))')
        if log_table.count() == 0:
            print("[READ] No Sales Order Log table found")
            return entries

        rows = log_table.locator('tbody tr').all()
        for row in rows:
            cells = row.locator('td').all()
            if len(cells) >= 3:
                entries.append({
                    "date": cells[0].inner_text().strip(),
                    "user": cells[1].inner_text().strip(),
                    "message": cells[2].inner_text().strip(),
                })
    except Exception as e:
        print(f"[READ] Error reading SO log: {e}")
    return entries


def read_sor_link(page: Page) -> str:
    """
    Find the linked SOR URL on the SO page.
    Returns the full href or empty string if none found.
    """
    try:
        link = page.locator('a[href*="/order-requests/"]').first
        if link.count() > 0:
            href = link.get_attribute("href")
            print(f"[READ] Found linked SOR: {href}")
            return href
    except Exception as e:
        print(f"[READ] Error finding SOR link: {e}")
    print("[READ] No linked SOR found")
    return ""


@timed
def read_sor_data(page: Page) -> dict:
    """
    Navigate to the linked SOR, read key fields, navigate back to SO.

    Returns dict with:
        processor_type: '' (Stripe default), '6' (Stripe explicit), '2' (Fortis)
        required_date: str like '2026-05-29' or '' if none
        required_date_raw: str like 'Specific date (May 29, 2026)' -- the raw text
        is_expedited: bool -- True if EXPEDITED flag present
        sor_url: str -- the SOR URL we visited
    """
    result = {
        "processor_type": "",
        "required_date": "",
        "required_date_raw": "",
        "is_expedited": False,
        "card_design_type": "",  # "New design", "Modify", "Reprint", or ""
        "contact_name": "",
        "contact_email": "",
        "contact_phone": "",
        "location_name": "",
        "location_address": "",
        "existing_end_customer": "",  # "Swift Wash (01435)" -- authoritative existing link
        "existing_end_customer_id": "",
        "access_sharing": "",          # "Yes"/"No" -- drives 01 (grouped) vs 02 (new group) loc series
        "card_correspondent": "",      # answer to "Who should we correspond with re: card design?"
        "submitted_by_name": "",       # dealer rep who submitted -- card-email recipient when "Me"
        "submitted_by_email": "",
        "shipping_method": "",
        "shipping_comments": "",
        "dealer": "",          # General Info "Dealer" -> SF Distributor lookup / SaaS handoff
        "total_kits": "",      # "Total # of kits needed" -> SF MOOPS Opp Number_of_Machines__c
        "sor_url": "",
    }

    def _one_line(value) -> str:
        return " ".join(str(value or "").split())

    so_url = page.url
    sor_href = read_sor_link(page)
    if not sor_href:
        print("[READ] No SOR link -- returning defaults")
        return result

    # Navigate to SOR
    sor_url = sor_href if sor_href.startswith("http") else f"{MOOPS_BASE}{sor_href}"
    result["sor_url"] = sor_url
    print(f"[NAV] Going to SOR: {sor_url}")
    page.goto(sor_url, wait_until="domcontentloaded", timeout=20000)
    try:
        page.wait_for_function(
            "() => !document.body.textContent.includes('{{')", timeout=8000)
    except Exception:
        page.wait_for_timeout(1500)  # fallback if Angular brace-clear times out

    # --- Read EBT / Processor field ---
    try:
        labels = page.locator('label, th, td, dt, strong').all()
        for label in labels:
            text = label.inner_text().strip()
            if "EBT" in text and "Processor" in text:
                parent = label.locator('..')
                select = parent.locator('select')
                if select.count() > 0:
                    result["processor_type"] = select.first.input_value()
                    print(f"[READ] Processor type (select): '{_one_line(result['processor_type'])}'")
                else:
                    sibling_text = parent.inner_text().replace(text, "").strip()
                    if sibling_text:
                        result["processor_type"] = sibling_text
                        print(f"[READ] Processor type (text): '{_one_line(result['processor_type'])}'")
                break

        if not result["processor_type"]:
            rows = page.locator('tr').all()
            for row in rows:
                row_text = row.inner_text()
                if "EBT" in row_text or "Processor" in row_text:
                    cells = row.locator('td, th').all()
                    if len(cells) >= 2:
                        result["processor_type"] = cells[-1].inner_text().strip()
                        print(f"[READ] Processor type (table row): '{_one_line(result['processor_type'])}'")
                    break
    except Exception as e:
        print(f"[READ] Error reading processor type: {e}")

    print(f"[READ] Final processor_type='{_one_line(result['processor_type'])}'")

    # --- Read Required Delivery Date ---
    # DOM structure (validated from screenshot):
    #   <label class="col-3 col-form-label py-0">Required Delivery Date</label>
    #   <span class="col-9 pb-0 font-weight-bold">
    #     <span>Specific date</span>
    #     <span>(May 29, 2026)</span>
    #     <span class="font-weight-bold bs-red">EXPEDITED</span>  <- optional
    #   </span>
    try:
        req_label = page.locator('label').filter(has_text="Required Delivery Date").first
        if req_label.count() > 0:
            parent = req_label.locator('..')
            value_span = parent.locator('span.col-9').first
            if value_span.count() > 0:
                raw_text = value_span.inner_text().strip()
                result["required_date_raw"] = raw_text
                print(f"[READ] Required Delivery Date raw: '{_one_line(raw_text)}'")

                # Check for EXPEDITED flag
                expedited = parent.locator('span.bs-red').first
                if expedited.count() > 0 and "EXPEDITED" in expedited.inner_text():
                    result["is_expedited"] = True
                    print("[READ] Order is EXPEDITED")

                # Parse the date from "(Jun 5, 2026)" or "(May 29, 2026)" format
                date_match = re.search(r'\((\w+ \d+,\s*\d{4})\)', raw_text)
                if date_match:
                    from datetime import datetime
                    date_text = date_match.group(1)
                    parsed = None
                    for fmt in ["%b %d, %Y", "%B %d, %Y"]:
                        try:
                            parsed = datetime.strptime(date_text, fmt)
                            break
                        except ValueError:
                            continue
                    if parsed:
                        result["required_date"] = parsed.strftime("%Y-%m-%d")
                        print(f"[READ] Required date parsed: {result['required_date']}")
                    else:
                        print(f"[READ] Could not parse date: {date_text}")
            else:
                print("[READ] No value span found for Required Delivery Date")
        else:
            print("[READ] No Required Delivery Date label found on SOR")
    except Exception as e:
        print(f"[READ] Error reading required date: {e}")

    # --- Read Card Design Type ---
    # DOM: label "Design type" -> sibling span.col-9.pb-0.font-weight-bold
    # Values: "New design", "Modify", "Reprint", or not present
    try:
        design_label = page.locator('label').filter(has_text="Design type").first
        if design_label.count() > 0:
            parent = design_label.locator('..')
            value_span = parent.locator('span.col-9').first
            if value_span.count() > 0:
                result["card_design_type"] = value_span.inner_text().strip()
                print(f"[READ] Card design type: '{_one_line(result['card_design_type'])}'")
            else:
                print("[READ] No value span for Design type")
        else:
            print("[READ] No Design type label found (no cards on SOR)")
    except Exception as e:
        print(f"[READ] Error reading card design type: {e}")

    # --- Read Contact Info + Location from SOR ---
    try:
        field_map = [
            ("New Contact Name", "contact_name"),
            ("New Contact Email", "contact_email"),
            ("New Contact Phone", "contact_phone"),
            ("Location Name", "location_name"),
            ("Location Address", "location_address"),
            ("Existing End Customer", "existing_end_customer"),
            ("Access Sharing", "access_sharing"),
            ("Dealer", "dealer"),
        ]
        for field, key in field_map:
            lbl = page.locator('label, th, td').filter(has_text=field).first
            if lbl.count() > 0:
                parent = lbl.locator('..')
                val = parent.inner_text().replace(field, "").strip()
                if val:
                    result[key] = val
                    if key == "existing_end_customer":
                        m = re.search(r'\((\d{3,})\)', val)
                        if m:
                            result["existing_end_customer_id"] = m.group(1)
                    print(f"[READ] {field}: '{_one_line(val)}'")
    except Exception as e:
        print(f"[READ] Error reading contact/location info: {e}")

    # --- Card correspondent: "Who should we correspond with regarding the card design?" ---
    # "Me" => the dealer rep who SUBMITTED the SOR is the card-design contact (not the store
    # operator/New Contact). Any other answer ("Location operator/store owner") keeps the operator.
    try:
        lbl = page.locator('label, th, td').filter(has_text="correspond with regarding").first
        if lbl.count() > 0:
            raw = lbl.locator('..').inner_text()
            ans = re.sub(r'(?is).*card design\?\s*', '', raw).strip()  # drop the question, keep answer
            result["card_correspondent"] = _one_line(ans)
            print(f"[READ] Card correspondent: '{result['card_correspondent']}'")
    except Exception as e:
        print(f"[READ] Error reading card correspondent: {e}")

    # --- Submitted By (dealer rep name + email) -- the recipient when correspondent == "Me" ---
    try:
        lbl = page.locator('label, th, td').filter(has_text="Submitted By").first
        if lbl.count() > 0:
            val = _one_line(lbl.locator('..').inner_text().replace("Submitted By", "")).strip()
            em = re.search(r'[\w.+-]+@[\w.-]+\.\w+', val)
            if em:
                result["submitted_by_email"] = em.group(0)
            result["submitted_by_name"] = re.sub(r'[\w.+-]+@[\w.-]+\.\w+', '', val).strip().strip(',').strip()
            print(f"[READ] Submitted By: '{result['submitted_by_name']}' / '{result['submitted_by_email']}'")
    except Exception as e:
        print(f"[READ] Error reading Submitted By: {e}")

    # --- Read Total # of kits needed (count only) -> SF MOOPS Opp Number_of_Machines__c ---
    # The label and the count render in separate cells, so match against the whole SOR text
    # rather than one element ([:\s]* spans the ':'/newline between the label and the number).
    try:
        body_txt = page.locator("body").inner_text(timeout=3000)
        m = re.search(r"Total # of kits needed[:\s]*([0-9]+)", body_txt)
        if m:
            result["total_kits"] = m.group(1)
            print(f"[READ] Total kits: {result['total_kits']}")
    except Exception as e:
        print(f"[READ] Error reading total kits: {e}")

    # --- Read Shipping Method + comments ---
    try:
        labels = page.locator('label, th, td, dt').all()
        for label in labels:
            text = label.inner_text().strip()
            text_l = text.lower()
            is_shipping_label = (
                ("ship" in text_l and "method" in text_l)
                or "shipping method" in text_l
                or "delivery method" in text_l
            )
            if is_shipping_label:
                parent = label.locator('..')
                sel = parent.locator('select')
                if sel.count() > 0:
                    result["shipping_method"] = sel.first.evaluate(
                        'el => el.options[el.selectedIndex]?.text || ""'
                    ).strip()
                else:
                    val = parent.inner_text().replace(text, "").strip()
                    if val:
                        result["shipping_method"] = val
                if result["shipping_method"]:
                    print(f"[READ] Shipping method: '{_one_line(result['shipping_method'])}'")
                    break

        comments_labels = page.locator('label, th').filter(has_text="Comments").all()
        for cl in comments_labels:
            parent = cl.locator('..')
            comment_text = parent.inner_text().replace("Comments", "").strip()
            if comment_text:
                result["shipping_comments"] = comment_text
                print(f"[READ] Shipping comments: '{_one_line(comment_text)}'")
                if not result["shipping_method"] and (
                    "NEXT DAY" in comment_text.upper() or "OVERNIGHT" in comment_text.upper()
                ):
                    result["shipping_method"] = "NEXT DAY"
                    print("[READ] Shipping urgency from comments: NEXT DAY")
                break
    except Exception as e:
        print(f"[READ] Error reading shipping method/comments: {e}")

    # Navigate back to SO
    print(f"[NAV] Returning to SO: {so_url}")
    page.goto(so_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector('tr[id^="existing_part_order_"]', timeout=15000)

    return result


def read_sor_contact(page: Page, sor_id) -> dict:
    """Navigate DIRECTLY to a raw SOR (order request) and read its contact + location
    fields -- treating the request like an order so it can be deduped before it's an
    SO. No SO round-trip. Returns {contact_name, contact_email, contact_phone,
    location_name, location_address, existing_end_customer, sor_url}.

    `existing_end_customer` is the raw SOR field ("Swift Wash (01435)") -- the AUTHORITATIVE
    human-asserted link to an existing customer. On existing-customer SORs the New Contact
    fields are usually blank, so this is the only reliable signal; callers should prefer it
    over name/contact matching.

    The SOR is Angular/client-rendered (lesson #18): wait for the template braces to
    resolve before scraping, not a fixed sleep.
    """
    out = {"contact_name": "", "contact_email": "", "contact_phone": "",
           "location_name": "", "location_address": "",
           "existing_end_customer": "", "sor_url": ""}
    url = f"{MOOPS_BASE}/order-requests/{str(sor_id).lstrip('#')}"
    out["sor_url"] = url
    print(f"[NAV] SOR (direct): {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    try:
        page.wait_for_function(
            "() => !document.body.textContent.includes('{{')", timeout=12000)
    except Exception:
        page.wait_for_timeout(2500)  # fallback if the brace check never clears

    field_map = [
        ("New Contact Name", "contact_name"),
        ("New Contact Email", "contact_email"),
        ("New Contact Phone", "contact_phone"),
        ("Location Name", "location_name"),
        ("Location Address", "location_address"),
        ("Existing End Customer", "existing_end_customer"),
    ]
    for field, key in field_map:
        try:
            lbl = page.locator('label, th, td').filter(has_text=field).first
            if lbl.count() > 0:
                parent = lbl.locator('..')
                val = parent.inner_text().replace(field, "").strip()
                if val:
                    out[key] = val
                    print(f"[READ] {field}: '{val}'")
        except Exception:
            pass
    return out


def read_processor_type(page: Page) -> str:
    """
    Convenience wrapper -- navigate to SOR, read processor type, navigate back.
    For backward compatibility. Use read_sor_data() to get all SOR fields at once.
    """
    data = read_sor_data(page)
    return data["processor_type"]


def read_wire_splicers_from_missing(page: Page) -> dict:
    """
    Read wire splicer (03-01-43) info from the missing parts section.
    Returns {"part_number": "03-01-43", "qty": N} or empty dict if not found.
    Wire splicers come from missing parts, not from rules.
    """
    missing = read_missing_parts(page)
    for m in missing:
        associated = m.get("associated_part", "").strip()
        if associated == "03-01-43":
            qty_str = m.get("qty", "0").strip()
            qty = int(qty_str) if qty_str.isdigit() else 0
            print(f"[READ] Wire splicers in missing parts: 03-01-43 qty={qty}")
            return {"part_number": "03-01-43", "qty": qty}
    print("[READ] No wire splicers in missing parts")
    return {}


# ---------------------------------------------------------------------------
# Write actions (modify the page)
# ---------------------------------------------------------------------------

def _clear_customer_id_if_blocking(page: Page) -> bool:
    """
    Check if Customer ID is populated but Location is empty (blocks save).
    If so, clear Customer ID. Returns True if it cleared something.

    MOOPS enforces: if Customer ID is set, Location is required.
    The field gets auto-populated from SOR for existing customers.
    We read the customer info from notes before save, so clearing here is safe.
    """
    try:
        # Use JS to find the Customer ID field by label text and check state
        result = page.evaluate("""() => {
            // Find all labels on the page
            const labels = document.querySelectorAll('label');
            let custField = null;
            let locField = null;

            for (const label of labels) {
                const text = label.textContent.trim().toLowerCase();
                // Customer ID / End Customer / Customer Account
                if (text.includes('customer') && (text.includes('id') || text.includes('account'))) {
                    // Look for associated input or select
                    const forId = label.getAttribute('for');
                    if (forId) {
                        custField = document.getElementById(forId);
                    }
                    if (!custField) {
                        // Try sibling/nearby input or select
                        const parent = label.closest('.form-group, .row, div');
                        if (parent) {
                            custField = parent.querySelector('select, input[type="text"]');
                        }
                    }
                }
                if (text.includes('location') && !text.includes('address') && !text.includes('inventory')) {
                    const forId = label.getAttribute('for');
                    if (forId) {
                        locField = document.getElementById(forId);
                    }
                    if (!locField) {
                        const parent = label.closest('.form-group, .row, div');
                        if (parent) {
                            locField = parent.querySelector('select, input[type="text"]');
                        }
                    }
                }
            }

            // Also try common MOOPS name patterns
            if (!custField) {
                custField = document.querySelector('select[name*="customer"], input[name*="customer_account"]');
            }
            if (!locField) {
                locField = document.querySelector('select[name*="location_id"], select[name*="location"]');
            }

            if (!custField) return {found: false};

            const custVal = custField.value || '';
            const locVal = locField ? (locField.value || '') : '';
            const custName = custField.name || custField.id || '';
            const locName = locField ? (locField.name || locField.id || '') : '';

            return {
                found: true,
                cust_val: custVal,
                loc_val: locVal,
                cust_name: custName,
                loc_name: locName,
                cust_tag: custField.tagName,
            };
        }""")

        if not result.get("found"):
            return False

        cust_val = result.get("cust_val", "")
        loc_val = result.get("loc_val", "")

        if cust_val and not loc_val:
            cust_name = result.get("cust_name", "")
            print(f"[WARNING] Customer ID populated ('{cust_val}') but Location empty — clearing to unblock save")
            # Clear the Customer ID field
            if result.get("cust_tag") == "SELECT":
                page.evaluate(f"""() => {{
                    const el = document.querySelector('[name="{cust_name}"]') || document.getElementById('{cust_name}');
                    if (el) {{ el.value = ''; el.dispatchEvent(new Event('change', {{bubbles: true}})); }}
                }}""")
            else:
                page.evaluate(f"""() => {{
                    const el = document.querySelector('[name="{cust_name}"]') || document.getElementById('{cust_name}');
                    if (el) {{ el.value = ''; el.dispatchEvent(new Event('input', {{bubbles: true}})); }}
                }}""")
            page.wait_for_timeout(500)
            return True

        return False
    except Exception as e:
        print(f"[WARNING] Customer ID check failed: {e}")
        return False


@timed
def save_so(page: Page, accept_sor: bool = False, clear_customer_location_blocker: bool = True) -> None:
    """
    Click Save. Auto-dismiss the 'Transition SOR?' popup if it appears.
    Pre-checks Customer ID / Location blocker before saving.
    MOOPS save is AJAX — no page navigation occurs.
    """
    # Pre-save check: clear Customer ID if Location is empty (blocks save).
    # Cards-only orders can legitimately carry a customer id without a location.
    if clear_customer_location_blocker:
        _clear_customer_id_if_blocking(page)

    print("[ACTION] Saving SO...")
    # MOOPS save triggers a full page reload (gears → reload → "successfully saved").
    # Playwright's click() blocks ~30s waiting for post-navigation load event.
    # Use JS click to bypass all Playwright actionability/navigation waits.
    clicked = page.evaluate("""() => {
        const els = document.querySelectorAll('button, a, input[type="submit"]');
        for (const el of els) {
            const txt = el.textContent.trim();
            if (txt === 'Save' || txt.startsWith('Save')) {
                el.click();
                return true;
            }
        }
        // Fallback: try input[value="Save"]
        const inp = document.querySelector('input[value="Save"]');
        if (inp) { inp.click(); return true; }
        return false;
    }""")

    if not clicked:
        # Last resort: use Playwright click (will be slow but at least works)
        print("[WARNING] JS click didn't find Save — falling back to Playwright click")
        page.locator('button, a').filter(has_text="Save").first.click()

    # Wait for the save + reload to complete.
    # Use time.sleep — page.wait_for_timeout hangs when page context is
    # destroyed during a full page reload.
    time.sleep(3)

    # Dismiss SOR transition popup if visible (no wait — just check once)
    try:
        popup = page.locator('text=Would you also like to transition')
        if popup.is_visible():
            if accept_sor:
                page.locator('button').filter(has_text="Yes").first.click()
                print("[ACTION] Accepted linked SOR")
            else:
                page.locator('button').filter(has_text="No").first.click()
                print("[ACTION] Declined SOR transition")
            time.sleep(1)
    except Exception:
        pass  # No popup — normal

    print("[ACTION] SO saved")


def set_work_state(page: Page, state: str) -> None:
    """Set the Work State dropdown (e.g., 'Placed', 'Draft')."""
    print(f"[ACTION] Setting work state to: {state}")
    try:
        selects = page.locator('select').all()
        for sel in selects:
            options = sel.locator('option').all_inner_texts()
            if 'Placed' in options and 'Draft' in options:
                sel.select_option(label=state)
                print(f"[ACTION] Work state set to: {state}")
                return
    except Exception as e:
        print(f"[ACTION] Could not set work state: {e}")


def set_shipment(page: Page, method: str, shipped_by: str) -> None:
    """Set Shipment Method and Shipment By dropdowns."""
    print(f"[ACTION] Setting shipment: method={method}, by={shipped_by}")
    selects = page.locator('select').all()
    for sel in selects:
        try:
            options = sel.locator('option').all_inner_texts()
            if method in options:
                sel.select_option(label=method)
                print(f"[ACTION] Shipment method set to: {method}")
            elif shipped_by in options:
                sel.select_option(label=shipped_by)
                print(f"[ACTION] Shipment by set to: {shipped_by}")
        except Exception:
            continue


def update_existing_part_qty(page: Page, part_number: str, new_qty: int) -> bool:
    """
    Update quantity on an EXISTING row in the product table.
    Used for wire splicers -- don't add new line, update what's already there.
    Returns True if found and updated, False if part not on order.
    """
    print(f"[ACTION] Updating existing part qty: {part_number} -> {new_qty}")
    rows = page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all()
    for row in rows:
        try:
            pn_loc = row.locator('th[scope="row"] a')
            if pn_loc.count() > 0 and pn_loc.first.inner_text().strip().upper() == part_number.upper():
                qty_input = row.locator('input[type="number"]').first
                old_val = qty_input.input_value()
                qty_input.click()
                qty_input.fill(str(new_qty))
                print(f"[ACTION] Updated {part_number} qty: {old_val} -> {new_qty}")
                return True
        except Exception:
            continue
    print(f"[WARNING] Part {part_number} not found on order -- cannot update qty")
    return False


def set_task_checklist(page: Page, statuses: dict) -> None:
    """
    Set task checklist statuses on the SO page.
    statuses = {1: "Completed", 2: "Completed", 3: "Completed", ...}
    Task indices are 1-10. Values: "Completed", "To Do", "N/A"

    The 10 selects all have name="task_state" -- they appear in DOM order matching task 1-10.
    """
    print(f"[ACTION] Setting task checklist: {statuses}")

    task_selects = page.locator('select[name="task_state"]').all()
    if len(task_selects) < 10:
        print(f"[WARNING] Found {len(task_selects)} task selects (expected 10)")

    for task_num, desired_status in statuses.items():
        idx = task_num - 1  # 0-based index
        if idx < 0 or idx >= len(task_selects):
            print(f"[WARNING] Task {task_num} out of range")
            continue

        sel = task_selects[idx]
        try:
            sel.select_option(label=desired_status)
            print(f"[ACTION] Task {task_num}: set to {desired_status}")
        except Exception as e:
            print(f"[WARNING] Could not set task {task_num}: {e}")


TASK_LABELS = {
    1: "Hardware verified",
    2: "End-customer info obtained",
    3: "Connected with end-customer/dealer",
    4: "Card approval received",
    5: "Card proofs, PO sent",
    6: "Sent SaaS contract",
    7: "Sent Payment processing contract",
    8: "End-customer and location added to Portal",
    9: "VAC Config files attached to order",
    10: "Created Admin Portal user and emailed Intro email",
}


def read_task_states(page: Page) -> dict:
    """
    Read current task checklist states from the SO page.
    Returns dict: {1: {"label": "...", "status": "Completed"}, 2: {...}, ...}
    """
    task_selects = page.locator('select[name="task_state"]').all()
    if len(task_selects) < 10:
        print(f"[WARNING] Found {len(task_selects)} task selects (expected 10)")

    results = {}
    for i, sel in enumerate(task_selects):
        task_num = i + 1
        try:
            selected = sel.locator('option:checked')
            status = selected.first.inner_text().strip() if selected.count() > 0 else "Unknown"
        except Exception:
            status = "Unknown"
        results[task_num] = {
            "label": TASK_LABELS.get(task_num, f"Task {task_num}"),
            "status": status,
        }

    return results


def set_order_type(page: Page, order_type: str) -> None:
    """Set the Order Type dropdown (e.g., 'System - Laundromat', 'Parts', 'Cards Only')."""
    print(f"[ACTION] Setting order type to: {order_type}")
    try:
        sel = page.locator('select[name="sales_type_id"]')
        sel.select_option(label=order_type)
        print(f"[ACTION] Order type set to: {order_type}")
    except Exception as e:
        print(f"[ACTION] Could not set order type: {e}")


def generate_card_shortname(customer_name: str) -> str:
    """
    Generate a card shortname from customer name.
    Rules: remove vowels (except leading), uppercase, target 6 chars.

    Examples:
      "The Laundry" -> "THLND"
      "Rainbow Brite Laundry" -> "RNBRL"
      "Gold Coin Laundry Equipment" -> "GLCNLN"
      "KMRB Investments" -> "KMRBIN"
    """

    name = customer_name.upper().strip()
    # Remove common suffixes that don't help with uniqueness
    for suffix in ["LLC", "INC", "INC.", "CORP", "EQUIPMENT", "CO", "CO."]:
        name = re.sub(r'\b' + suffix + r'\b', '', name).strip()
    # Remove non-alpha
    name = re.sub(r'[^A-Z ]', '', name)
    words = name.split()
    if not words:
        return "CARD"

    # For each word: keep first letter, remove vowels from the rest
    vowels = set("AEIOU")
    parts = []
    for word in words:
        if not word:
            continue
        # Keep first char, strip vowels from remainder
        cleaned = word[0] + ''.join(c for c in word[1:] if c not in vowels)
        parts.append(cleaned)

    result = ''.join(parts)

    # Target 6 chars. If still too long, take first 2 chars of each word,
    # then first char of each, then truncate as last resort.
    if len(result) > 6:
        # Try first 2 consonant-stripped chars per word
        short = ''.join(p[:2] for p in parts)
        if len(short) <= 6:
            result = short
        else:
            # Try first char of each word (acronym)
            acronym = ''.join(p[0] for p in parts)
            if len(acronym) <= 6:
                result = acronym
            else:
                result = acronym[:6]

    return result


@timed
def clone_temp_card(page: Page, shortname: str, end_customer_id: str = "",
                    location_id: str = "") -> str:
    """
    Clone A-TEMP-CARD-MD to create a new card part CARD-MD-{SHORTNAME}.
    Does NOT save -- human reviews first.

    Args:
      shortname: Card shortname (e.g. "THELNDRY") — printed on back of card.
      end_customer_id: Optional customer ID (e.g. "01707") for existing customers.
          If provided, searches for that customer. If empty, defaults to Mitech.
      location_id: Optional location ID (e.g. "0100001") to set in the card's Card
          Ownership block, below End-Customer. Only passed when we already have it
          (the system chain after the location is created); skipped when unknown.

    Steps:
      1. Navigate to A-TEMP-CARD-MD (part_id=3064)
      2. Click Clone
      3. Fill Part Number = CARD-MD-{SHORTNAME}
      4. Set Description = PLACEHOLDER
      5. Upload placeholder image from assets/placeholder_card.png
      6. Click Use pricing group radio (already set to USER-CARDS)
      7. Set End-Customer (real customer ID if provided, else Mitech)

    Returns the new part number string.
    """
    import os

    new_part_number = f"CARD-MD-{shortname.upper()}"
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    placeholder_path = os.path.join(project_dir, "assets", "Placeholder.png")
    print(f"[ACTION] Cloning A-TEMP-CARD-MD -> {new_part_number}")

    if not os.path.exists(placeholder_path):
        print(f"[WARNING] Placeholder image not found: {placeholder_path}")
        print("[WARNING] Save placeholder_card.png to assets/ folder first")

    # Step 1: Navigate to temp card
    url = f"{MOOPS_BASE}/part?part_id=3064"
    print(f"[NAV] Going to A-TEMP-CARD-MD: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2000)

    # Step 2: Click Clone
    print("[ACTION] Clicking Clone...")
    page.locator('text=Clone').first.click()
    page.wait_for_timeout(3000)

    # Step 3: Upload placeholder image FIRST (avoids form reset on error)
    if os.path.exists(placeholder_path):
        print("[ACTION] Uploading placeholder image...")
        try:
            file_input = page.locator('input[type="file"]#uploadFile')
            if file_input.count() == 0:
                file_input = page.locator('input[type="file"]').first
            file_input.set_input_files(placeholder_path)
            page.wait_for_timeout(2000)
            print("[ACTION] Image uploaded")
        except Exception as e:
            print(f"[WARNING] Image upload failed: {e}")
    else:
        print(f"[WARNING] Image not found: {placeholder_path}")

    # Step 4: Set Part Number
    print(f"[ACTION] Setting part number to: {new_part_number}")
    pn_input = page.locator('input[name="part_number"]')
    if pn_input.count() > 0:
        pn_input.first.click()
        pn_input.first.fill(new_part_number)
    else:
        labels = page.locator('label').filter(has_text="Part Number")
        if labels.count() > 0:
            parent = labels.first.locator('..')
            inp = parent.locator('input').first
            inp.click()
            inp.fill(new_part_number)
        else:
            print("[WARNING] Could not find Part Number input")

    # Step 5: Set Description = PLACEHOLDER
    print("[ACTION] Setting description to: PLACEHOLDER")
    desc_input = page.locator('input[name="description"], textarea[name="description"]')
    if desc_input.count() > 0:
        desc_input.first.click()
        desc_input.first.fill("PLACEHOLDER")
    else:
        print("[WARNING] Could not find Description input")

    # Step 7: Click Use pricing group radio
    print("[ACTION] Clicking Use pricing group radio...")
    try:
        radio_label = page.locator('label').filter(has_text="Use pricing group").first
        radio_label.click()
        print("[ACTION] Pricing group radio clicked")
    except Exception as e:
        print(f"[WARNING] Could not click pricing group radio: {e}")

    # Step 8: Set End-Customer
    # For existing customers (end_customer_id provided), search by ID.
    # For new customers, default to Mitech.
    search_term = end_customer_id if end_customer_id else "Mitech"
    print(f"[ACTION] Setting End-Customer (search: '{search_term}')...")
    try:
        cust_input = page.locator('#validity_portal_customer_search')
        if cust_input.count() == 0:
            cust_input = page.locator('label').filter(has_text="End-Customer").locator('..').locator('input').first
        cust_input.click()
        cust_input.fill("")
        cust_input.type(search_term, delay=50)
        page.wait_for_timeout(1500)
        # Click the first dropdown result
        # For customer ID searches, the dropdown shows "Name (ID)"
        dropdown_items = page.locator('.dropdown-item, [role="option"], li').filter(has_text=search_term)
        if dropdown_items.count() > 0:
            dropdown_items.first.click()
        else:
            # Fallback: click first text match
            page.locator(f'text={search_term}').first.click()
        page.wait_for_timeout(500)
        print(f"[ACTION] End-Customer set (searched: '{search_term}')")
    except Exception as e:
        print(f"[WARNING] Could not set End-Customer: {e}")

    # Location (Card Ownership) on the card part -- only when handed a real customer id AND a
    # location (the card-part location search is enabled once an End-Customer is chosen). Same
    # pick -> Add Location -> verify path as the SO; the card-part field has its own selector.
    if end_customer_id and location_id:
        page.wait_for_timeout(500)
        # Pick the location + click "Add Location" to attach it to the ownership table. That button
        # is type=button (verified), so it adds the row WITHOUT saving the part -- the human's
        # review-Save persists it, alongside the End-Customer id.
        _commit_ownership_location(page, location_id, '#validity_portal_location_search')

    print(f"\n[ACTION] Card clone form filled: {new_part_number}")
    print("[PAUSE] Review and save the card. Press Enter when done.")
    try:
        input()
    except KeyboardInterrupt:
        print("\n[ABORT] Card not saved.")
        return new_part_number

    # Re-read actual part number from the page after save
    import re as _re
    actual_pn = ""
    # Try 1: input field (still in edit mode)
    try:
        actual_pn = page.locator('input[name="part_number"]').first.input_value(timeout=2000).strip()
    except Exception:
        pass
    # Try 2: heading or bold text with CARD-MD-
    if not actual_pn:
        try:
            page_text = page.locator('h1, h2, h3, .page-title, [class*="title"]').first.inner_text(timeout=2000)
            match = _re.search(r'(CARD-MD-[A-Z0-9-]+)', page_text.upper())
            if match:
                actual_pn = match.group(1)
        except Exception:
            pass
    # Try 3: scan full page text for CARD-MD-*
    if not actual_pn:
        try:
            body = page.locator('body').first.inner_text(timeout=3000)
            match = _re.search(r'(CARD-MD-[A-Z0-9-]+)', body.upper())
            if match:
                actual_pn = match.group(1)
        except Exception:
            pass

    if actual_pn and new_part_number.startswith(actual_pn) and actual_pn != new_part_number:
        print(f"[WARNING] Ignoring partial card number detection: {actual_pn}; using {new_part_number}")
    elif actual_pn and actual_pn != new_part_number:
        print(f"[INFO] Card renamed: {new_part_number} -> {actual_pn}")
        new_part_number = actual_pn
    elif not actual_pn:
        print(f"[WARNING] Could not detect part number, using: {new_part_number}")

    return new_part_number


@timed
def open_card_design_email(page: Page, card_part_number: str,
                           contact_name: str = "", contact_email: str = "",
                           source_card: str = "") -> None:
    """
    Open the Card Design Email modal on the SO page, fill it out, but do NOT send.
    Human reviews and clicks Send.

    Steps:
      1. Click "Card Design Email" button
      2. Clear CC field
      3. Update Card Part Number in message body; on a modify, reference the original card
         (source_card) so graphics can pull the existing artwork; fill the template's blank
         Contact Name/Email lines (not appended at the bottom)
      4. Select design files (skip anything with "PO" in filename)
      5. Stop -- human reviews and sends

    `source_card` (modify only): the CARD-MD-* already on the SO that's being modified. The
    template ships Contact Name/Email labels blank; on a modify the contact comes from an Admin
    lookup (the SOR has no contact), so we fill those existing lines instead of tacking a second
    contact block onto the end.
    """
    print("[ACTION] Opening Card Design Email...")

    # Step 1: Click the email button
    try:
        email_btn = page.locator('button').filter(has_text="Card Design Email").first
        email_btn.click()
        page.wait_for_timeout(2000)
        print("[ACTION] Email modal opened")
    except Exception as e:
        print(f"[ERROR] Could not open Card Design Email: {e}")
        return

    # Step 2: Clear ONLY the header CC field -- email_carbon_copy, the CC that sits between
    # To and BCC (confirmed from the modal DOM: 0=To, 1=CC, 2=BCC, 4=email_body). Clear it by
    # exact name only -- NO nth()/label guessing, so the message body (email_body) and the CC
    # text inside it are never touched.
    modal = page.locator('#design-email-modal')
    try:
        cc = modal.locator('input[name="email_carbon_copy"]')
        if cc.count() == 0:
            cc = page.locator('input[name="email_carbon_copy"]')
        if cc.count() > 0:
            cc.first.fill("")
            print("[ACTION] Header CC (email_carbon_copy) cleared")
        else:
            print("[WARNING] email_carbon_copy field not found -- CC left as-is")
    except Exception as e:
        print(f"[WARNING] Could not clear header CC: {e}")

    # Step 3: Update message body with correct card part number and contact info
    try:
        msg_textarea = modal.locator('textarea').first
        if msg_textarea.count() > 0:
            current_msg = msg_textarea.input_value()

            # Fix incomplete card part number (template has bare "CARD-MD-")
            # Use regex to replace "CARD-MD-" only when NOT already followed by
            # the shortname. This avoids doubling like CARD-MD-THELNDRYCARD-MD-THELNDRY.
        
            shortname_part = card_part_number.replace("CARD-MD-", "")
            if shortname_part and "CARD-MD-" in current_msg and card_part_number not in current_msg:
                # Replace bare "CARD-MD-" (not followed by alphanumeric = incomplete)
                current_msg = re.sub(r'CARD-MD-(?![A-Z0-9])', card_part_number, current_msg)

            # On a modify, point graphics at the original card already on the SO so they can pull
            # the existing artwork as the base. Insert right after the "Card Part Number:" line
            # (fall back to prepending). Guard on the "Original Card" label, NOT on source_card in
            # the text -- the bumped name (OLYMPIA2) contains the original (OLYMPIA) as a substring.
            if source_card and "Original Card" not in current_msg:
                orig_line = f"Original Card (modify - use existing artwork): {source_card}"
                m = re.search(r'(?im)^(Card Part Number:.*)$', current_msg)
                if m:
                    current_msg = current_msg[:m.end()] + "\n" + orig_line + current_msg[m.end():]
                else:
                    current_msg = orig_line + "\n" + current_msg
                print(f"[ACTION] Referenced original card for modify: {source_card}")

            # Fill the template's existing "Contact Name:" / "Contact Email:" lines (they ship
            # blank) instead of appending at the bottom. The old append checked for the value and,
            # not finding it, tacked a detached contact block onto the end -- which is what showed
            # up on modify orders, where the contact comes from an Admin lookup. Fall back to
            # appending only if the label isn't present in the template at all.
            def _fill_label(msg, label, value):
                if not value:
                    return msg
                pat = re.compile(r'(?im)^(' + re.escape(label) + r'):[ \t]*$')
                if pat.search(msg):
                    return pat.sub(lambda mm: f"{mm.group(1)}: {value}", msg, count=1)
                if value not in msg:
                    return msg + f"\n{label}: {value}"
                return msg
            current_msg = _fill_label(current_msg, "Contact Name", contact_name)
            current_msg = _fill_label(current_msg, "Contact Email", contact_email)

            msg_textarea.fill(current_msg)
            print(f"[ACTION] Message updated with {card_part_number}")
            if contact_name:
                print(f"[ACTION] Contact: {contact_name} / {contact_email}")
    except Exception as e:
        print(f"[WARNING] Could not update message: {e}")

    # Step 4: Select design files. Each attached file is a `div.file-item` (filename in
    # `.file-info strong`, checkbox in `.file-actions input[name="file_resources"]`). MOOPS checks
    # them ALL by default, but the card design email should carry ONLY design artwork -- so UNCHECK
    # the PO and the .cfg config files (not design proofs). The old code scanned every div with a
    # checkbox and matched the modal's own To:/CC:/Subject: chrome as "files" (Matt: SO-20106 logged
    # 6 bogus skips); iterate the real .file-item rows instead.
    try:
        items = modal.locator('div.file-item')
        n = items.count()
        selected = skipped = 0
        for i in range(n):
            item = items.nth(i)
            try:
                strong = item.locator('.file-info strong').first
                name = (strong.inner_text() if strong.count() else item.inner_text() or "").strip()
            except Exception:
                name = ""
            up = name.upper()
            is_design = not (re.search(r'\bPURCHASE\b', up) or re.search(r'\bPO\b', up)
                             or up.endswith('.CFG'))
            box = item.locator('input[name="file_resources"]').first
            if box.count() == 0:
                continue
            try:
                if is_design:
                    if not box.is_checked():
                        box.check()
                    selected += 1
                    print(f"[ACTION] Design file attached: {name[:60]}")
                else:
                    if box.is_checked():
                        box.uncheck()
                    skipped += 1
                    print(f"[INFO] Unchecked (not design — PO/config): {name[:60]}")
            except Exception as e:
                print(f"[INFO] Could not toggle file '{name[:40]}' ({e})")
        if n == 0:
            print("[INFO] No attached files in the modal to choose from.")
        print(f"[ACTION] Files: {selected} design selected, {skipped} unchecked (PO/config)")
    except Exception as e:
        print(f"[WARNING] Could not select files: {e}")

    print("\n[ACTION] Card Design Email ready for review")
    print("[ACTION] NOT SENT -- review and click Send manually")


# ---------------------------------------------------------------------------
# IT Provisioning Form (Jira Service Desk)
# ---------------------------------------------------------------------------

ITF_URL = "https://cents.atlassian.net/servicedesk/customer/portal/1/group/3/create/171"


@timed
def open_itf_form(page: Page, so_id: int, notes_data: dict, vac_count: int,
                  existing_customer: dict = None,
                  card_design_type: str = "", processor_type: str = "") -> None:
    """
    Open the IT provisioning form (Jira Service Desk) in a new tab
    and fill it with data parsed from Internal Mitech Notes on the SO page.

    Does NOT submit — human reviews and clicks submit.

    Args:
      notes_data: from read_internal_notes() — contact, address, etc.
      vac_count: number of VACs on the order
      existing_customer: from read_existing_customer_id() — {"name": ..., "id": ...}
          If present, sets Existing=Yes + Customer ID. If empty/None, Existing=No.

    Fields filled:
      - Existing (Yes/No) — based on whether existing customer ID found in notes
      - Customer ID + Location (if existing)
      - Admin Portal Name (location name)
      - Contact Name, Email, Phone (from notes, may be empty for existing customers)
      - Address (location address)
      - SO URL
      - Number of VAC licences (actual count from SO)
      - Laundry Operation Type (Laundromat)
    """
    print(f"\n[ACTION] Opening IT Provisioning Form (ITF)...")

    is_existing = bool(existing_customer and existing_customer.get("id"))

    context = page.context
    itf_page = context.new_page()

    print(f"[NAV] Going to ITF form: {ITF_URL}")
    itf_page.goto(ITF_URL, wait_until="domcontentloaded", timeout=30000)
    itf_page.wait_for_timeout(3000)

    so_url = f"{MOOPS_BASE}/order?order_id={so_id}"

    # --- Existing customer dropdown ---
    try:
        existing_dropdown = itf_page.get_by_label("Existing")
        existing_dropdown.click()
        itf_page.wait_for_timeout(500)
        if is_existing:
            itf_page.get_by_role("option", name="Yes", exact=True).click()
            print(f"[ACTION] Existing: Yes (customer {existing_customer['id']})")
        else:
            itf_page.get_by_role("option", name="No", exact=True).click()
            print("[ACTION] Existing: No (new customer)")
        itf_page.wait_for_timeout(1000)  # Wait for conditional fields to appear
    except Exception as e:
        print(f"[WARNING] Could not set Existing field: {e}")

    # --- If existing: fill Customer ID and Location ---
    if is_existing:
        cust_id = existing_customer.get("id", "")
        cust_name = existing_customer.get("name", "")
        try:
            itf_page.get_by_label("Customer ID").fill(cust_id)
            print(f"[ACTION] Customer ID: {cust_id}")
        except Exception as e:
            print(f"[WARNING] Could not fill Customer ID: {e}")
        try:
            # Location field — fill with location name
            loc_name = notes_data.get("location_name", cust_name)
            itf_page.get_by_label("Location").fill(loc_name)
            print(f"[ACTION] Location: {loc_name}")
        except Exception as e:
            print(f"[WARNING] Could not fill Location: {e}")

    # --- Fill contact/address fields from Internal Mitech Notes ---
    # Title-case names (SOR data often comes in ALL CAPS)
    portal_name = notes_data.get("location_name", "").strip().title()
    contact_name_val = notes_data.get("contact_name", "").strip().title()
    fields = [
        ("Admin Portal Name", portal_name),
        ("Contact Name", contact_name_val),
        ("Contact Email", notes_data.get("contact_email", "").strip().lower()),
        ("Contact Number", notes_data.get("contact_phone", "")),
        ("Address", notes_data.get("location_address", "")),
    ]

    for label_text, value in fields:
        if not value:
            if not is_existing:
                print(f"[WARNING] No value for {label_text} — skipping")
            continue
        try:
            itf_page.get_by_label(label_text).fill(value)
            print(f"[ACTION] {label_text}: {value}")
        except Exception as e:
            print(f"[WARNING] Could not fill {label_text}: {e}")

    # SO URL
    try:
        itf_page.get_by_label("SO url").fill(so_url)
        print(f"[ACTION] SO URL: {so_url}")
    except Exception:
        try:
            itf_page.get_by_label("SO URL").fill(so_url)
            print(f"[ACTION] SO URL: {so_url}")
        except Exception as e:
            print(f"[WARNING] Could not fill SO URL: {e}")

    # Number of VAC licences
    vac_str = str(vac_count)
    try:
        itf_page.get_by_label("Number of VAC licences").fill(vac_str)
        print(f"[ACTION] VAC licences: {vac_str}")
    except Exception:
        try:
            itf_page.get_by_label("Number of VAC licenses").fill(vac_str)
            print(f"[ACTION] VAC licenses: {vac_str}")
        except Exception as e:
            print(f"[WARNING] Could not fill VAC licences: {e}")

    # Laundry Operation Type dropdown
    try:
        itf_page.get_by_label("Laundry Operation Type").select_option(label="Laundromat")
        print("[ACTION] Laundry Operation Type: Laundromat")
    except Exception:
        try:
            lot = itf_page.get_by_label("Laundry Operation Type")
            lot.click()
            itf_page.get_by_role("option", name="Laundromat", exact=True).click()
            print("[ACTION] Laundry Operation Type: Laundromat")
        except Exception as e:
            print(f"[WARNING] Could not set Laundry Operation Type: {e}")

    # --- Radio button helper ---
    # Jira service desk radio groups: legend/label text + Yes/No radio inputs.
    # Use JS for reliability — Playwright label matching breaks on Jira's dynamic DOM.
    def set_radio(label_text: str, value: str):
        """Click a Yes/No radio button by finding the group header text via JS."""
        result = itf_page.evaluate(f"""() => {{
            // Find all elements containing the label text
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_ELEMENT);
            let header = null;
            while (walker.nextNode()) {{
                const el = walker.currentNode;
                const txt = el.textContent || '';
                // Match element whose own text (not children) contains the label
                const ownText = Array.from(el.childNodes)
                    .filter(n => n.nodeType === 3)
                    .map(n => n.textContent.trim()).join(' ');
                if (ownText.includes('{label_text}')) {{
                    header = el;
                    break;
                }}
            }}
            if (!header) {{
                // Fallback: any element containing the text
                const all = [...document.querySelectorAll('legend, label, span, div, h3, h4')];
                header = all.find(el => el.textContent.includes('{label_text}')
                    && el.textContent.length < 80);
            }}
            if (!header) return 'no_header';

            // Walk up to find the container with radio inputs
            let container = header;
            for (let i = 0; i < 8; i++) {{
                container = container.parentElement;
                if (!container) break;
                if (container.querySelector('input[type="radio"]')) break;
            }}
            if (!container || !container.querySelector('input[type="radio"]')) return 'no_radios';

            // Find the radio input next to a label with the target value
            const radios = container.querySelectorAll('input[type="radio"]');
            for (const radio of radios) {{
                const lbl = radio.closest('label') ||
                    container.querySelector('label[for="' + radio.id + '"]');
                if (lbl && lbl.textContent.trim() === '{value}') {{
                    radio.click();
                    return 'clicked';
                }}
                // Also check sibling text
                const next = radio.nextSibling;
                if (next && next.textContent && next.textContent.trim() === '{value}') {{
                    radio.click();
                    return 'clicked';
                }}
            }}
            return 'no_match';
        }}""")
        return result == 'clicked'

    # --- Payment Email (IT) — Yes for system orders ---
    if set_radio("Payment Email (IT)", "Yes"):
        print("[ACTION] Payment Email (IT): Yes")
    else:
        print("[WARNING] Could not set Payment Email — set manually")

    # --- Custom Cards (Mark) — Yes if new card design ---
    from run import _card_type as _cdt
    has_custom_cards = _cdt(card_design_type) in ("new", "modify")
    cards_value = "Yes" if has_custom_cards else "No"
    if set_radio("Custom Cards (Mark)", cards_value):
        print(f"[ACTION] Custom Cards (Mark): {cards_value}")
    else:
        print(f"[WARNING] Could not set Custom Cards — set manually")

    # --- Stripe Processing (IT) — Yes if Stripe, No if Fortis ---
    is_stripe = "FORTIS" not in processor_type.upper() and "EBT" not in processor_type.upper()
    stripe_value = "Yes" if is_stripe else "No"
    if set_radio("Stripe Processing (IT)", stripe_value):
        print(f"[ACTION] Stripe Processing (IT): {stripe_value}")
    else:
        print(f"[WARNING] Could not set Stripe Processing — set manually")

    print(f"\n[ACTION] ITF form filled — review and submit in browser tab")
    print("[ACTION] NOT SUBMITTED -- human reviews and clicks submit")


# ---------------------------------------------------------------------------
# Higher-level action composites (moved from run.py)
# ---------------------------------------------------------------------------

def clean_name(name: str) -> str:
    """Convert ALL CAPS or messy names to title case."""
    if not name:
        return ""
    if name == name.upper():
        return name.title()
    return name


# Kit families that count as "readers" in the tag, besides CR-* reader parts: POS, MDB vending,
# door access, vending. These live in the SO's "Other Parts" and were previously left out of the
# "N Readers" / "N Reader Kits" tag count (Matt: 35 readers + 1 KIT-VENDRITE should tag as 36).
READER_KIT_PREFIXES = ("KIT-POS", "KIT-VENDRITE", "KIT-MDBVENDING", "KIT-VENDING", "KIT-DOORACCESS")


def build_tag(products: list, customer_name: str,
              dealer_name: str = "", is_route: bool = False) -> str:
    """
    Build the tag string from products on the SO.
    System: "2 VAC07, 1 VAC02, 4 Readers (Customer Name)"
    Route:  "1 VAC02, 19 Readers (Dealer - Location)"  -- name AND address
    The "N Readers" count includes CR-* readers PLUS reader-equivalent kits (POS, MDB vending,
    door access, vending) from Other Parts -- see READER_KIT_PREFIXES.
    """
    vac_counts = {}  # dict preserves insertion order since Python 3.7
    reader_count = 0
    for p in products:
        pn = (p.get("part_number") or p.get("part", "")).upper()
        qty_raw = p.get("qty", "0")
        qty = int(qty_raw) if str(qty_raw).isdigit() else 0
        if pn.startswith("VAC"):
            prefix = pn.split("-")[0]  # VAC07, VAC08, etc.
            vac_counts[prefix] = vac_counts.get(prefix, 0) + qty
        elif pn.startswith("CR-") or pn.startswith(READER_KIT_PREFIXES):
            # CR-* readers PLUS reader-equivalent kits (POS / MDB vending / door access / vending)
            reader_count += qty

    parts = []
    for prefix in vac_counts:
        parts.append(f"{vac_counts[prefix]} {prefix}")

    tag = ", ".join(parts)
    if reader_count > 0:
        tag += f", {reader_count} Readers"

    loc = clean_name(customer_name)
    if is_route:
        dealer = clean_name(dealer_name)
        if dealer and loc:
            tag += f" ({dealer} - {loc})"
        elif dealer or loc:
            tag += f" ({dealer or loc})"
    elif loc:
        tag += f" ({loc})"
    return tag


@timed
def action_add_part(page: Page, part_number: str, qty: int) -> None:
    """Add a part via the Product search box and set quantity."""
    print(f"\n[ACTION] Adding {part_number} qty={qty}")

    search_input = page.locator('#validity_product-search')
    search_input.click()
    search_input.fill(part_number)

    # Click Add To Order immediately — exact part number, no need for dropdown
    page.locator('text=Add To Order').first.click()

    # Wait for the part row to appear in the table
    try:
        page.locator(f'th a >> text="{part_number}"').wait_for(state="visible", timeout=5000)
    except Exception:
        page.wait_for_timeout(1000)  # Fallback

    # Find the row and set qty
    all_rows = page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all()
    found = False
    for row in all_rows:
        try:
            pn = row.locator('th[scope="row"] a')
            if pn.count() > 0 and pn.first.inner_text().strip() == part_number:
                qty_input = row.locator('input[type="number"]').first
                current = qty_input.input_value()
                qty_input.click()
                qty_input.fill(str(qty))
                print(f"[ACTION] Set {part_number} qty from {current} to {qty}")
                found = True
                break
        except Exception:
            continue

    if not found:
        print(f"[WARNING] Could not find {part_number} row to set qty")

    print(f"[ACTION] {part_number} added")


@timed
def action_set_tag(page: Page, tag_value: str) -> None:
    """Set the Tag field (name=description)."""
    print(f"\n[ACTION] Setting tag to: {tag_value}")
    tag_input = page.locator('input[name="description"]')
    tag_input.click()
    tag_input.fill(tag_value)
    print(f"[ACTION] Tag filled.")


@timed
def action_set_assembly_week(page: Page, date_str: str) -> None:
    """
    Set the Assembly Week date field.
    date_str should be YYYY-MM-DD (HTML date input format).
    """
    print(f"\n[ACTION] Setting assembly week to: {date_str}")
    try:
        label = page.locator('text=Assembly Week').first
        container = label.locator('..')
        date_input = container.locator('input[type="date"], input[type="text"]').first

        if date_input.count() > 0:
            date_input.click()
            date_input.fill(date_str)
            print(f"[ACTION] Assembly week set to: {date_str}")
        else:
            print("[ACTION] Trying fallback: scanning all date inputs in header area...")
            date_inputs = page.locator('input[type="date"]').all()
            for di in date_inputs:
                try:
                    nearby = di.evaluate('el => el.closest("tr, div, td, label")?.innerText || ""')
                    if "Assembly" in nearby:
                        di.click()
                        di.fill(date_str)
                        print(f"[ACTION] Assembly week set (fallback): {date_str}")
                        return
                except Exception:
                    continue
            print("[WARNING] Could not find Assembly Week date input")
    except Exception as e:
        print(f"[WARNING] Could not set assembly week: {e}")


@timed
def action_add_required_parts(page: Page, processor_type: str = None,
                              is_route: bool = False) -> None:
    """
    Read what's on the SO, determine what's missing, add it.
    Includes pinpad kit based on processor_type from SOR.
    Also handles wire splicers from missing parts section.
    Route orders skip CARD-03-01 and SVC-LAUNDROMAT.
    """
    parts_on_order = set()
    vac_count = 0
    touchscreen_vac_count = 0
    pinpad_vac_count = 0

    rows = page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all()
    for row in rows:
        try:
            pn = row.locator('th[scope="row"] a')
            if pn.count() > 0:
                part = pn.first.inner_text().strip().upper()
                parts_on_order.add(part)
                if part.startswith("VAC"):
                    inp = row.locator('input[type="number"]')
                    qty = int(inp.first.input_value()) if inp.count() > 0 else 0
                    vac_count += qty
                    d = decode_vac(part)
                    if d["is_touchscreen"]:
                        touchscreen_vac_count += qty
                    if d["needs_pinpad"]:
                        pinpad_vac_count += qty
        except Exception:
            continue

    print(f"\nParts on order: {len(parts_on_order)}")
    print(f"VAC count: {vac_count}, Touchscreen: {touchscreen_vac_count}, Pinpad: {pinpad_vac_count}")

    to_add = []

    # Order matters for the product table -- hardware first, SVC last
    # 1. CARD-03-01: system cards, always qty=1 (not for routes)
    if vac_count > 0 and "CARD-03-01" not in parts_on_order and not is_route:
        to_add.append(("CARD-03-01", 1))
    elif is_route:
        print("[INFO] Route order — skipping CARD-03-01")

    # 2. Paper rolls (03-01-34): only for touchscreen VACs (cabinet 07/08)
    if touchscreen_vac_count > 0 and "03-01-34" not in parts_on_order:
        to_add.append(("03-01-34", touchscreen_vac_count))

    # 3. Pinpad kit: only for VACs with pinpad digit != 0
    if pinpad_vac_count > 0 and processor_type is not None:
        kit = determine_pinpad_kit(processor_type)
        if kit.upper() not in parts_on_order:
            to_add.append((kit, pinpad_vac_count))
            print(f"[INFO] Pinpad kit: {kit} qty={pinpad_vac_count} (processor_type='{processor_type}')")
        else:
            print(f"[INFO] Pinpad kit {kit} already on order")
    elif pinpad_vac_count > 0:
        print("[WARNING] Pinpad needed but no processor_type provided -- skipping kit. Use --read-sor first.")

    # 4. SVC-LAUNDROMAT last — software, not physical (not for routes)
    if vac_count > 0 and "SVC-LAUNDROMAT" not in parts_on_order and not is_route:
        to_add.append(("SVC-LAUNDROMAT", 1))
    elif is_route:
        print("[INFO] Route order — skipping SVC-LAUNDROMAT")

    # --- Missing parts section (MOOPS-suggested associations) ---
    # Analyze each suggestion using what we know. Decide: add, skip, or flag.
    #
    # Decision logic (order matters):
    #   1. Wire splicers (03-01-43) → ALWAYS add to existing qty (they stack)
    #      Must check BEFORE "already on order" since splicers are usually already there.
    #   2. Already handled by rule-based logic above → skip
    #   3. "OLD VERSION" in description → skip (outdated mapping)
    #   4. Blocker plates (01-05-56) from X-series/USX reader (CR-*-126 family) → skip (built-in blockouts)
    #   4b. Long power cable (02-06-78*) → skip (almost never needed; add manually if required)
    #   5. VAC pedestal/base (01-03-03) → skip (large item, customer-ordered only)
    #   6. Everything else (cables, etc.) → add
    missing = []
    flagged = []
    if page.locator('text=Missing part associations detected').count() > 0:
        tables = page.locator('table').all()
        for table in tables:
            ths = table.locator('th').all_inner_texts()
            if 'Associated Part' in ths:
                for tr in table.locator('tr').all()[1:]:
                    cells = tr.locator('th, td').all_inner_texts()
                    if len(cells) >= 4:
                        missing.append({"source": cells[0], "part": cells[1], "desc": cells[2], "qty": cells[3]})
                break

    # Collapse duplicate missing-part rows by part number BEFORE processing. MOOPS lists the same
    # companion once per source (e.g. wire splicer 03-01-43 from HV-SENSOR-01 qty 16 AND from
    # 02-06-72 qty 160), but it's ONE product line on the order -- the totals add up (176). Without
    # this, the 2nd entry tried to add 03-01-43 as a SECOND line; MOOPS keeps "Add To Order"
    # disabled for a duplicate part, which hung the run (Matt: SO-20080). Summing -> add/stack once.
    if missing:
        agg = {}
        for m in missing:
            key = m["part"].strip().upper()
            qn = m["qty"].strip()
            qn = int(qn) if qn.isdigit() else 0
            if key in agg:
                prev = agg[key]["qty"].strip()
                agg[key]["qty"] = str((int(prev) if prev.isdigit() else 0) + qn)
                if m["source"].strip() not in agg[key]["source"]:
                    agg[key]["source"] = f"{agg[key]['source']} + {m['source'].strip()}"
            else:
                agg[key] = dict(m)
        missing = list(agg.values())

    # Track what we're already adding to avoid duplicates
    already_adding = {part.upper() for part, qty in to_add}

    # Wrong-processor pinpad parts: a combo VAC's missing-parts lists BOTH the A35 (Fortis) and
    # P630 (Stripe) pinpad options; keep only the order's processor family (Matt). VAC orders only.
    wrong_pinpad = set()
    if vac_count > 0:
        wrong_pinpad = (_PINPAD_PARTS_P630 if determine_pinpad_kit(processor_type or "") == "KIT-A35"
                        else _PINPAD_PARTS_A35)

    for m in missing:
        mp = m["part"].strip()
        mp_upper = mp.upper()
        mq_str = m["qty"].strip()
        mq = int(mq_str) if mq_str.isdigit() else 0
        desc = m["desc"]
        desc_upper = desc.upper()
        source = m["source"].strip()
        source_upper = source.upper()

        if mq <= 0:
            continue

        # 1. Wire splicers — ALWAYS add to existing qty (check FIRST, before "already on order")
        if mp == "03-01-43":
            existing_qty = 0
            for row in rows:
                try:
                    pn = row.locator('th[scope="row"] a')
                    if pn.count() > 0 and pn.first.inner_text().strip() == "03-01-43":
                        qty_input = row.locator('input[type="number"]').first
                        existing_qty = int(qty_input.input_value())
                        break
                except Exception:
                    continue
            if existing_qty > 0:
                new_qty = existing_qty + mq
                print(f"[ADD] Splicers: {existing_qty} on order + {mq} missing = {new_qty}")
                update_existing_part_qty(page, "03-01-43", new_qty)
            else:
                # Insert before SVC-LAUNDROMAT
                svc_idx = next((i for i, (p, q) in enumerate(to_add) if p.upper() == "SVC-LAUNDROMAT"), None)
                if svc_idx is not None:
                    to_add.insert(svc_idx, ("03-01-43", mq))
                else:
                    to_add.append(("03-01-43", mq))
                already_adding.add("03-01-43")
                print(f"[ADD] Splicers: 03-01-43 qty={mq} (from missing parts)")
            continue

        # 2. Already handled by rule-based logic or on the order
        if mp_upper in already_adding:
            print(f"[SKIP] {mp} qty={mq} — already queued")
            continue
        if mp_upper in parts_on_order:
            print(f"[SKIP] {mp} qty={mq} — already on order")
            continue

        # 2b. Wrong-processor pinpad part — MOOPS lists both A35 and P630 options on a combo VAC;
        # keep only the order's processor family. (The combo full-kit-vs-attachment over-add is
        # handled in rule 5c below: an attachment in missing parts drops the rule-based full kit.)
        if mp_upper in wrong_pinpad:
            fam = "Fortis/A35" if mp_upper in _PINPAD_PARTS_A35 else "Stripe/P630"
            flagged.append(f"  SKIPPED: {mp:15s} qty={mq:3d}  — {fam} pinpad part, wrong processor for this order")
            print(f"[SKIP] {mp} qty={mq} — wrong-processor pinpad part")
            continue

        # 3. "OLD VERSION" / "OBSOLETE" in description — outdated mapping. MOOPS often disables
        # the Add-To-Order button for these (a disabled button hung a run), so never auto-add.
        if "OLD VERSION" in desc_upper or "OBSOLETE" in desc_upper:
            flagged.append(f"  SKIPPED: {mp:15s} qty={mq:3d}  — obsolete/old version ({desc[:50]})")
            print(f"[SKIP] {mp} qty={mq} — obsolete/old version in description")
            continue

        # 4. Blocker plates from X-series / USX readers (CR-*-126 family) — built-in blockouts
        if mp_upper == "01-05-56" and "CR-" in source_upper and "-126" in source_upper:
            flagged.append(f"  SKIPPED: {mp:15s} qty={mq:3d}  — X-series/USX reader doesn't need blocker plates")
            print(f"[SKIP] {mp} qty={mq} — X-series/USX reader ({source}) has built-in blockouts")
            continue

        # 4b. Reader companion cables (02-06-*) — broken-out BOM. MOOPS suggests every cable a
        # reader *could* use (MDC vs ACA vs long vs start-pulse); the tech picks the right one
        # per install, so NONE are auto-added. Flag for manual add (Matt: "those are the cables
        # that fall into the not needed category"). Anything already on the order is kept by the
        # "already on order" check above.
        if mp_upper.startswith("02-06-"):
            flagged.append(f"  SKIPPED: {mp:15s} qty={mq:3d}  — reader cable, install-dependent (add manually if required)")
            print(f"[SKIP] {mp} qty={mq} — reader cable, not auto-added")
            continue

        # 5. VAC pedestal/base (01-03-03) — large item, only if customer ordered it
        if mp_upper == "01-03-03":
            flagged.append(f"  SKIPPED: {mp:15s} qty={mq:3d}  — VAC pedestal (customer-ordered only)")
            print(f"[SKIP] {mp} qty={mq} — VAC pedestal, only add if customer ordered")
            continue

        # 5b. Drilling template (01-05-70) — reusable back-plate tooling. MOOPS reports one per
        # reader, but a single template drills every stud hole, so only ONE is needed per order
        # (Matt: "a drilling template is needed, but only 1, not as many as it says are missing").
        if mp_upper == "01-05-70":
            print(f"[ADD] {mp} qty=1 (drilling template — reusable; MOOPS asked for {mq})")
            mq = 1

        # 5c. Combo VAC: a pinpad ATTACHMENT in missing parts means the VAC ships with the pinpad
        # integrated -- it needs the attachment (mounting + butt cable), NOT the full pinpad kit the
        # rule-based logic queued above. Drop that full kit (Matt: "adding the P630 attachment means
        # we don't need the Stripe P630 we typically add"). The wrong-processor rule (2b) already
        # dropped the other family's attachment, so this one matches the order's processor family.
        # The attachment itself still gets added by rule 6 below (no `continue`).
        if mp_upper in ("KIT-P630_ATTACHMENT", "KIT-P630-ATTACHMENT", "KIT-A35-ATTACHMENT"):
            full_kit = determine_pinpad_kit(processor_type or "").upper()  # KIT-P630 / KIT-A35
            n_before = len(to_add)
            to_add[:] = [(p, q) for (p, q) in to_add if p.upper() != full_kit]
            if len(to_add) < n_before:
                already_adding.discard(full_kit)
                print(f"[INFO] Combo VAC ({source}) needs {mp} -> dropping rule-based full kit {full_kit}")

        # 6. Everything else — add it
        svc_idx = next((i for i, (p, q) in enumerate(to_add) if p.upper() == "SVC-LAUNDROMAT"), None)
        if svc_idx is not None:
            to_add.insert(svc_idx, (mp, mq))
        else:
            to_add.append((mp, mq))
        already_adding.add(mp_upper)
        print(f"[ADD] {mp} qty={mq} (from missing parts, source: {source})")

    # Print what was skipped so the human knows
    if flagged:
        print(f"\n  Missing parts skipped (review if needed):")
        for line in flagged:
            print(line)
        print(f"  Use --add-part PART --qty N to override\n")

    if not to_add:
        print("Nothing to add -- all required parts present.")
        return

    print(f"\nWill add {len(to_add)} parts:")
    for part, qty in to_add:
        print(f"  {part} qty={qty}")

    for part, qty in to_add:
        action_add_part(page, part, qty)

    return to_add


@timed
def action_add_splicers(page: Page) -> None:
    """Read wire splicers from missing parts, add to existing qty on the order."""
    splicer_info = read_wire_splicers_from_missing(page)
    if not splicer_info:
        print("[INFO] No wire splicers in missing parts -- nothing to do")
        return

    missing_qty = splicer_info["qty"]
    if missing_qty <= 0:
        print("[INFO] Wire splicer qty is 0 -- skipping")
        return

    # Check if 03-01-43 is already on the order
    existing_qty = 0
    rows = page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all()
    for row in rows:
        try:
            pn = row.locator('th[scope="row"] a')
            if pn.count() > 0 and pn.first.inner_text().strip() == "03-01-43":
                qty_input = row.locator('input[type="number"]').first
                existing_qty = int(qty_input.input_value())
                break
        except Exception:
            continue

    if existing_qty > 0:
        new_qty = existing_qty + missing_qty
        print(f"[INFO] 03-01-43 on order: {existing_qty} + {missing_qty} missing = {new_qty}")
        update_existing_part_qty(page, "03-01-43", new_qty)
    else:
        print(f"[INFO] 03-01-43 not on order -- adding qty={missing_qty}")
        action_add_part(page, "03-01-43", missing_qty)


@timed
def action_set_system_tasks(page: Page, is_route: bool = False) -> None:
    """
    Set task checklist for first touch.
    Detects card state from the product table automatically.

    System orders:
      1: Hardware verified         -> Completed
      2: End-customer info         -> Completed
      3: Connected via (card email)-> Completed (new card) or N/A (no card)
      4: Card approval received    -> To Do (new card) or N/A
      5: Final card proofs, PO     -> To Do (any card) or N/A
      6: SaaS contract             -> To Do (Salesforce, not automated)
      7-10: Provisioning steps     -> To Do

    Route orders:
      1-2: Completed
      3-10: N/A (no ITF, no provisioning)
    """
    # Detect card state from the product table -- SAME for route and system. A route CAN carry a
    # card design (most don't); when it does, its card tasks follow the same rule as a system order.
    has_card = False
    has_new_card = False
    rows = page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all()
    for row in rows:
        try:
            pn = row.locator('th[scope="row"] a')
            if pn.count() > 0:
                part = pn.first.inner_text().strip()
                if part.startswith("CARD-MD-") and part != "CARD-03-01":
                    has_card = True
                    if "PLACEHOLDER" in row.inner_text().upper():
                        has_new_card = True
        except Exception:
            continue

    # Card task logic (shared by both paths):
    #   New design (PLACEHOLDER) → 3=Completed (email sent), 4=To Do (proof), 5=To Do (PO)
    #   Existing card (reprint)  → 3=N/A, 4=N/A, 5=To Do (PO still needed)
    #   No card                  → 3/4/5 N/A
    card_tasks = {
        3: "Completed" if has_new_card else "N/A",
        4: "To Do" if has_new_card else "N/A",
        5: "To Do" if has_card else "N/A",
    }
    if is_route:
        # Route / multi-housing: hardware (+ card if present) only -- no SaaS/payment/portal/config.
        statuses = {1: "Completed", 2: "Completed", **card_tasks,
                    6: "N/A", 7: "N/A", 8: "N/A", 9: "N/A", 10: "N/A"}
        print("\n[ACTION] Setting route order task checklist:")
    else:
        # System: SaaS (6 = Salesforce, not automated) + provisioning (7-10) all To Do.
        statuses = {1: "Completed", 2: "Completed", **card_tasks,
                    6: "To Do", 7: "To Do", 8: "To Do", 9: "To Do", 10: "To Do"}
        print("\n[ACTION] Setting system order task checklist:")
    print(f"  Card detected: has_card={has_card}, has_new_card={has_new_card}")

    for num, status in statuses.items():
        print(f"  Task {num:2d}: {status}")

    set_task_checklist(page, statuses)


@timed
def download_vac_configs(page: Page, so_id, out_dir) -> list:
    """Task 9: one config (.cfg) per VAC UNIT, numbered SEQUENTIALLY across the order
    (VAC01.cfg, VAC02.cfg, ...). Each VAC product row has a 'Get Config' button
    (button.btn.btn-xs.btn-dark.tm-1). Two cases (Matt):
      - distinct VAC lines (qty 1 each): each click downloads that VAC's own config.
      - a qty>1 line (e.g. 2x VAC07 on ONE line, one button): clicking downloads the SAME
        file each time -> download once and COPY it per unit, assigning the running number.
    We number per unit by ORDER POSITION (don't trust the filename), so both cases get
    VAC01/VAC02/... Chrome blocks .cfg as 'unverified', but Playwright's download capture
    grabs it at the protocol level. Returns the list of saved .cfg paths."""
    import os
    import shutil
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    n = 1  # running sequential VAC number across the whole order
    rows = page.locator('tr[id^="existing_part_order_"]')
    for i in range(rows.count()):
        row = rows.nth(i)
        pn = ""
        try:
            a = row.locator('th[scope="row"] a')
            if a.count():
                pn = (a.first.inner_text() or "").strip()
        except Exception:
            pass
        if not pn.upper().startswith("VAC"):
            continue
        btn = row.locator('button:has-text("Get Config")')
        if btn.count() == 0:
            continue
        try:
            qty = int((row.locator('input[type="number"]').first.input_value() or "1") or 1)
        except Exception:
            qty = 1
        try:  # one click downloads this VAC type's config (same file for every unit of the row)
            with page.expect_download(timeout=20000) as dl_info:
                btn.first.click()
            dl = dl_info.value
            orig_name = dl.suggested_filename or f"VAC01_{pn}.cfg"
            tmp = os.path.join(out_dir, "_tmp.cfg")
            dl.save_as(tmp)
        except Exception as e:
            print(f"[CONFIG] Get Config failed for {pn} ({e}) -- skipping.")
            continue
        try:
            with open(tmp, "r", encoding="utf-8") as fh:
                content = fh.read()
        except Exception as e:
            print(f"[CONFIG] Couldn't read downloaded config for {pn} ({e}).")
            content = None
        for _ in range(max(qty, 1)):  # one .cfg per unit, sequential VAC number
            # Keep the MOOPS-generated filename format, just increment the _VAC{nn}_ part.
            # e.g. SO19885_Parkway Washateria_0100004_VAC01_VAC07-62-20.cfg
            #   -> SO19885_Parkway Washateria_0100004_VAC02_VAC07-62-20.cfg for unit 2
            # MOOPS names each downloaded VAC with a _VACnn_ token already. If it's there,
            # NORMALIZE that token to the running unit number (count=1) -- nothing else. Only when
            # there's genuinely no _VACnn_ token do we append a suffix. (The old test
            # `dest_name == orig_name and n > 1` wrongly fired when the token already equalled the
            # running number -- e.g. a 2nd distinct VAC downloaded as _VAC02_ -- and tacked on an
            # extra _VAC02, e.g. ..._VAC02_VAC04-40-20_VAC02.cfg.)
            if re.search(r'_VAC\d+_', orig_name):
                dest_name = re.sub(r'_VAC\d+_', f'_VAC{n:02d}_', orig_name, count=1)
            else:
                base, ext = os.path.splitext(orig_name)
                dest_name = f"{base}_VAC{n:02d}{ext}"
            dest = os.path.join(out_dir, dest_name)
            try:
                if content is not None:
                    # Patch KioskName to match the sequential VAC number.
                    patched, nsub = re.subn(
                        r'("SettingName"\s*:\s*"KioskName"\s*,\s*"SettingValue"\s*:\s*")[^"]*(")',
                        lambda m: f'{m.group(1)}VAC{n:02d}{m.group(2)}', content, count=1)
                    if nsub == 0:
                        print(f"[CONFIG] [WARN] No KioskName line found in {pn}'s config -- "
                              "wrote unchanged; set KioskName manually.")
                    with open(dest, "w", encoding="utf-8") as fh:
                        fh.write(patched)
                else:
                    shutil.copyfile(tmp, dest)
                saved.append(dest)
                print(f"[CONFIG] {pn} (unit {n}) -> {dest_name} (KioskName=VAC{n:02d})")
            except Exception as e:
                print(f"[CONFIG] Couldn't write {dest_name} ({e}).")
            n += 1
        try:
            os.remove(tmp)
        except Exception:
            pass
    if not saved:
        print("[CONFIG] No VAC config files downloaded (no 'Get Config' buttons / no VAC rows?).")
    else:
        print(f"[CONFIG] {len(saved)} config file(s) saved to {out_dir}")
    return saved


@timed
def download_location_vac_configs(page: Page, so_id, out_dir, location_id, units) -> list:
    """Laundrylux stock flow: download THIS location's VAC configs after its End Customer
    location has been linked + saved on the SO (so MOOPS bakes the right location-specific
    data into each .cfg). `units` is the list of (kiosk_n, part_number) pairs for this
    location (1 or 2 of them) -- KioskName restarts at VAC01 per location. Each .cfg is the
    matching VAC row's Get Config download (regenerated for the now-linked location), with only
    KioskName patched to VAC{kiosk_n:02d}. Returns the saved .cfg paths.

    Unlike download_vac_configs (one pass, sequential VAC01..VACn across the order), this is
    called ONCE PER LOCATION so the location-specific fields differ per pair (Matt: you must
    re-download on each location switch -- the file pulls location-specific info)."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    rows = page.locator('tr[id^="existing_part_order_"]')
    # Map part_number -> row for quick lookup (first matching VAC row per part).
    row_by_part = {}
    for i in range(rows.count()):
        try:
            a = rows.nth(i).locator('th[scope="row"] a')
            pn = (a.first.inner_text() or "").strip() if a.count() else ""
        except Exception:
            pn = ""
        if pn and pn.upper().startswith("VAC") and pn not in row_by_part:
            row_by_part[pn] = rows.nth(i)
    for kiosk_n, part in units:
        row = row_by_part.get(part)
        if row is None:
            print(f"[CONFIG] No VAC row for {part} at location {location_id} -- skipping that unit.")
            continue
        btn = row.locator('button:has-text("Get Config")')
        if btn.count() == 0:
            print(f"[CONFIG] No 'Get Config' button on {part} row -- skipping.")
            continue
        tmp = os.path.join(out_dir, "_tmp.cfg")
        try:
            with page.expect_download(timeout=20000) as dl_info:
                btn.first.click()
            dl_info.value.save_as(tmp)
            with open(tmp, "r", encoding="utf-8") as fh:
                content = fh.read()
        except Exception as e:
            print(f"[CONFIG] Get Config failed for {part} @ {location_id} ({e}) -- skipping.")
            continue
        patched, nsub = re.subn(
            r'("SettingName"\s*:\s*"KioskName"\s*,\s*"SettingValue"\s*:\s*")[^"]*(")',
            lambda m: f'{m.group(1)}VAC{kiosk_n:02d}{m.group(2)}', content, count=1)
        if nsub == 0:
            print(f"[CONFIG] [WARN] No KioskName line in {part}'s config -- wrote unchanged.")
        dest = os.path.join(out_dir, f"SO{so_id}_LL_{location_id}_VAC{kiosk_n:02d}_{part}.cfg")
        try:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(patched)
            saved.append(dest)
            print(f"[CONFIG] {location_id} VAC{kiosk_n:02d} ({part}) -> {os.path.basename(dest)}")
        except Exception as e:
            print(f"[CONFIG] Couldn't write {os.path.basename(dest)} ({e}).")
        try:
            os.remove(tmp)
        except Exception:
            pass
    return saved


def upload_files_to_so(page: Page, paths: list) -> bool:
    """ADD files to the SO's File Resources by driving the page's own 'Upload Files' button
    (#fileTrigger) through the file chooser -- the same action a human does. That fires MOOPS's
    upload handler (an AJAX upload of just the files), which is why it works where set_input_files
    on the hidden input did NOT, and why it avoids the whole-order native POST that threw the
    'submission error'. Caller then Saves + verifies. Returns True if files were handed off."""
    import os
    if not paths:
        return False
    names = [os.path.basename(p) for p in paths]
    trigger = page.locator('#fileTrigger')
    if trigger.count() > 0:
        try:
            with page.expect_file_chooser(timeout=8000) as fc_info:
                trigger.first.click()
            fc_info.value.set_files(paths)
            time.sleep(2)  # let MOOPS's upload handler run
            print(f"[CONFIG] Added {len(names)} file(s) via the 'Upload Files' button.")
            return True
        except Exception as e:
            print(f"[CONFIG] 'Upload Files' chooser flow failed ({e}) -- falling back to the hidden input.")
    # Fallback: set the hidden input directly (older page versions).
    inp = page.locator('#uploadFiles')
    if inp.count() == 0:
        print("[CONFIG] No upload control found -- attach the .cfg files manually.")
        return False
    try:
        inp.set_input_files(paths)
        time.sleep(1)
        print(f"[CONFIG] Set {len(names)} file(s) on #uploadFiles (fallback).")
    except Exception as e:
        print(f"[CONFIG] Could not set file input ({e}) -- attach manually from {os.path.dirname(paths[0])}.")
        return False
    return True


def read_config_file_resources(page: Page) -> list:
    """Return the unique .cfg files attached in the SO File Resources area. MOOPS renders each
    attached file as TWO anchors that share one /files/<id> href (a "View" link + a filename
    link), so we dedupe by that file id -- the true unique file. We do NOT scan the page's body
    text for .cfg tokens: filenames contain spaces (e.g. 'SO..._Precision Laundry_...cfg'), so a
    \\S+\\.cfg regex grabbed the post-space fragment as a PHANTOM name and over-counted 2 files
    as 4 (Matt). Falls back to the lowercased name only when an anchor has no /files/<id> href."""
    try:
        names = page.evaluate(r"""() => {
            const byKey = new Map();
            for (const a of document.querySelectorAll('a[href*="/files/"], a[download]')) {
                const dl = (a.getAttribute('download') || '').trim();
                const txt = (a.textContent || '').trim();
                const name = /\.cfg$/i.test(dl) ? dl : (/\.cfg$/i.test(txt) ? txt : '');
                if (!name) continue;
                const m = (a.getAttribute('href') || '').match(/\/files\/(\d+)/);
                const key = m ? ('file:' + m[1]) : ('name:' + name.toLowerCase());
                if (!byKey.has(key)) byKey.set(key, name);
            }
            return [...byKey.values()];
        }""")
        print(f"[READ] Config files visible on SO: {len(names or [])}")
        return names or []
    except Exception as e:
        print(f"[READ] Could not read config file resources ({e})")
        return []


# Card shipping charge ("Cost" column of the card shipping table, keyed by quantity). Mitech covers
# shipping for the first 5000 cards on a system order; cards beyond 5000 are charged on the EXCESS.
_CARD_SHIP_COST = {1000: 125.00, 2000: 225.00, 3000: 285.00, 5000: 290.00, 6000: 324.00,
                   10000: 500.00, 15000: 750.00, 20000: 580.00, 50000: 1350.00}


def action_add_card_shipping(page: Page) -> None:
    """SYSTEM orders only: the first 5000 cards ship free; cards beyond 5000 are charged. If the SO
    has VACs (system) AND total card qty > 5000, add a 'SHIPPING' line (qty 1) priced at the charge
    for the EXCESS (total - 5000) from the card shipping table (Air default; Sea only if comments
    ask). Fill-only -- human verifies the price. Matt: 10000 cards -> excess 5000 -> $290."""
    has_vac = False
    card_qty = 0
    for row in page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all():
        try:
            pn = row.locator('th[scope="row"] a')
            if pn.count() == 0:
                continue
            part = pn.first.inner_text().strip().upper()
            if part.startswith("VAC"):
                has_vac = True
            elif part == "SHIPPING":
                print("[CARDS] SHIPPING line already on the SO -- not adding another.")
                return
            elif part.startswith("CARD-") and part != "CARD-03-01":
                inp = row.locator('input').all()
                if inp and inp[0].input_value().isdigit():
                    card_qty = max(card_qty, int(inp[0].input_value()))
        except Exception:
            continue
    if not has_vac:
        return  # cards-only order -- the >5000 shipping line is a SYSTEM-order rule
    if card_qty <= 5000:
        return
    excess = card_qty - 5000
    charge = _CARD_SHIP_COST.get(excess)
    print(f"\n[CARDS] >5000 cards ({card_qty}) on a system order -- adding SHIPPING line for the "
          f"{excess} cards over the free 5000.")
    action_add_part(page, "SHIPPING", 1)
    set_price = None
    for row in page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all():
        try:
            pn = row.locator('th[scope="row"] a')
            if pn.count() > 0 and pn.first.inner_text().strip().upper() == "SHIPPING":
                inputs = row.locator('input').all()
                if charge is not None and len(inputs) >= 2:
                    inputs[1].click()
                    inputs[1].fill(f"{charge:.2f}")
                    set_price = charge
                break
        except Exception:
            continue
    if set_price is not None:
        print(f"[CARDS] SHIPPING price set to {set_price:.2f} (Air default; use the Sea price if the "
              "SOR comments ask). VERIFY at the save pause.")
    else:
        print(f"[CARDS] [FLAG] No table price for excess {excess} -- SHIPPING line added; set the "
              "price from the card shipping table at the save pause (VERIFY).")


@timed
def action_add_card_to_so(page: Page, card_part_number: str) -> None:
    """
    Add a card part to the SO, matching qty and price from the source card.
    Looks for CARD-01-02 first (new design from SOR), then falls back to
    any existing CARD-MD-* (card modify flow).
    Pauses for human confirmation before deleting the source.
    """
    # Find source card: any CARD- part that isn't the one we're adding
    # Matches CARD-01-02 (new design placeholder) or CARD-MD-* (modify)
    card_qty = 0
    card_price = ""
    source_part = ""
    rows = page.locator('tr[id^="existing_part_order_"]').all()
    for row in rows:
        try:
            pn = row.locator('th[scope="row"] a')
            if pn.count() > 0:
                part = pn.first.inner_text().strip()
                if part.startswith("CARD-") and part != card_part_number and part != "CARD-03-01":
                    source_part = part
                    inputs = row.locator('input').all()
                    if len(inputs) >= 1:
                        card_qty = int(inputs[0].input_value()) if inputs[0].input_value().isdigit() else 0
                    if len(inputs) >= 2:
                        card_price = inputs[1].input_value().strip()
                    break
        except Exception:
            continue

    if card_qty == 0:
        print("[WARNING] Could not find source CARD- part on order")
        return

    print(f"\n[ACTION] Adding {card_part_number} to SO (matching {source_part}: qty={card_qty}, price={card_price})")

    # Add the new card part
    action_add_part(page, card_part_number, card_qty)

    # Set the price to match CARD-01-02
    if card_price:
        all_rows = page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all()
        for row in all_rows:
            try:
                pn = row.locator('th[scope="row"] a')
                if pn.count() > 0 and pn.first.inner_text().strip() == card_part_number:
                    inputs = row.locator('input').all()
                    if len(inputs) >= 2:
                        inputs[1].click()
                        inputs[1].fill(card_price)
                        print(f"[ACTION] Set {card_part_number} price to {card_price}")
                    break
            except Exception:
                continue

    # Verify the new card matches before deleting placeholder
    verified = False
    all_rows = page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all()
    for row in all_rows:
        try:
            pn = row.locator('th[scope="row"] a')
            if pn.count() > 0 and pn.first.inner_text().strip() == card_part_number:
                inputs = row.locator('input').all()
                new_qty = inputs[0].input_value() if len(inputs) >= 1 else ""
                new_price = inputs[1].input_value() if len(inputs) >= 2 else ""
                print(f"[VERIFY] {card_part_number}: qty={new_qty}, price={new_price}")
                print(f"[VERIFY] {source_part} was: qty={card_qty}, price={card_price}")
                if str(new_qty) == str(card_qty) and new_price == card_price:
                    verified = True
                    print("[VERIFY] Match confirmed")
                else:
                    print(f"[WARNING] QTY OR PRICE MISMATCH -- NOT deleting {source_part}")
                break
        except Exception:
            continue

    # Pause before deleting source card
    if verified:
        print(f"[PAUSE] CARD-MD added and verified. Press Enter to delete {source_part}.")
        try:
            input()
        except KeyboardInterrupt:
            print(f"\n[ABORT] Skipping {source_part} deletion")
            return
        delete_card_placeholder(page, source_part)
    else:
        print(f"[WARNING] Skipping {source_part} deletion -- verify manually")

    print(f"[ACTION] {card_part_number} added: qty={card_qty}, price={card_price}")


def read_shipping_to(page: Page) -> dict:
    """
    Parse the Shipping To textarea on the SO page.
    Format (typical):
      Bubbles & Suds Laundromat
      8003 5th Avenue
      Brooklyn, New York, 11209, United States
      ATTN: Omar Kasi. 917-734-0633

    Returns dict with: company, address, city, state, zip, attn_name, phone
    """
    result = {
        "company": "",
        "address": "",
        "city": "",
        "state": "",
        "zip": "",
        "attn_name": "",
        "phone": "",
        "raw": "",
    }
    try:
        textarea = page.locator('textarea').filter(has_text="ATTN")
        if textarea.count() == 0:
            # Fallback: look for Shipping To label area
            textarea = page.locator('textarea[name*="ship"], textarea[name*="address"]').first
            if textarea.count() == 0:
                print("[READ] No Shipping To textarea found")
                return result
        raw = textarea.first.input_value().strip()
        result["raw"] = raw
    except Exception as e:
        print(f"[READ] Error reading Shipping To: {e}")
        return result

    if not raw:
        return result

    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if not lines:
        return result

    # Line 1: Company name
    result["company"] = lines[0] if len(lines) > 0 else ""

    # Line 2: Street address
    result["address"] = lines[1] if len(lines) > 1 else ""

    # Line 3: City, State, Zip, Country
    if len(lines) > 2:
        addr_line = lines[2]
        # Remove "United States" or "US"
        addr_line = re.sub(r',?\s*United States\s*$', '', addr_line, flags=re.IGNORECASE).strip()
        parts = [p.strip() for p in addr_line.split(",")]
        if len(parts) >= 3:
            result["city"] = parts[0]
            result["state"] = abbreviate_state(parts[1]) if len(parts[1]) > 2 else parts[1].upper()
            # State might already be abbreviated
            if result["state"] in STATE_ABBREVIATIONS.values():
                pass
            elif parts[1].strip().lower() in STATE_ABBREVIATIONS:
                result["state"] = STATE_ABBREVIATIONS[parts[1].strip().lower()]
            result["zip"] = parts[2].strip()
        elif len(parts) == 2:
            result["city"] = parts[0]
            result["zip"] = parts[1].strip()

    # ATTN line: "ATTN: Name. Phone" or "ATTN: Name, Phone"
    for line in lines:
        if line.upper().startswith("ATTN"):
            after = line.split(":", 1)[1].strip() if ":" in line else line[4:].strip()
            # Split on period or last comma
            # "Omar Kasi. 917-734-0633" or "Omar Kasi, 917-734-0633"
            phone_match = re.search(r'[\.\,]\s*([\d\-\(\)\s\+]+)$', after)
            if phone_match:
                result["phone"] = phone_match.group(1).strip()
                result["attn_name"] = after[:phone_match.start()].strip()
            else:
                result["attn_name"] = after

    # Check remaining lines for standalone phone number (digits only or with dashes)
    if not result["phone"]:
        for line in lines:
            if line.upper().startswith("ATTN") or line == result["company"] or line == result["address"]:
                continue
            cleaned = re.sub(r'[\s\-\(\)\+\.]', '', line)
            if cleaned.isdigit() and 7 <= len(cleaned) <= 15:
                result["phone"] = line
                break

    #
    print(f"[READ] Shipping To: {result['company']}, {result['address']}, "
          f"{result['city']}, {result['state']} {result['zip']}")
    if result["attn_name"]:
        print(f"[READ] ATTN: {result['attn_name']}, Phone: {result['phone']}")

    return result


@timed
def read_sor_shipping_method(page: Page) -> str:
    """
    Navigate to the linked SOR and read the Shipping Method field.
    Returns the raw shipping text (e.g. "NEXT DAY", "Ground", "Freight").
    Waits for Angular render instead of hard sleep.
    """
    so_url = page.url
    sor_href = read_sor_link(page)
    if not sor_href:
        print("[READ] No SOR link — cannot read shipping method")
        return ""

    sor_url = sor_href if sor_href.startswith("http") else f"{MOOPS_BASE}{sor_href}"
    print(f"[NAV] Going to SOR for shipping method: {sor_url}")
    page.goto(sor_url, wait_until="domcontentloaded", timeout=20000)

    # Wait for Angular to render — look for actual content, not template tags
    try:
        page.wait_for_function(
            "() => !document.body.textContent.includes('{{') || false",
            timeout=5000
        )
    except Exception:
        time.sleep(1)  # Fallback: brief wait if detection fails

    shipping_method = ""
    try:
        labels = page.locator('label, th, td, dt').all()
        for label in labels:
            text = label.inner_text().strip()
            text_l = text.lower()
            is_shipping_label = (
                ("ship" in text_l and "method" in text_l)
                or "shipping method" in text_l
                or "delivery method" in text_l
            )
            if is_shipping_label:
                parent = label.locator('..')
                sel = parent.locator('select')
                if sel.count() > 0:
                    shipping_method = sel.first.evaluate('el => el.options[el.selectedIndex]?.text || ""').strip()
                else:
                    val = parent.inner_text().replace(text, "").strip()
                    if val:
                        shipping_method = val
                if shipping_method:
                    break

        if not shipping_method:
            try:
                comments_labels = page.locator('label, th').filter(has_text="Comments").all()
                for cl in comments_labels:
                    parent = cl.locator('..')
                    comment_text = parent.inner_text().upper()
                    if "NEXT DAY" in comment_text or "OVERNIGHT" in comment_text:
                        shipping_method = "NEXT DAY"
                        print(f"[READ] Shipping urgency from comments: NEXT DAY")
                        break
            except Exception:
                pass
    except Exception as e:
        print(f"[READ] Error reading SOR shipping method: {e}")

    print(f"[READ] SOR shipping method: '{shipping_method}'")

    print(f"[NAV] Returning to SO: {so_url}")
    page.goto(so_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector('tr[id^="existing_part_order_"]', timeout=15000)

    return shipping_method


def map_sor_to_efs_shipping(sor_method: str, sor_comments: str = "") -> str:
    """
    Map SOR shipping method / comments to EFS Ship Via option.
    EFS options: 'FedEx Standard Overnight', 'FedEx Ground', etc.
    """
    combined = f"{sor_method} {sor_comments}".upper()
    if "NEXT DAY" in combined or "OVERNIGHT" in combined:
        return "FedEx Standard Overnight"
    if "GROUND" in combined:
        return "FedEx Ground"
    if "FREIGHT" in combined:
        return "FedEx Ground"  # Freight usually maps to ground for EFS
    # Default to ground
    return "FedEx Ground"


@timed
def action_set_shipment_3pl(page: Page) -> None:
    """
    Set Shipment By to '3PL - EFS' which triggers the -DS part swap dialog.
    Clicks OK on the swap dialog.
    """
    print("[ACTION] Setting Shipment By to '3PL - EFS'...")
    try:
        sel = page.locator('select[name="part_inventory_location_address_id"]')
        sel.select_option(label="3PL - EFS")
        print("[ACTION] Selected '3PL - EFS'")
    except Exception:
        # Try matching partial text
        try:
            sel = page.locator('select[name="part_inventory_location_address_id"]')
            options = sel.locator('option').all()
            for opt in options:
                if "3PL" in opt.inner_text():
                    sel.select_option(value=opt.get_attribute("value"))
                    print(f"[ACTION] Selected '{opt.inner_text().strip()}'")
                    break
        except Exception as e:
            print(f"[WARNING] Could not set Shipment By: {e}")
            return

    # Wait for swap dialog and click OK
    page.wait_for_timeout(2000)
    try:
        dialog_text = page.locator('text=Proceed with swap')
        if dialog_text.count() > 0:
            page.locator('button').filter(has_text="OK").first.click()
            print("[ACTION] Clicked OK on part swap dialog")
            page.wait_for_timeout(1500)
        else:
            print("[INFO] No swap dialog appeared")
    except Exception as e:
        print(f"[WARNING] Swap dialog handling: {e}")


@timed
def delete_card_placeholder(page: Page, part_to_delete: str = "CARD-01-02") -> None:
    """
    Delete a card row from the product table.
    Handles the SVG trash icon wrapped in an <a> or <button>.
    """
    print(f"[ACTION] Deleting {part_to_delete}...")
    rows = page.locator('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').all()
    for row in rows:
        try:
            pn = row.locator('th[scope="row"] a')
            if pn.count() > 0 and pn.first.inner_text().strip() == part_to_delete:
                # Try multiple selectors for the delete button:
                delete_target = None
                # 1. <a> or <button> wrapping the trash SVG (most reliable)
                wrapper = row.locator('a:has(svg[data-icon="trash-alt"]), button:has(svg[data-icon="trash-alt"])')
                if wrapper.count() > 0:
                    delete_target = wrapper.first
                else:
                    wrapper = row.locator('a:has(svg.fa-trash-alt), button:has(svg.fa-trash-alt)')
                    if wrapper.count() > 0:
                        delete_target = wrapper.first
                    else:
                        svg = row.locator('svg[data-icon="trash-alt"], svg.fa-trash-alt')
                        if svg.count() > 0:
                            delete_target = svg.first
                        else:
                            delete_target = row.locator('td').last

                delete_target.click()
                page.wait_for_timeout(1000)

                # Handle confirmation dialog if present
                try:
                    confirm = page.locator('button').filter(has_text="Yes")
                    if confirm.count() > 0 and confirm.first.is_visible():
                        confirm.first.click()
                        page.wait_for_timeout(500)
                        print("[ACTION] Confirmed deletion dialog")
                except Exception:
                    pass

                print(f"[ACTION] {part_to_delete} deleted")
                return
        except Exception as e:
            print(f"[WARNING] Could not delete {part_to_delete}: {e}")


@timed
def create_card_po(page: Page, card_part_number: str) -> str:
    """
    Click 'Create PO' on a card product row in the SO page.
    Opens a new PO page. Does NOT modify any PO fields.

    Returns the PO URL or empty string on failure.
    """
    print(f"[ACTION] Creating PO for {card_part_number}...")
    rows = page.locator('tr[id^="existing_part_order_"]').all()
    for row in rows:
        try:
            pn = row.locator('th[scope="row"] a')
            if pn.count() > 0 and pn.first.inner_text().strip().upper() == card_part_number.upper():
                # Find the Create PO button in this row
                po_btn = row.locator('button:has-text("Create PO")')
                if po_btn.count() == 0:
                    po_btn = row.locator('a:has-text("Create PO")')
                if po_btn.count() > 0:
                    # Create PO may open a new tab — listen for it
                    with page.context.expect_page(timeout=10000) as new_page_info:
                        po_btn.first.click()
                    po_page = new_page_info.value
                    po_page.wait_for_load_state("domcontentloaded")
                    po_url = po_page.url
                    print(f"[ACTION] PO created: {po_url}")
                    return po_url
                else:
                    print(f"[WARNING] No 'Create PO' button found for {card_part_number}")
                    return ""
        except Exception:
            # Create PO might open in same tab instead of new tab
            pass

    # Fallback: check if Create PO opened in same tab
    page.wait_for_timeout(3000)
    if "purchase" in page.url:
        print(f"[ACTION] PO created (same tab): {page.url}")
        return page.url

    print(f"[WARNING] Could not find {card_part_number} in product table")
    return ""


@timed
def open_po_email(page: Page) -> None:
    """
    On a PO page, click 'Purchase Order Email', clear CC, and pause for human to send.
    Does NOT click Send.
    """
    print("[ACTION] Opening Purchase Order Email...")

    # Find and click the Purchase Order Email button
    try:
        email_btn = page.locator('button:has-text("Purchase Order Email"), a:has-text("Purchase Order Email")').first
        email_btn.click()
        # Wait for modal to appear instead of hard sleep
        page.wait_for_selector('input[name="email_carbon_copy"]', timeout=5000)
        print("[ACTION] PO email modal opened")
    except Exception as e:
        print(f"[WARNING] Could not open PO email: {e}")
        return

    # Clear CC field (direct selector from modal form)
    try:
        page.locator('input[name="email_carbon_copy"]').fill("")
        print("[ACTION] CC cleared")
    except Exception as e:
        print(f"[WARNING] Could not clear CC: {e}")

    print("[ACTION] PO email ready — review and click Send")
    # Plain Enter to continue (after you've sent or skipped it) -- do NOT instruct Ctrl+C: a SIGINT
    # tears down the Playwright browser and the rest of the chain then dies "browser has been closed".
    print("[PAUSE] Press Enter when done (sent or skipped).")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        print("\n[INFO] Continuing without confirming PO email send.")


def _pick_validity_result(page: Page, token: str) -> bool:
    """Click the dropdown result row whose text starts with `token` (e.g. a cust id
    '01892' or a location id '0100002'). The validity widget renders results as
    clickable rows ('0100002 - 2930 Sacramento Street'); the container class isn't
    pinned, so match by visible text across the common row tags."""
    page.wait_for_timeout(1200)  # let the AJAX results render
    # The validity dropdown renders each result as a <button class="dropdown-item"> (confirmed live
    # on the MOOPS customer page). Try that FIRST -- otherwise a generic <div> wrapper gets clicked
    # and the selection never binds, which silently broke the dealer End-Customer add.
    for tag in ("button.dropdown-item", "li", "a", "tr", "div[role='option']", "div"):
        rows = page.locator(tag).filter(has_text=token)
        for i in range(min(rows.count(), 8)):
            row = rows.nth(i)
            try:
                if not row.is_visible():
                    continue
                txt = (row.inner_text() or "").strip()
                # result rows look like "<id> - <address>"; skip the label/input echo
                if txt.startswith(token) and (" - " in txt or txt == token):
                    row.click()
                    print(f"[ACTION] Picked result '{txt[:40]}'")
                    page.wait_for_timeout(600)
                    return True
            except Exception:
                continue
    return False


def _location_in_ownership_table(page: Page, location_id: str) -> bool:
    """True if `location_id` already appears as a row in the Card Ownership location
    table (rows look like '1600001 - 1320 Maryland Ave E, St. Paul, MN'). Used to keep
    the Add-Location step idempotent on look-back re-runs."""
    if not location_id:
        return False
    try:
        rows = page.locator('tr').filter(has_text=location_id)
        for i in range(min(rows.count(), 10)):
            txt = (rows.nth(i).inner_text() or "").strip()
            if txt.startswith(location_id) or f" {location_id} -" in f" {txt}":
                return True
    except Exception:
        pass
    return False


def _commit_ownership_location(page: Page, location_id: str, loc_search_selector: str) -> None:
    """Add the Location to a Card Ownership block: fill the search-select, pick the dropdown row,
    then click "Add Location" to commit the row to the ownership table. ONE shared path for both
    blocks -- only the search field differs:
      * SO page:        '[id^="validity_$location_filter"]'
      * card-part page: '#validity_portal_location_search'
    The End-Customer must already be set (the location field is enabled once a customer is chosen).

    DOM facts (verified live on the card-part page): the button is
    `<button type="button" class="btn btn-primary mr-sm-2"><span>Add Location</span>...`, DISABLED
    until a row is picked. Because it's type="button" it does NOT submit/save the part -- it just
    adds the row to the ownership table (Angular). Picking the row ALONE does not persist (that's
    why a fill-only pick failed to attach); the Add Location click is required, and the human's own
    Save persists the table. The End-Customer field has no such button (it binds a single value on
    pick); the Location feeds a table, like "Add To Order" for products. Idempotent + never blocks."""
    if not location_id:
        return
    try:
        if _location_in_ownership_table(page, location_id):
            print(f"[INFO] Location {location_id} already in Card Ownership -- skip.")
            return
        loc = page.locator(loc_search_selector)
        if loc.count() == 0:
            print("[INFO] Location search not present yet -- add the location manually.")
            return
        # Fill + pick the dropdown row. A just-created location is slow to come back on the
        # card-part page, so retry the fill/pick a couple of times before giving up (1.2s once
        # wasn't enough -- "No location row matched" on a fresh location).
        picked = False
        for _ in range(3):
            loc.first.click()
            loc.first.fill("")
            loc.first.fill(location_id)
            if _pick_validity_result(page, location_id):
                picked = True
                break
            page.wait_for_timeout(900)
        if not picked:
            print(f"[INFO] No location row matched '{location_id}' after retries -- pick it + "
                  "click Add Location manually.")
            return
        # Click "Add Location" (type=button -> adds the picked row to the ownership table; does NOT
        # save the part). It's disabled until the pick registers, so poll for the ENABLED button in
        # the location field's own form-group row -- matched by its "Add Location" text, not just
        # btn-primary (the row also holds a dropdown-toggle button). Click via JS; skip gracefully
        # if it never enables (the SO page has no such button and its own Save captures the pick).
        clicked = "no-button"
        for _ in range(8):
            clicked = page.evaluate(
                """(sel) => {
                    const inp = document.querySelector(sel);
                    if (!inp) return 'no-input';
                    const row = inp.closest('.form-group') || inp.closest('.row') || document;
                    const btn = [...row.querySelectorAll('button.btn-primary')]
                        .find(b => /add location/i.test(b.textContent || ''));
                    if (!btn) return 'no-button';
                    if (btn.disabled) return 'disabled';
                    btn.click();
                    return 'clicked';
                }""", loc_search_selector)
            if clicked in ('clicked', 'no-button', 'no-input'):
                break
            page.wait_for_timeout(400)   # 'disabled' -> wait for the pick to enable it, retry
        page.wait_for_timeout(800)
        if _location_in_ownership_table(page, location_id):
            print(f"[ACTION] Added location {location_id} to Card Ownership.")
        else:
            print(f"[INFO] Add Location ({clicked}) -- {location_id} not in the table yet; "
                  "verify in Card Ownership.")
    except Exception as e:
        print(f"[INFO] Couldn't auto-add location ({e}) -- select {location_id} + click "
              "Add Location manually.")


def read_so_dealer_id(page: Page) -> str:
    """The dealer's MOOPS customer_id from the SO's Customer field link (top of the SO). The
    dealer is the account the SO was placed under, and its customer record holds the End
    Customer associations (a dealer can only order for end customers on their record). Returns
    '' if no /customer?customer_id= link is found on the page."""
    try:
        return page.evaluate(r"""() => {
            for (const a of document.querySelectorAll('a[href*="customer_id="]')) {
                const m = (a.getAttribute('href') || '').match(/customer(?:\.php)?\?customer_id=(\d+)/);
                if (m) return m[1];
            }
            return '';
        }""") or ""
    except Exception:
        return ""


def fill_dealer_end_customer_association(page: Page, dealer_customer_id, cust_id: str) -> bool:
    """On the DEALER's MOOPS customer page, add `cust_id` as an End Customer association so it
    becomes selectable as the SO's End Customer. The End-Customer search
    (#validity_customer-search) is a finicky typeahead: fill it, pick the row, click the
    'Add Customer' button next to it (just picking does NOT add it), THEN Save (#change).
    Auto-Adds + auto-Saves, then VERIFIES on the reloaded page. Returns True only if the
    association actually committed (cust_id present on the reloaded dealer record)."""
    url = f"{MOOPS_BASE}/customer?customer_id={dealer_customer_id}"
    print(f"[ASSOC] Dealer account: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    search = page.locator('#validity_customer-search')
    if search.count() == 0:
        print("[ASSOC] End-Customer search (#validity_customer-search) not on the dealer page "
              "-- add the association manually.")
        return False
    try:
        search.first.click()
        search.first.fill(cust_id)
    except Exception as e:
        print(f"[ASSOC] Could not fill the End-Customer search ({e}) -- add manually.")
        return False
    if not _pick_validity_result(page, cust_id):
        print(f"[ASSOC] Could not pick {cust_id} in the End-Customer search -- add it manually.")
        return False
    # Click 'Add Customer' to add the picked result to the associations list. Target it by its
    # STABLE CLASS (button.btn.btn-primary.mr-sm-2) -- the button has no text node, so matching by
    # the words "Add Customer" never found it (confirmed live). Picking alone does NOT add the row;
    # the Add button must fire and the row must appear BEFORE the save.
    add_clicked = page.evaluate("""() => {
        const btn = document.querySelector('button.btn.btn-primary.mr-sm-2');
        if (btn) { btn.click(); return true; }
        return false;
    }""")
    if not add_clicked:
        print("[ASSOC] Add Customer button (button.btn.btn-primary.mr-sm-2) not found -- add manually.")
        return False

    # Confirm the row actually landed in the associations table before saving. The Add is a
    # client-side Angular insert; saving before it commits loses it.
    added_row = False
    for _ in range(6):
        added_row = page.evaluate(
            "(cid) => [...document.querySelectorAll('#customer_details_form table tbody tr')]"
            ".some(r => (r.textContent || '').includes(cid))",
            cust_id,
        )
        if added_row:
            break
        page.wait_for_timeout(500)
    if not added_row:
        print(f"[ASSOC] 'Add Customer' did not add a row for {cust_id} -- not saving; add it manually.")
        return False
    print(f"[ASSOC] {cust_id} added to the associations table -- saving (#change).")

    # Record it: click the green Save (#change) automatically -- no human pause. The page
    # reloads on save, so we sleep rather than wait_for_timeout (context is destroyed).
    page.evaluate("""() => {
        const byId = document.querySelector('#change');
        if (byId) { byId.click(); return true; }
        const els = document.querySelectorAll('button, a, input[type="submit"]');
        for (const el of els) {
            const txt = (el.textContent || el.value || '').trim();
            if (txt === 'Save' || txt.startsWith('Save')) { el.click(); return true; }
        }
        return false;
    }""")
    time.sleep(3)

    # Verify on the RELOADED dealer page (the only trustworthy signal). cust_id is brand-new under
    # this dealer, so if it shows anywhere on the reloaded record the association committed; if not,
    # the Add didn't register -- report that truthfully instead of a false "Saved".
    try:
        present = page.evaluate("(cid) => (document.body ? document.body.innerText : '').includes(cid)", cust_id)
    except Exception:
        present = False
    if present:
        print(f"[ASSOC] Confirmed -- {cust_id} is recorded on dealer {dealer_customer_id}'s End Customer associations.")
        return True
    print(f"[ASSOC] {cust_id} did NOT land on the dealer's associations after Add + Save -- add it "
          "manually (pick -> Add Customer -> Save) on the dealer record, then re-run.")
    return False


@timed
def set_so_end_customer(page: Page, cust_id: str, location_id: str = "", save: bool = False) -> bool:
    """
    Set the End Customer (Customer ID + Location) on the SO page.

    End-Customer is a search-select (`#validity_customer-search`): type the id, click the
    matching row. Location (Card Ownership) is a search-select
    (`[id^="validity_$location_filter"]`, enabled once a customer is chosen) PLUS an
    "Add Location" button -- picking the row alone doesn't stick; you must click
    "Add Location" to commit it to the ownership table. Idempotent: skips if the
    location is already listed.

    save=False (default): FILL-ONLY -- pauses for the human to verify and Save.
    save=True: after setting cust id + location, SAVE the SO automatically (no pause) --
    per Matt, the End-Customer + location should just save, not wait.
    Returns True if the customer ended up populated.
    """
    print(f"[ACTION] Setting End Customer {cust_id}" + (f" / location {location_id}" if location_id else ""))
    cust_search = page.locator('#validity_customer-search')
    if cust_search.count() == 0:
        print("[WARNING] End Customer search (#validity_customer-search) not on this page "
              "-- set it manually (Customer ID + Location) and save.")
        return False

    # Customer -- skip if already showing this id (the SOR can pre-populate it).
    try:
        already = (cust_search.first.input_value() or "")
    except Exception:
        already = ""
    customer_matched = cust_id in already
    if customer_matched:
        print(f"[INFO] Customer already set ('{already}') -- leaving as is.")
    else:
        try:
            cust_search.first.click()
            cust_search.first.fill(cust_id)
            customer_matched = _pick_validity_result(page, cust_id)
        except Exception as e:
            print(f"[WARNING] Customer auto-select unreliable ({e}) -- set it manually.")

    # If the customer doesn't appear in the End Customer search, it's almost always because a
    # NEW customer isn't tied to the dealer account yet. Do NOT force a save -- that would
    # clear the cust id (Location-empty blocker) and lose the link. Flag the dealer step.
    if not customer_matched:
        print(f"[ACTION] Customer {cust_id} not selectable in End Customer -- it isn't tied to "
              "the dealer account yet.")
        print(f"[FLAG] Add {cust_id} to the dealer record first, then set the End Customer + "
              "Location on the SO and save. Skipping the auto-save (won't clear the cust id).")
        return False

    # Location (Card Ownership) -- pick the row, click "Add Location" to commit it, verify.
    # Shared with the card-part page; the SO page's location search differs by selector.
    _commit_ownership_location(page, location_id, '[id^="validity_$location_filter"]')

    if save:
        print(f"[ACTION] End Customer = {cust_id}"
              + (f" / location {location_id}" if location_id else "") + " -- saving SO...")
        # We just DELIBERATELY set the End Customer + location, so do NOT run the
        # clear-customer-blocker here. That check is only for a leftover SOR cust id with no
        # location; running it now can wipe the link we just made or, via its loose label
        # matching, touch the wrong field (e.g. the uninvoiced field). Save as-is.
        save_so(page, accept_sor=False, clear_customer_location_blocker=False)
        return True

    print(f"[PAUSE] Verify End Customer = {cust_id}"
          + (f" and Location = {location_id}" if location_id else "")
          + ", then SAVE the SO. Press Enter when done.")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        print("\n[INFO] Continuing without confirming End Customer save.")
    return True
