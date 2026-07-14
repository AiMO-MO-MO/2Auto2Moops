"""
Salesforce workflow -- STANDALONE (run on its own: `sf <id>`).

Deliberately NOT wired into the `s <id>` chain yet (Matt: don't make SF during a system
run for now; build it separately so it can be tested and connected later). Mechanism is
Playwright on the Lightning UI -- there is NO connector and NO REST API for this org
(settled 2026-06-04). Full field spec: docs/salesforce.md.

Order: dedupe (intake decision) -> Account -> Location -> Contact -> Opportunity (+ Cents
POS opp if a POS is on the order) -> add hosting-plan product -> Account Note -> set
LW_account_ID__c -> IT email -> hand opp owner to Mark. SF runs AFTER the MOOPS parts.

Status: data-gathering + plan + dedupe + IT-email are built and runnable. The actual New-form
fills are STUBBED pending an `inspect-form` of each New form (Account / Location / Contact /
Opportunity) from inside the authenticated console -- never guess SF selectors.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

from core.browser import navigate_to_so
from core.moops import read_sor_data, read_internal_notes, read_so_end_customer, read_products
from core.provisioning import _proper_case   # same name formatting used for the Cust ID

SF_BASE = "https://trycentssf.lightning.force.com"
SECONDARY_OWNER_ID = "005S6000003nvEjIAI"  # Mark -- Secondary_Owner__c on every opportunity (always)

MOOPS_BASE = "https://moops.mitechisys.com"
SAAS_WEBHOOK_ENV = "SLACK_SAAS_WEBHOOK_URL"  # incoming-webhook URL for #moops-matt-mark (env only, never committed)

_DIRECTIONALS = {"n", "s", "e", "w", "north", "south", "east", "west",
                 "ne", "nw", "se", "sw"}

_STATE_FULL = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def to_e164(phone: str) -> str:
    """Normalize a US phone to +1XXXXXXXXXX (SF rejects hyphenated formats). '' if < 10 digits."""
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return f"+1{d}" if len(d) == 10 else ""


def expand_state(s: str) -> str:
    """Expand a 2-letter state to its full name (SF wants full names). Pass through otherwise."""
    s = (s or "").strip()
    return _STATE_FULL.get(s.upper(), s)


def opportunity_name(street: str, so_id) -> str:
    """`<street number> <first non-directional street word>-Moops-SO-<SO#>`
    e.g. '3400 West Vine Street' + 19871 -> '3400 Vine-Moops-SO-19871'
         '4900 Valley Blvd'      + 19402 -> '4900 Valley-Moops-SO-19402'"""
    toks = (street or "").split()
    num = toks[0] if toks and re.match(r"^\d", toks[0]) else ""
    word = ""
    for t in toks[1:]:
        if t.strip(".").lower() in _DIRECTIONALS:
            continue
        word = t.strip(".,")
        break
    base = f"{num} {word}".strip() or (street or "").strip()
    return f"{base}-Moops-SO-{str(so_id).lstrip('#')}"


def _parse_address(addr: str) -> dict:
    """Best-effort split of a MOOPS multi-line address into street/city/state/zip."""
    lines = [l.strip() for l in (addr or "").splitlines() if l.strip()]
    # drop a leading name line (the location name) if present
    street = city = state = zipc = ""
    # last line like "City, State, Zip, Country" or "City, ST 12345"
    body = lines[-1] if lines else ""
    m = re.search(r"([A-Za-z .'-]+),\s*([A-Za-z]{2}|[A-Za-z ]+),?\s*(\d{5})", body)
    if m:
        city, state, zipc = m.group(1).strip(), m.group(2).strip(), m.group(3)
    # street = the line before the city line (skip the name line)
    if len(lines) >= 2:
        street = lines[-2] if m else lines[-1]
    return {"street": street, "city": city, "state": expand_state(state), "zip": zipc}


def build_plan(so_id, sor: dict, cust_id: str, products: list) -> dict:
    """Pure: assemble the Salesforce record plan from the order data (no browser)."""
    addr = _parse_address(sor.get("location_address", ""))
    loc_name = (sor.get("location_name", "") or "").split(" - ")[0].strip()
    has_pos = any("POS" in (p.get("part_number", "") or "").upper() for p in (products or []))
    return {
        "account": {
            "Name": loc_name, "Type": "Prospect", "Status": "Working",
            "LW_account_ID__c": cust_id, "billing": addr,
            "Phone": to_e164(sor.get("contact_phone", "")), "Website": "(Google the address)",
        },
        "location": {"Name": loc_name, "Status": "Prospect", "StatusReason": "Working",
                     "Type": "SS + FS", "address": addr},
        "contact": {"FirstName": (sor.get("contact_name", "") or "").split(" ")[0],
                    "LastName": " ".join((sor.get("contact_name", "") or "").split(" ")[1:]),
                    "Email": sor.get("contact_email", ""),
                    "Phone": to_e164(sor.get("contact_phone", ""))},
        "opportunity": {
            "Name": opportunity_name(addr.get("street", ""), so_id),
            "Stage": "Demo Booked", "Type": "New Business", "LeadSource": "Partner",
            "Secondary_Owner__c": SECONDARY_OWNER_ID,
            "Sales_Opportunity_Source__c": "LW Moops Order",
            "HW_Interest": "Laundroworks", "BillingFrequency": "Monthly", "Term": "One Year",
            "NextStep": "Install date - payment processing",
            "demo_dates": "today", "Distributor": "(dealer from the order)",
            "product": "Laundroportal hosting (add after save)",
        },
        "cents_pos_opp": ({"Name": f"Cents POS {loc_name}", "Stage": "Demo Booked",
                           "Type": "Upsell - New Product Added",
                           "Sales_Opportunity_Source__c": "Phone - AE",
                           "close": "today + 30"} if has_pos else None),
        "account_note": "Laundroportal Set Up",
    }


def build_it_email(loc_value: str, custom_location_id: str) -> str:
    """The IT-ticket email body (no subject/greeting/sign-off) requesting the LW Loc id +
    the Prospect->Customer flip (which IT must do)."""
    return (f"May we please make the following a customer location with LW Loc ID: {loc_value}\n"
            f"{SF_BASE}/lightning/r/Custom_Location__c/{custom_location_id}/view\n"
            "May we also make the associated account a customer account")


def _processor_label(sor: dict) -> str:
    pt = (sor.get("processor_type", "") or "").upper()
    if "FORTIS" in pt or "EBT" in pt or pt.strip() == "2":
        return "Fortis (EBT)"
    return "Stripe"


def build_saas_message(so_id, cust_id, location_key, sor: dict) -> dict:
    """Task 6 SaaS handoff -> Slack mrkdwn payload for #moops-matt-mark.

    Posting this IS task 6 for our checklist. It does NOT create Salesforce records or send the
    SaaS contract -- it tells Mark a new order is ready for the account / location / opportunity
    work + intro email, and hands him the info he'd otherwise pull off the SOR. Reuses the SF
    field helpers (to_e164, _parse_address) so the copy block matches docs/salesforce.md."""
    sor = sor or {}
    sid = str(so_id).lstrip("#")
    so_link = f"{MOOPS_BASE}/order?order_id={sid}"
    loc_name = _proper_case((sor.get("location_name", "") or "").split(" - ")[0].strip()) or "(location)"
    dealer = sor.get("dealer", "") or "(dealer not read)"
    addr = _parse_address(sor.get("location_address", ""))
    phone = sor.get("contact_phone", "") or ""
    e164 = to_e164(phone)
    # Everything except the clickable SO link goes in the code block, one key:value per line
    # (links don't render inside code blocks, so only the SO link sits up top).
    block = "\n".join([
        f"Cust ID:       {cust_id}",
        f"Location_Key:  {location_key}",
        f"Dealer:        {dealer}",
        f"Processor:     {_processor_label(sor)}",
        f"Location name: {loc_name}",
        f"Street:        {addr.get('street', '')}",
        f"City:          {addr.get('city', '')}",
        f"State:         {addr.get('state', '')}",
        f"ZIP:           {addr.get('zip', '')}",
        f"Contact:       {_proper_case(sor.get('contact_name', '')) or '-'}",
        f"Email:         {sor.get('contact_email', '') or '-'}",
        f"Phone:         {phone or '-'}" + (f"  ({e164})" if e164 else ""),
        f"SO number:     {sid}",
        f"Reader kits:   {sor.get('total_kits', '') or '-'}",
    ])
    text = (
        f"*{loc_name} - <{so_link}|SO-{sid}>*\n"
        f"```{block}```\n"
        "React when the account is built."
    )
    return {"text": text}


SAAS_WEBHOOK_FILE = "slack_webhook.txt"  # local fallback (gitignored): paste ONLY the webhook URL


def _resolve_saas_webhook():
    """Webhook URL: env var SLACK_SAAS_WEBHOOK_URL first, else the first non-comment line of a
    gitignored `slack_webhook.txt` at the repo root (just the URL, one line). '' if neither set.
    Lets a non-dev configure Slack by dropping a file in the folder -- no shell env vars needed."""
    url = os.environ.get(SAAS_WEBHOOK_ENV, "").strip()
    if url:
        return url
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), SAAS_WEBHOOK_FILE)
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except Exception:
        pass
    return ""


def post_saas_handoff(so_id, cust_id, location_key, sor: dict):
    """POST the task-6 handoff to #moops-matt-mark via a Slack incoming webhook.

    URL from env SLACK_SAAS_WEBHOOK_URL, else a gitignored slack_webhook.txt at the repo root
    (never committed -- post-only, single channel). Returns (ok: bool, info: str). Missing URL or
    a failed POST -> (False, reason) so the chain leaves task 6 To Do instead of crashing."""
    url = _resolve_saas_webhook()
    if not url:
        return False, f"no webhook -- set {SAAS_WEBHOOK_ENV} or create {SAAS_WEBHOOK_FILE} at the repo root"
    data = json.dumps(build_saas_message(so_id, cust_id, location_key, sor)).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            code = r.getcode()
            body = r.read().decode("utf-8", "ignore").strip()
    except Exception as e:
        return False, f"post failed: {e}"
    if code == 200:
        return True, "posted"
    return False, f"HTTP {code}: {body[:120]}"


def run(page, so_id):
    """Standalone SF workflow. Reads the order, builds + prints the plan, runs the SF dedupe.
    The New-form CREATE steps are stubbed pending inspect-form of each form (no guessed
    selectors). Run from inside the authenticated console (Okta)."""
    print("\n" + "=" * 60)
    print(f"  SALESFORCE WORKFLOW (sf {so_id}) -- SO-{so_id}")
    print("=" * 60)

    navigate_to_so(page, so_id)
    sor = read_sor_data(page)
    fc = read_so_end_customer(page)
    cust_id = fc.get("id", "")
    if not cust_id:
        notes = read_internal_notes(page)  # fallback
        cust_id = (notes.get("existing_customer_id", "") or "")
    products = read_products(page)

    plan = build_plan(so_id, sor, cust_id, products)
    print("\n[SF PLAN] (from the order -- verify before creating)")
    for section, fields in plan.items():
        if fields is None:
            continue
        print(f"  {section}:")
        if isinstance(fields, dict):
            for k, v in fields.items():
                print(f"      {k}: {v!r}")
        else:
            print(f"      {fields!r}")

    # Dedupe surface -- address first (top signal), then email. Uses the global-search typeahead.
    from core.provisioning import inspect_sf_search
    addr = plan["location"]["address"]
    q = (addr.get("street", "") or plan["account"]["Name"])
    print(f"\n[SF DEDUPE] searching SF for {q!r} (address-first). Review candidates -> go/no-go.")
    inspect_sf_search(page, q)

    print("\n[SF CREATE] STUBBED -- the New-form fills (Account / Location / Contact / Opportunity)"
          " need an `inspect-form` of each form first (no guessed SF selectors). Plan above is"
          " what will be filled. IT email + opp-owner handoff come after the records exist.")
    print("=" * 60)
