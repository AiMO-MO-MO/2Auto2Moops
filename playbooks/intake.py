"""
Batch intake -- Phase 1 (read-only).

Ad-hoc command: scan the MOOPS order-request queue (the "Submitted/In Review"
section only), read each SOR, and produce an easy-to-scan board of everything
waiting, with the important info per order type.

READ-ONLY. Never writes to MOOPS. Outputs (see DEDUPE_RUNBOOK.md):
  - dedupe_data.js      (queue + live Admin/LW dedupe; read by the static dedupe_board.html)
  - dedupe_keys.json    (tiny contact-key list the Claude SF step queries Salesforce on)
  - intake_plan.json    (machine-readable, for later Phase 2 execution)
  - opens dedupe_board.html (the static shell you actually look at; SF fills in after the SF step)

Key facts about the queue page (validated from the live DOM):
  - /order-requests has several sections, each an <h5> heading + a <table>:
    "Submitted/In Review", "Awaiting Update", etc. Only the first is actionable.
  - Submitted/In Review rows have an EMPTY "Linked SO" column -- these orders have
    no SO yet, so all data comes from the SOR detail page, not an SO page.
  - The table's "Type" column classifies the order directly (System / Route /
    Cards / Parts), so classification needs no navigation.
  - EXPEDITED shows inline in the Request # cell.

Per-SOR enrichment (required delivery date, card design type, processor, contact,
comments) is read from each SOR detail page (/order-requests/<id>) using the same
selectors proven in core.moops.read_sor_data, plus a best-effort comments read.

Not in v1 (next slices): SOR line-item parsing (VAC weight, card qty), customer
dedup, rules engine. v1 surfaces flags rather than a READY/BLOCK verdict.
"""

import html
import json
import os
import pathlib
import re
import time
import webbrowser
from datetime import datetime

from core.browser import MOOPS_BASE
from core.schedule import calculate_order_weight, pick_assembly_week
from core.moops import read_schedule_capacity
from core.portal import scrape_admin_customers
from core import dedup
from core import reader_kits

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE_URL = f"{MOOPS_BASE}/order-requests"
ACTIONABLE_SECTION = "submitted"  # matches "Submitted/In Review" (case-insensitive)


# ---------------------------------------------------------------------------
# Queue scrape (Submitted/In Review section only)
# ---------------------------------------------------------------------------

def _open_queue(page):
    """Navigate to the queue page and wait for the Angular table to render."""
    print(f"\n[NAV] Queue: {QUEUE_URL}")
    page.goto(QUEUE_URL, wait_until="domcontentloaded", timeout=30000)
    # Wait for the ACTUAL Submitted/In Review heading to mount -- not just any <h5>
    # (the modernizr "Your browser is not supported" alert is always present and
    # would otherwise satisfy a generic h5 check before Angular renders the table).
    try:
        page.wait_for_function(
            """() => {
                if (document.body.textContent.includes('{{')) return false;
                return Array.from(document.querySelectorAll('h5'))
                  .some(h => (h.innerText||'').trim().toLowerCase().startsWith('submitted'));
            }""",
            timeout=20000,
        )
    except Exception:
        print("[WARN] Queue render wait timed out -- reading whatever is present")
        page.wait_for_timeout(3000)


def read_sor_queue(page):
    """
    Parse the Submitted/In Review table. Returns a list of row dicts:
        {"sor_id", "sor_no", "sor_url", "dealer", "type_label",
         "submitted_on", "linked_so", "po_number", "description", "expedited"}
    """
    _open_queue(page)

    data = page.evaluate(
        """
        (sectionKey) => {
          const h5s = Array.from(document.querySelectorAll('h5'));
          const target = h5s.find(h => (h.innerText||'').trim().toLowerCase().startsWith(sectionKey));
          if (!target) return {found:false, headings: h5s.map(h => (h.innerText||'').trim())};
          let el = target.nextElementSibling;
          while (el && el.tagName !== 'TABLE') el = el.nextElementSibling;
          if (!el) return {found:false, headings: h5s.map(h => (h.innerText||'').trim())};
          const headers = Array.from(el.querySelectorAll('thead th, tr:first-child th'))
            .map(x => (x.innerText||'').trim());
          let rows = Array.from(el.querySelectorAll('tbody tr'));
          if (!rows.length) rows = Array.from(el.querySelectorAll('tr')).slice(1);
          const parsed = rows.map(r => {
            const cells = Array.from(r.querySelectorAll('td,th')).map(c => (c.innerText||'').trim());
            const links = Array.from(r.querySelectorAll('a')).map(a => a.getAttribute('href')||'');
            const sor = links.find(h => /\\/order-requests\\/\\d+/.test(h)) || '';
            return {cells, sor};
          });
          return {found:true, headers, rows: parsed};
        }
        """,
        ACTIONABLE_SECTION,
    )

    if not data.get("found"):
        print("\n[DIAGNOSTIC] Could not find the 'Submitted/In Review' table.")
        print(f"  Section headings seen: {data.get('headings')}")
        print("  -> Paste this back so the section match can be adjusted.\n")
        return []

    headers = [h.lower() for h in data.get("headers", [])]

    def col(*names):
        for i, h in enumerate(headers):
            if any(n in h for n in names):
                return i
        return -1

    idx = {
        "request": col("request"),
        "dealer": col("dealer"),
        "type": col("type"),
        "submitted": col("submitted"),
        "linked": col("linked"),
        "po": col("po "),
        "description": col("description"),
    }

    def cell(cells, i):
        return cells[i].strip() if 0 <= i < len(cells) else ""

    out = []
    for r in data["rows"]:
        cells = r.get("cells", [])
        req_cell = cell(cells, idx["request"])
        sor_href = r.get("sor", "")
        m = re.search(r"/order-requests/(\d+)", sor_href)
        sor_id = m.group(1) if m else ""
        sno = re.search(r"(SOR-\d+)", req_cell)
        sor_no = sno.group(1) if sno else (f"SOR-{sor_id}" if sor_id else req_cell.split("\n")[0])
        if not sor_id and not sor_no:
            continue
        sor_url = (sor_href if sor_href.startswith("http")
                   else f"{MOOPS_BASE}{sor_href}") if sor_href else ""
        out.append({
            "sor_id": sor_id,
            "sor_no": sor_no,
            "sor_url": sor_url,
            "dealer": cell(cells, idx["dealer"]),
            "type_label": cell(cells, idx["type"]),
            "submitted_on": cell(cells, idx["submitted"]),
            "linked_so": cell(cells, idx["linked"]),
            "po_number": cell(cells, idx["po"]),
            "description": cell(cells, idx["description"]),
            "expedited": "EXPEDITED" in req_cell.upper(),
        })

    print(f"[READ] Submitted/In Review: {len(out)} order(s)")
    return out


# ---------------------------------------------------------------------------
# Classification (from the queue Type column -- no navigation needed)
# ---------------------------------------------------------------------------

def classify_from_type(type_label):
    t = (type_label or "").lower()
    if "route" in t or "multi-family" in t or "multi family" in t:
        return "Route"
    if "system" in t or "laundromat" in t:
        return "System"
    if "card" in t:
        return "Cards"
    if "part" in t or "reader" in t:
        return "Parts"
    return "Unknown"


def card_workflow(design_type):
    d = (design_type or "").lower()
    if not d:
        return "none"
    if "generic" in d:
        return "generic"
    if any(k in d for k in ("reprint", "re-print", "existing", "reorder")):
        return "reprint"
    if any(k in d for k in ("modify", "change")):
        return "modify"
    if any(k in d for k in ("new", "design")):
        return "new_design"
    return "unknown"


# ---------------------------------------------------------------------------
# SOR detail read (mirrors core.moops.read_sor_data selectors + comments)
# ---------------------------------------------------------------------------

def parse_required_date(raw):
    """From the Required Delivery Date raw value -> (iso_date, is_expedited)."""
    if not raw:
        return "", False
    exp = "EXPEDITED" in raw.upper()
    mt = re.search(r'\((\w+ \d+,\s*\d{4})\)', raw)
    if mt:
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(mt.group(1), fmt).strftime("%Y-%m-%d"), exp
            except ValueError:
                continue
    return "", exp


def read_sor_detail(page, sor_url):
    """Navigate to a SOR detail page and read the fields intake needs.

    One page.evaluate pulls every label->value pair, an attachment count, and any
    CARD-* part tokens. Field labels are the real ones from the live SOR DOM:
    Required Delivery Date, Design type, Comment (singular), Shipping Company Name /
    Address / Contact / Phone #.
    """
    print(f"[NAV] SOR: {sor_url}")
    page.goto(sor_url, wait_until="domcontentloaded", timeout=20000)
    try:
        page.wait_for_function("() => !document.body.textContent.includes('{{')", timeout=15000)
    except Exception:
        page.wait_for_timeout(2000)

    data = page.evaluate(
        """
        () => {
          const fields = {};
          document.querySelectorAll('label').forEach(l => {
            const lab = (l.innerText||'').trim();
            if (!lab || (lab in fields)) return;
            const p = l.parentElement;
            let val = '';
            if (p) {
              const v = p.querySelector('span.col-9, .col-9');
              val = v ? (v.innerText||'').trim() : (p.innerText||'').replace(lab,'').trim();
            }
            fields[lab] = val;
          });
          let att = 0;
          document.querySelectorAll('a,img').forEach(a => {
            const t=(a.innerText||a.getAttribute('alt')||'').trim();
            const h=a.getAttribute('href')||a.getAttribute('src')||'';
            if (/\\.(pdf|png|jpe?g|gif|ai|eps|svg|zip|docx?)($|\\?)/i.test(h) ||
                /attach|upload|artwork|proof|\\blogo\\b/i.test(t)) att++;
          });
          const cards = document.body.innerText.match(/CARD-[A-Z0-9]+(?:-[A-Z0-9]+)*/g) || [];
          // VAC line items from the "Value Add Centers Kiosks" table (Part #, Quantity)
          let vacs = [];
          const vh = Array.from(document.querySelectorAll('h3,h4,h5'))
            .find(h => /value add centers/i.test(h.innerText||''));
          if (vh) {
            let el = vh.nextElementSibling, t = null, g = 0;
            while (el && g < 12) {
              if (el.tagName === 'TABLE') { t = el; break; }
              const inner = el.querySelector && el.querySelector('table');
              if (inner) { t = inner; break; }
              el = el.nextElementSibling; g++;
            }
            if (t) {
              const trs = Array.from(t.querySelectorAll('tr'));
              const heads = trs.length ? Array.from(trs[0].querySelectorAll('th,td'))
                .map(x => (x.innerText||'').trim().toLowerCase()) : [];
              let qi = heads.findIndex(h => h.indexOf('quantity') >= 0);
              let pi = heads.findIndex(h => h.indexOf('part') >= 0); if (pi < 0) pi = 0;
              let rows = Array.from(t.querySelectorAll('tbody tr'));
              if (!rows.length) rows = trs.slice(1);
              rows.forEach(r => {
                const cells = Array.from(r.querySelectorAll('td,th')).map(c => (c.innerText||'').trim());
                const pn = (cells[pi] || '').split(/\\s/)[0];
                if (/^VAC/i.test(pn)) {
                  let q = qi >= 0 ? (cells[qi] || '') : '';
                  q = (q.match(/\\d+/) || [''])[0];
                  vacs.push({part_number: pn, qty: parseInt(q || '0', 10) || 0});
                }
              });
            }
          }
          // Reader count: sum the Quantity column of the "Reader" section table (best-effort;
          // section/column naming may vary -- verify against an `inspect <sor>` dump).
          let reader_count = 0;
          const rh = Array.from(document.querySelectorAll('h3,h4,h5'))
            .find(h => /reader/i.test(h.innerText||''));
          if (rh) {
            let el = rh.nextElementSibling, rt = null, g = 0;
            while (el && g < 12) {
              if (el.tagName === 'TABLE') { rt = el; break; }
              const inner = el.querySelector && el.querySelector('table');
              if (inner) { rt = inner; break; }
              el = el.nextElementSibling; g++;
            }
            if (rt) {
              const trs = Array.from(rt.querySelectorAll('tr'));
              const heads = trs.length ? Array.from(trs[0].querySelectorAll('th,td'))
                .map(x => (x.innerText||'').trim().toLowerCase()) : [];
              let qi = heads.findIndex(h => h.indexOf('quantity') >= 0 || h.indexOf('qty') >= 0);
              let rows = Array.from(rt.querySelectorAll('tbody tr'));
              if (!rows.length) rows = trs.slice(1);
              rows.forEach(r => {
                const cells = Array.from(r.querySelectorAll('td,th')).map(c => (c.innerText||'').trim());
                let q = qi >= 0 ? (cells[qi] || '') : '';
                q = (q.match(/\\d+/) || [''])[0];
                reader_count += parseInt(q || '0', 10) || 0;
              });
            }
          }
          return {fields, att, cards, vacs, reader_count};
        }
        """
    )
    f = data.get("fields", {})

    req_date, expedited = parse_required_date(f.get("Required Delivery Date", ""))

    processor = ""
    for k, v in f.items():
        if "processor" in k.lower() or "ebt" in k.lower():
            processor = v
            break

    ship = {
        "company": f.get("Shipping Company Name", "").strip(),
        "address": f.get("Shipping Address", "").strip(),
        "contact": f.get("Shipping Contact", "").strip(),
        "phone": (f.get("Shipping Phone #", "") or f.get("Shipping Phone", "")).strip(),
    }
    has_zip = bool(re.search(r'\b\d{5}\b', ship["address"]))
    shipping_ok = all(ship.values()) and has_zip

    card_part = ""
    for c in data.get("cards", []):
        if c.upper().startswith("CARD-MD"):
            card_part = c
            break

    # "Existing End Customer" on the SOR -- a human-asserted link to an existing
    # account (e.g. "Sudz Coin Laundromat (01892)"). Authoritative dedup signal.
    existing_raw = f.get("Existing End Customer", "").strip()
    m_ex = re.search(r'\((\d+)\)', existing_raw)
    existing_customer_id = m_ex.group(1) if m_ex else ""
    existing_customer_name = re.sub(r'\s*\(\d+\).*$', '', existing_raw).strip()

    return {
        "processor_type": processor,
        "existing_customer_id": existing_customer_id,
        "existing_customer_name": existing_customer_name,
        "required_date": req_date,
        "required_date_raw": f.get("Required Delivery Date", ""),
        "is_expedited": expedited,
        "card_design_type": f.get("Design type", ""),
        # End-customer/operator contact first (for dedup + create-customer), dealer shipping as fallback
        "contact_name": f.get("New Contact Name", "") or f.get("Shipping Contact", ""),
        "contact_email": f.get("New Contact Email", ""),
        "contact_phone": (f.get("New Contact Phone", "") or f.get("Shipping Phone #", "")).strip(),
        "location_name": f.get("Location Name", ""),
        "location_address": (f.get("Location Address", "")
                             or ship["address"].replace("\n", ", ")),
        "comments": (f.get("Comment", "") or f.get("Comments", "")).strip(),
        "shipping": ship,
        "shipping_ok": shipping_ok,
        "has_attachments": data.get("att", 0) > 0,
        "card_part": card_part,
        "vacs": data.get("vacs", []),
        "reader_count": int(data.get("reader_count", 0) or 0),
    }


def inspect_sor(page, sor_id):
    """
    Diagnostic dump of one SOR detail page: every field label + value, section
    headings, and attachment-like links. Used to find the exact selectors for
    shipping fields and attachments before wiring them into the board.
    """
    url = f"{MOOPS_BASE}/order-requests/{sor_id}"
    print(f"\n[NAV] SOR detail: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    try:
        page.wait_for_function("() => !document.body.textContent.includes('{{')", timeout=15000)
    except Exception:
        page.wait_for_timeout(2000)

    dump = page.evaluate(
        """
        () => {
          const out = {fields: [], headings: [], attachments: []};
          document.querySelectorAll('label').forEach(l => {
            const lab = (l.innerText||'').trim();
            if (!lab) return;
            let val = '';
            const p = l.parentElement;
            if (p) {
              const v = p.querySelector('span.col-9, .col-9, span.font-weight-bold');
              val = v ? (v.innerText||'').trim() : (p.innerText||'').replace(lab,'').trim();
            }
            out.fields.push({label: lab, value: val.slice(0,140)});
          });
          document.querySelectorAll('h3,h4,h5,h6,.card-header,legend').forEach(h => {
            const t=(h.innerText||'').trim(); if (t && t.length<70) out.headings.push(t);
          });
          document.querySelectorAll('a,img,button').forEach(a => {
            const t=(a.innerText||a.getAttribute('alt')||'').trim();
            const h=a.getAttribute('href')||a.getAttribute('src')||'';
            if (/\\.(pdf|png|jpe?g|gif|ai|eps|svg|zip|docx?)($|\\?)/i.test(h) ||
                /attach|upload|file|logo|artwork|proof|download/i.test(t)) {
              out.attachments.push({text: t.slice(0,70), ref: h.slice(0,140)});
            }
          });
          out.sections = [];
          document.querySelectorAll('h3,h4,h5').forEach(h => {
            const title=(h.innerText||'').trim();
            if(!title || title.length>70) return;
            let txt=''; let el=h.nextElementSibling; let g=0;
            while(el && !/^H[3-5]$/.test(el.tagName) && g<60){
              if(el.querySelectorAll){
                el.querySelectorAll('select').forEach(s=>{const o=s.options[s.selectedIndex]; if(o&&(o.text||'').trim())txt+=' {'+o.text.trim()+'}';});
                el.querySelectorAll('input').forEach(i=>{const v=(i.value||'').trim(); if(v)txt+=' <'+v+'>';});
              }
              txt += ' ' + (el.innerText||'').replace(/\\s+/g,' ').trim();
              el = el.nextElementSibling; g++;
            }
            txt = txt.replace(/\\s+/g,' ').trim();
            if(txt) out.sections.push({title, text: txt.slice(0,400)});
          });
          return out;
        }
        """
    )
    print("\n=== SOR FIELDS (label: value) ===")
    for f in dump["fields"]:
        print(f"  {f['label']!r}: {f['value']!r}")
    print("\n=== SECTION HEADINGS ===")
    for h in dict.fromkeys(dump["headings"]):
        print(f"  {h}")
    print("\n=== SECTION CONTENT (VACs / reader kits / parts) ===")
    for s in dump.get("sections", []):
        if any(k in s["title"] for k in ("Value Add", "Reader", "Other Parts", "Laundry Cards")):
            print(f"\n[{s['title']}]\n  {s['text']}")
    print("\n=== ATTACHMENT-LIKE ELEMENTS ===")
    if dump["attachments"]:
        for a in dump["attachments"]:
            print(f"  {a['text']!r} -> {a['ref']}")
    else:
        print("  (none detected)")
    print("\n  -> Paste this back so VAC weight (scheduling) + dedup fields can be wired.")


def analyze_sor(page, row):
    """Enrich a queue row with SOR detail data and classification."""
    classification = classify_from_type(row["type_label"])
    t = time.time()
    detail = read_sor_detail(page, row["sor_url"]) if row["sor_url"] else {}
    print(f"  [sor detail {time.time() - t:.1f}s]")

    design = detail.get("card_design_type", "")
    workflow = card_workflow(design) if classification in ("System", "Route", "Cards") else "none"
    expedited = bool(row.get("expedited") or detail.get("is_expedited"))
    comments = detail.get("comments", "").strip()

    # Reader kits: read the Card Reader Kits table (page is still on the SOR) and
    # scrape candidate model tokens from the comments. Batch resolution of the
    # MISSING ones happens once per run in run() -> reader_kits.resolve_models.
    reader_table = (reader_kits.extract_reader_table(page)
                    if classification in ("System", "Route")
                    else {"install_type": "", "machines": []})
    comment_models = reader_kits.extract_comment_models(comments)

    flags = []
    if expedited:
        flags.append("Expedited")
    if comments:
        flags.append("Comments")
    if classification == "Unknown":
        flags.append("Unclassified")
    # Flag only a genuine gap: blank or "not selected". A lead-time / shipping option
    # ("Ground", "2-3 Days", "Normal lead-time") IS the delivery requirement -- not a gap.
    raw_dd = (detail.get("required_date_raw") or "").strip().lower()
    if not detail.get("required_date") and raw_dd in ("", "not selected"):
        flags.append("No delivery date")

    shipping_ok = detail.get("shipping_ok")
    has_attachments = detail.get("has_attachments")
    card_part = detail.get("card_part", "")

    vacs = detail.get("vacs", [])
    weight = calculate_order_weight(vacs) if classification in ("System", "Route") else 0.0
    vac_count = sum(int(v.get("qty", 0) or 0) for v in vacs)
    reader_count = detail.get("reader_count", 0)

    # Card readiness gates
    if classification == "Cards":
        if shipping_ok is False:
            flags.append("Shipping incomplete")
        if workflow == "new_design" and has_attachments is False:
            flags.append("No artwork")

    return {
        "sor_id": row["sor_id"],
        "sor_no": row["sor_no"],
        "sor_url": row["sor_url"],
        "classification": classification,
        "type_label": row["type_label"],
        "dealer": row["dealer"],
        "description": row["description"],
        "submitted_on": row["submitted_on"],
        "po_number": row["po_number"],
        "required_date": detail.get("required_date", ""),
        "required_date_raw": detail.get("required_date_raw", ""),
        "is_expedited": expedited,
        "card_design_type": design,
        "card_workflow": workflow,
        "card_part": card_part,
        "processor_type": detail.get("processor_type", ""),
        "existing_customer_id": detail.get("existing_customer_id", ""),
        "existing_customer_name": detail.get("existing_customer_name", ""),
        "contact_name": detail.get("contact_name", ""),
        "contact_email": detail.get("contact_email", ""),
        "contact_phone": detail.get("contact_phone", ""),
        "location_name": detail.get("location_name", ""),
        "location_address": detail.get("location_address", ""),
        "comments": comments,
        "shipping_ok": shipping_ok,
        "has_attachments": has_attachments,
        "vacs": vacs,
        "vac_count": vac_count,
        "reader_count": reader_count,
        "reader_table": reader_table,
        "comment_models": comment_models,
        "reader_kits": None,  # filled by batch resolution in run()
        "weight": weight,
        "assembly_week": "",
        "assembly_week_date": "",
        "flags": flags,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Board rendering (standalone HTML file)
# ---------------------------------------------------------------------------

def _details(o):
    """Type-aware (label, value) list -- what's important varies by order type."""
    # No PO, no submitted date (per Matt). Card workflow shown clean (no parenthetical),
    # with the card part number next to it for reprint/modify once wired from the SOR.
    t = o["classification"]
    rd = o["required_date_raw"] or o["required_date"] or "-"
    rows = [("Dealer", o["dealer"] or "-")]

    # Lead with the terminal-readout numbers for system/route: VAC count, reader count,
    # required date, proposed schedule.
    if t in ("System", "Route"):
        vac_bd = ", ".join(f'{v["part_number"]} x{v["qty"]}' for v in o.get("vacs", []))
        rows.append(("VAC count",
                     f'{o.get("vac_count", 0)}' + (f'  ({vac_bd})' if vac_bd else '')))
        rc = o.get("reader_count", 0)
        rows.append(("Reader count", str(rc) if rc else "-"))
        rows.append(("Required date", rd))
        wk = o.get("assembly_week") or "(unscheduled)"
        rows.append(("Proposed schedule", f'{wk}  ({o.get("weight", 0):.1f} wt)'))
    else:
        rows.append(("Required date", rd))

    if o["card_workflow"] != "none" and t in ("System", "Route", "Cards"):
        label = _WF_LABEL.get(o["card_workflow"], o["card_workflow"])
        part = o.get("card_part", "")
        rows.append(("Card", f"{label}  {part}".strip() if part else label))

    if t == "Cards":
        if o.get("shipping_ok") is not None:
            rows.append(("Shipping fields", "Yes" if o["shipping_ok"] else "No"))
        if o.get("has_attachments") is not None:
            rows.append(("Attachments", "Yes" if o["has_attachments"] else "No"))
    if t == "System" and o["processor_type"]:
        rows.append(("Processor", o["processor_type"]))
    if t in ("System", "Route") and o["location_address"]:
        rows.append(("Location", o["location_address"]))
    return rows


_TYPE_COLOR = {
    "System": "#185FA5", "Route": "#0F6E56",
    "Cards": "#534AB7", "Parts": "#854F0B", "Unknown": "#A32D2D",
}

_WF_LABEL = {
    "new_design": "New design", "reprint": "Reprint",
    "modify": "Modify", "generic": "Generic", "unknown": "Card (type?)",
}


def build_board_html(orders, generated_at):
    counts = {}
    for o in orders:
        counts[o["classification"]] = counts.get(o["classification"], 0) + 1
    summary = " &middot; ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items()))

    cc_counts = {"existing": 0, "potential": 0, "new": 0}
    for o in orders:
        cc = o.get("customer_check")
        if cc:
            cc_counts[cc["verdict"]] = cc_counts.get(cc["verdict"], 0) + 1
    cust_summary = ""
    if any(cc_counts.values()):
        cust_summary = (f' &middot; customers: {cc_counts["new"]} new, '
                        f'{cc_counts["potential"]} possible, {cc_counts["existing"]} existing')

    cards = []
    for o in orders:
        accent = "#A32D2D" if o["classification"] == "Unknown" else (
            "#BA7517" if o["is_expedited"] else "#D3D1C7")
        type_c = _TYPE_COLOR.get(o["classification"], "#5F5E5A")
        title = o["description"] or o["dealer"] or o["sor_no"]

        det = "".join(
            f'<tr><td class="lbl">{html.escape(l)}</td>'
            f'<td class="val">{html.escape(str(v))}</td></tr>'
            for l, v in _details(o)
        )
        flags = "".join(f'<span class="flag">{html.escape(f)}</span>' for f in o["flags"])

        # Customer dedup verdict -- sleek on-brand status pill (System orders only), plus a
        # list of the Admin /customers candidates and WHAT each matched on (signal + value).
        cust_badge = ""
        cc = o.get("customer_check")
        if cc:
            _kls = {"existing": "cust-existing", "potential": "cust-potential",
                    "new": "cust-new"}.get(cc["verdict"], "cust-new")
            _lbl = {"existing": "Existing customer", "potential": "Possible match",
                    "new": "New customer"}.get(cc["verdict"], cc["verdict"])
            _sig_lbl = {"email": "email", "phone": "phone", "last_name": "last name",
                        "name": "business name", "sor_assigned": "named on SOR"}
            match_rows = ""
            for m in cc.get("matches", [])[:6]:
                sig = _sig_lbl.get(m.get("signal", ""), m.get("signal", "") or "—")
                det = m.get("detail", "")
                on = f'{sig}: {det}' if det else sig
                strg = m.get("strength", "")
                contact = " · ".join(x for x in (m.get("contact_name", ""),
                                                 m.get("contact_email", ""),
                                                 m.get("contact_phone", "")) if x)
                match_rows += (
                    f'<div class="match">'
                    f'<span class="m-strength m-{html.escape(strg) or "asserted"}">'
                    f'{html.escape(strg or "asserted")}</span>'
                    f'<span class="m-id">{html.escape(m.get("cust_id", ""))}</span>'
                    f'<span class="m-name">{html.escape(m.get("name", ""))}</span>'
                    f'<span class="m-on">matched {html.escape(on)}</span>'
                    + (f'<span class="m-contact">{html.escape(contact)}</span>' if contact else "")
                    + f'</div>')
            matches_block = f'<div class="matches">{match_rows}</div>' if match_rows else ""
            # This order's own name + contact, shown directly above the candidates so the
            # email/phone/name comparison is line-up easy.
            o_name = o.get("location_name", "") or o.get("dealer", "") or title
            o_contact = " · ".join(x for x in (o.get("contact_name", ""),
                                               o.get("contact_email", ""),
                                               o.get("contact_phone", "")) if x)
            order_block = (
                f'<div class="orderinfo">'
                f'<span class="oi-label">This order</span>'
                f'<span class="oi-name">{html.escape(o_name)}</span>'
                + (f'<span class="oi-contact">{html.escape(o_contact)}</span>' if o_contact
                   else '<span class="oi-contact oi-none">no contact on SOR</span>')
                + '</div>')
            cust_badge = (f'<div class="custrow">'
                          f'<span class="cust-badge {_kls}">{html.escape(_lbl)}</span>'
                          f'</div>{order_block}{matches_block}')

        comment_block = ""
        if o["comments"]:
            comment_block = (
                f'<div class="cmt"><span class="cmt-l">SOR comments</span>'
                f'{html.escape(o["comments"])}</div>'
            )
        link = (f'<a href="{html.escape(o["sor_url"])}">{html.escape(o["sor_no"])}</a>'
                if o["sor_url"] else html.escape(o["sor_no"]))
        cards.append(
            f'<div class="card" style="border-left:4px solid {accent}">'
            f'<div class="hd">'
            f'<span class="type" style="background:{type_c}1a;color:{type_c}">'
            f'{html.escape(o["classification"])}</span>'
            f'<span class="cust">{html.escape(title)}</span>'
            f'<span class="links">{link}</span></div>'
            f'<table class="det">{det}</table>'
            f'{cust_badge}'
            f'<div class="flags">{flags}</div>'
            f'{comment_block}</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Intake board</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; color:#2C2C2A;
         background:#F1EFE8; margin:0; padding:24px; }}
  .wrap {{ max-width:860px; margin:0 auto; }}
  h1 {{ font-size:22px; font-weight:500; margin:0 0 2px; }}
  .sub {{ color:#5F5E5A; font-size:13px; margin-bottom:18px; font-variant-numeric:tabular-nums; }}
  .card {{ background:#fff; border:0.5px solid #D3D1C7; border-radius:12px;
           padding:14px 16px; margin-bottom:10px; }}
  .hd {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; flex-wrap:wrap; }}
  .type {{ font-size:12px; font-weight:500; padding:2px 9px; border-radius:8px; }}
  .cust {{ font-weight:500; font-size:15px; }}
  .links {{ margin-left:auto; display:flex; gap:10px; }}
  .links a {{ color:#185FA5; text-decoration:none; font-size:12px;
              font-family:ui-monospace, Menlo, monospace; }}
  table.det {{ width:100%; border-collapse:collapse; font-size:13px; }}
  table.det td {{ padding:3px 0; vertical-align:top; }}
  td.lbl {{ color:#5F5E5A; width:150px; }}
  td.val {{ color:#2C2C2A; }}
  .flags {{ margin-top:8px; display:flex; gap:6px; flex-wrap:wrap; }}
  .flag {{ font-size:11px; color:#5F5E5A; background:#F1EFE8; border:0.5px solid #D3D1C7;
           padding:2px 8px; border-radius:8px; }}
  .custrow {{ margin-top:10px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
  .cust-badge {{ font-size:11px; font-weight:600; padding:3px 11px; border-radius:20px;
                 letter-spacing:.02em; }}
  .cust-existing {{ background:#EAF3DE; color:#3B6D11; }}
  .cust-potential {{ background:#FAEEDA; color:#854F0B; }}
  .cust-new {{ background:#E6F1FB; color:#185FA5; }}
  .cust-ids {{ font-family:ui-monospace, Menlo, monospace; font-size:12px; color:#5F5E5A; }}
  .orderinfo {{ margin-top:8px; display:flex; align-items:baseline; gap:8px; flex-wrap:wrap;
                padding:6px 9px; background:#EDF3FB; border:0.5px solid #CFE0F2; border-radius:8px; }}
  .oi-label {{ font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:.04em;
               color:#185FA5; }}
  .oi-name {{ font-weight:600; color:#2C2C2A; font-size:13px; }}
  .oi-contact {{ width:100%; color:#5F5E5A; font-size:11px; font-family:ui-monospace, Menlo, monospace; }}
  .oi-none {{ font-style:italic; color:#A32D2D; font-family:inherit; }}
  .matches {{ margin-top:6px; display:flex; flex-direction:column; gap:4px; }}
  .match {{ display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; font-size:12px;
            padding:5px 9px; background:#F7F5EF; border:0.5px solid #E4E2D9; border-radius:8px; }}
  .m-strength {{ font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:.04em;
                 padding:1px 7px; border-radius:6px; }}
  .m-strong {{ background:#EAF3DE; color:#3B6D11; }}
  .m-weak {{ background:#FAEEDA; color:#854F0B; }}
  .m-asserted {{ background:#E6F1FB; color:#185FA5; }}
  .m-id {{ font-family:ui-monospace, Menlo, monospace; font-weight:600; color:#2C2C2A; }}
  .m-name {{ color:#2C2C2A; }}
  .m-on {{ color:#5F5E5A; }}
  .m-contact {{ width:100%; color:#8A8980; font-size:11px; font-family:ui-monospace, Menlo, monospace; }}
  .cmt {{ margin-top:10px; background:#FAEEDA; border-radius:8px; padding:8px 11px;
          font-size:13px; line-height:1.5; }}
  .cmt-l {{ display:block; font-size:11px; color:#854F0B; margin-bottom:2px; }}
</style></head>
<body><div class="wrap">
<h1>Intake board</h1>
<div class="sub">{len(orders)} orders &middot; {summary}{cust_summary} &middot; {generated_at}</div>
{''.join(cards)}
</div></body></html>"""


def _emit_board_data(orders, generated_at):
    """Write the two SMALL data files the static dedupe_board.html reads.

    dedupe_data.js   -- window.DEDUPE_DATA: queue + live Admin/LW dedupe (this file;
                        produced locally, no Claude usage).
    dedupe_keys.json -- tiny [{sor_no, email, phone, name}] list the separate SF step
                        queries Salesforce on (keeps the SF step's input tiny).

    The board HTML is static and is NEVER regenerated here -- a run only rewrites these.
    """
    board_orders, keys = [], []
    for o in orders:
        if o.get("classification") != "System":
            continue
        cc = o.get("customer_check") or {}
        matches = []
        for m in cc.get("matches", []):
            det = m.get("detail", "")
            sig = m.get("signal", "")
            matches.append({
                "cust_id": m.get("cust_id", ""),
                "name": m.get("name", ""),
                "strength": m.get("strength", ""),
                "matched_on": (f"{sig}: {det}" if det else sig),
                "contact_name": m.get("contact_name", ""),
                "contact_email": m.get("contact_email", ""),
                "contact_phone": m.get("contact_phone", ""),
            })
        board_orders.append({
            "sor_no": o.get("sor_no", ""), "sor_id": o.get("sor_id", ""),
            "sor_url": o.get("sor_url", ""), "classification": o.get("classification", ""),
            "dealer": o.get("dealer", ""), "location_name": o.get("location_name", ""),
            "location_address": o.get("location_address", ""),
            "contact_name": o.get("contact_name", ""), "contact_email": o.get("contact_email", ""),
            "contact_phone": o.get("contact_phone", ""), "is_expedited": bool(o.get("is_expedited")),
            "admin": {"verdict": cc.get("verdict", "new"), "matches": matches},
            "reader_kits": o.get("reader_kits"),
        })
        keys.append({
            "sor_no": o.get("sor_no", ""),
            # ordered by SF match accuracy (Matt): address > email > contact name > store name
            "address": o.get("location_address", ""),
            "email": o.get("contact_email", ""),
            "contact_name": o.get("contact_name", ""),
            "store_name": o.get("location_name", ""),
            "phone": o.get("contact_phone", ""),
        })
    with open(os.path.join(REPO_ROOT, "dedupe_data.js"), "w", encoding="utf-8") as f:
        f.write("window.DEDUPE_DATA = "
                + json.dumps({"generated_at": generated_at, "orders": board_orders}, indent=2)
                + ";\n")
    with open(os.path.join(REPO_ROOT, "dedupe_keys.json"), "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2)
    print(f"  dedupe_data.js: {len(board_orders)} system orders + Admin dedupe")
    print(f"  dedupe_keys.json: {len(keys)} keys for the SF step")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(page, limit=None):
    t0 = time.time()
    print("\n" + "=" * 60)
    print("  INTAKE -- Submitted/In Review queue")
    print("=" * 60)

    tq = time.time()
    queue = read_sor_queue(page)
    print(f"[TIME] queue scrape: {time.time() - tq:.1f}s")
    if not queue:
        print("[DONE] Nothing to analyze.")
        return
    if limit:
        queue = queue[:limit]
        print(f"[INFO] Limiting to first {limit}")

    # Focus on System (Laundromat) orders only for now -- routes/parts/cards are
    # skipped on the board. One-line revert: remove this filter to surface everything.
    queue = [r for r in queue if classify_from_type(r["type_label"]) == "System"]
    print(f"[INFO] System orders only: {len(queue)}")

    orders = []
    for i, row in enumerate(queue, 1):
        print(f"\n--- [{i}/{len(queue)}] {row['sor_no']} ({row['type_label']}) ---")
        try:
            orders.append(analyze_sor(page, row))
        except Exception as e:
            print(f"[ERROR] {row['sor_no']}: {e}")
            orders.append({
                "sor_id": row["sor_id"], "sor_no": row["sor_no"], "sor_url": row["sor_url"],
                "classification": classify_from_type(row["type_label"]),
                "type_label": row["type_label"], "dealer": row["dealer"],
                "description": row["description"], "submitted_on": row["submitted_on"],
                "po_number": row["po_number"], "required_date": "", "required_date_raw": "",
                "is_expedited": bool(row.get("expedited")), "card_design_type": "",
                "card_workflow": "none", "processor_type": "", "contact_name": "",
                "contact_email": "", "location_address": "", "comments": "",
                "flags": ["Read error"], "error": str(e),
            })

    # Suggested assembly week for system/route orders -- batch FIFO against live capacity,
    # decrementing as we assign so two orders don't double-book the same week.
    sched = [o for o in orders if o["classification"] in ("System", "Route") and o.get("weight", 0) > 0]
    if sched:
        try:
            print("\n--- Scheduling (suggested assembly weeks) ---")
            schedule = read_schedule_capacity(page)
            for o in sorted(sched, key=lambda x: (x.get("required_date") or "9999-99-99", x["sor_no"])):
                wk, label, _reason = pick_assembly_week(
                    schedule,
                    required_date=o.get("required_date") or None,
                    is_expedited=o.get("is_expedited", False),
                    order_weight=o.get("weight", 0),
                )
                o["assembly_week"] = label or ""
                o["assembly_week_date"] = wk or ""
                if label:
                    for s in schedule:
                        if s["week"] == label:
                            s["total"] += o.get("weight", 0)
                            break
        except Exception as e:
            print(f"[WARN] scheduling skipped: {e}")

    # Reader-kit resolution -- surface the MISSING kits (unassigned on the SOR's
    # Card Reader Kits table) plus any machine models dealers hid in the comments,
    # and propose a KIT-* for each with a confidence. One /reader_lookup fetch for
    # the whole batch (like the single Admin /customers scrape). Advisory only.
    # MUST run before dedup: dedup navigates to admintools (different origin) and the
    # matcher's fetch('/reader_lookup/index') is same-origin off a MOOPS page.
    rk_orders = [o for o in orders if o["classification"] in ("System", "Route")]
    if rk_orders:
        try:
            print("\n--- Reader kits (missing + comment models) ---")
            all_models = set()
            for o in rk_orders:
                for m in (o.get("reader_table") or {}).get("machines", []):
                    if not m.get("assigned") and m.get("model"):
                        all_models.add(m["model"].upper())
                for cm in o.get("comment_models", []):
                    all_models.add(cm.upper())
            resolved = reader_kits.resolve_models(page, all_models) if all_models else {}
            for o in rk_orders:
                summ = reader_kits.build_order_summary(o, resolved)
                if summ["missing"]:
                    props = ", ".join(
                        f'{m["model"]}->{m["proposed_kit"] or "?"}[{m["method"]}/{m["strength"]}]'
                        for m in summ["missing"][:4])
                    print(f"  {o['sor_no']:<10} {len(summ['missing'])} missing: {props}")
            # Durable record for later review/training.
            reader_kits.write_assessment_log(rk_orders, REPO_ROOT)
        except Exception as e:
            print(f"[WARN] reader-kit resolution skipped: {e}")

    # Customer dedup (Stage 1 -- Admin Portal /customers). System orders only:
    # routes attach to an existing dealer umbrella account, and parts/cards onboard
    # no customer. One /customers scrape for the whole batch; matching is in-memory.
    # SF (Stage 2) plugs in later as its own scraper feeding the SAME match_customer.
    sys_orders = [o for o in orders if o["classification"] == "System"]
    if sys_orders:
        try:
            print("\n--- Customer dedup (Admin /customers) ---")
            customers = scrape_admin_customers(page)
            for o in sys_orders:
                ex_id = o.get("existing_customer_id", "")
                if ex_id:
                    # SOR already names the existing account -- trust it over fuzzy matching.
                    o["customer_check"] = {
                        "verdict": "existing",
                        "matches": [{"cust_id": ex_id,
                                     "name": o.get("existing_customer_name", ""),
                                     "signal": "sor_assigned", "strength": "asserted",
                                     "detail": "named on SOR"}],
                        "resolution": "pending",
                        "sf_match": None,
                    }
                    print(f"  {o['sor_no']:<10} EXISTING  {ex_id} (from SOR)")
                    continue
                signals = {
                    "customer_name": o.get("location_name", ""),
                    "contact_name": o.get("contact_name", ""),
                    "contact_email": o.get("contact_email", ""),
                    "contact_phone": o.get("contact_phone", ""),
                }
                res = dedup.match_customer(signals, customers)
                o["customer_check"] = {
                    "verdict": res["verdict"],
                    "matches": res["matches"][:6],
                    "resolution": "pending",
                    "sf_match": None,  # reserved for Stage 2 (Salesforce)
                }
                ids = ", ".join(m["cust_id"] for m in res["matches"][:4])
                print(f"  {o['sor_no']:<10} {res['verdict'].upper():<9} {ids}")
        except Exception as e:
            print(f"[WARN] dedup skipped: {e}")
    # schema consistency: non-System orders carry an explicit null
    for o in orders:
        o.setdefault("customer_check", None)
        o.setdefault("reader_kits", None)

    # Oldest on top. Submitted On is date-only (no time), so the queue's own order
    # (newest-first) is the only reliable chronology -- reverse it for oldest-first.
    orders.reverse()
    generated_at = datetime.now().strftime("%a %b %d, %Y %H:%M")

    plan_path = os.path.join(REPO_ROOT, "intake_plan.json")
    board_path = os.path.join(REPO_ROOT, "dedupe_board.html")  # the static shell (built once)
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": generated_at, "orders": orders}, f, indent=2)
    # Static board is NOT regenerated -- just refresh the small data files it reads.
    _emit_board_data(orders, generated_at)

    print("\n" + "=" * 60)
    print(f"  {len(orders)} orders analyzed in {time.time() - t0:.0f}s")
    for o in orders:
        rd = o["required_date"] or "no date"
        exp = " EXP" if o["is_expedited"] else ""
        fl = (" [" + ", ".join(o["flags"]) + "]") if o["flags"] else ""
        title = (o["description"] or o["dealer"] or "")[:26]
        print(f"  {o['sor_no']:<10} {o['classification']:<8} {title:<26} {rd}{exp}{fl}")
    print("=" * 60)
    print(f"  Board: {board_path}")
    print(f"  Plan:  {plan_path}")

    try:
        webbrowser.open(pathlib.Path(board_path).resolve().as_uri())
    except Exception as e:
        print(f"[WARN] Could not auto-open board: {e}")
