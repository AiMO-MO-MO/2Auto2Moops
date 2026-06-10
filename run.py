"""
2AUTO2MOOPS -- Sales Order Playbook Runner

Quick reference:
  python run.py --so-id 19546                              # read only
  python run.py --so-id 19546 --first-touch                # full system order playbook
  python run.py --so-id 19546 --first-touch --assembly-week 2026-06-15
  python run.py --so-id 19667 --parts-order                # parts/readers order
  python run.py --so-id 19546 --check-schedule             # show assembly capacity
  python run.py --so-id 19546 --set-tag auto               # auto-generate tag
  python run.py --so-id 19546 --set-tag "custom tag"       # set specific tag
  python run.py --so-id 19546 --add-part 03-01-34 --qty 2  # add specific part
  python run.py --so-id 19546 --add-missing                # add rule-based parts
  python run.py --so-id 19546 --add-missing --read-sor     # add parts + pinpad kit
  python run.py --so-id 19546 --add-splicers               # update wire splicer qty
  python run.py --so-id 19546 --assembly-week 2026-06-15   # set assembly week
  python run.py --so-id 19546 --set-tasks                  # set task checklist
  python run.py --so-id 19546 --clone-card                 # clone temp card (auto name)
  python run.py --so-id 19546 --clone-card THELNDRY        # clone with specific name
  python run.py --so-id 19546 --add-card-to-so CARD-MD-X   # add card to SO
  python run.py --so-id 19546 --card-email CARD-MD-X       # open card design email
  python run.py --so-id 19546 --itf                        # IT provisioning form
  python run.py --so-id 19546 --save                       # save the SO
"""

import argparse
import os
import sys
import time

# Prevent stale .pyc bytecode cache (OneDrive sync issue)
sys.dont_write_bytecode = True

# Full run output is teed here so the assistant can review it (read the file) without
# Matt pasting big console dumps. Truncated at the start of each console command.
RUN_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.log")

from core.browser import launch_browser, navigate_to_so
from core.moops import (
    decode_vac,
    determine_pinpad_kit,
    read_products,
    read_customer_name,
    read_missing_parts,
    read_sor_data,
    read_schedule_capacity,
    read_existing_customer_id,
    read_internal_notes,
    read_task_states,
    generate_card_shortname,
    clone_temp_card,
    open_card_design_email,
    open_itf_form,
    save_so,
    build_tag,
    action_add_part,
    action_set_tag,
    action_set_assembly_week,
    action_add_required_parts,
    action_add_splicers,
    action_set_system_tasks,
    action_add_card_to_so,
    set_so_end_customer,
)
from core.schedule import print_schedule
from playbooks import first_touch, parts_order, cards_order, final_touch, intake, salesforce
from core import provisioning, portal, dedup
from core.order_plan import build_system_rerun_plan, classify_card_type


# ---------------------------------------------------------------------------
# Read SO (used by individual action flags)
# ---------------------------------------------------------------------------

def read_so(page, so_id):
    """Navigate to SO and read everything. Returns unified data dict."""
    navigate_to_so(page, so_id)

    tag = page.locator('input[name="description"]').input_value().strip()
    print(f"Tag: {tag or '(empty)'}")

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
            print(f"  {m['part_number']:20s} -> {m['associated_part']:15s} "
                  f"qty={m['qty']:5s} {m['description'][:40]}")
    else:
        print("Missing parts: none")

    vac_summary = []
    for p in products:
        if p["part_number"].upper().startswith("VAC"):
            d = decode_vac(p["part_number"])
            vac_summary.append({**d, "qty": p["qty"]})
            print(f"  VAC decode: {p['part_number']} -> cabinet={d['cabinet']} "
                  f"pinpad={d['needs_pinpad']} touch={d['is_touchscreen']}")

    return {
        "tag": tag,
        "customer_name": customer_name,
        "products": products,
        "missing": missing,
        "vac_summary": vac_summary,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

import re as _re

_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}


class _QuietWriter:
    """Filter the noisy per-step logging so pasted output stays small.

    Drops [READ]/[NAV]/[ACTION]/[INFO] chatter and bare timing lines like
    '[read_sor_data 4.9s]'. Keeps section headers, '>>' picks, tags, the final
    summary, [WARNING]/[PAUSE] prompts, and any errors/tracebacks. Default on;
    pass -v / --verbose to see everything.
    """

    _DROP_PREFIX = ("[READ]", "[NAV]", "[ACTION]", "[INFO]")
    _TIMING = _re.compile(r'^\[[^\]]*\d+\.\d+s\]$')

    def __init__(self, real, logpath=None):
        self._real = real
        self._log = None
        if logpath:
            try:
                self._log = open(logpath, "w", encoding="utf-8")
            except Exception:
                self._log = None

    def reset_log(self):
        """Truncate the logfile -- called per console command so it holds only the latest run."""
        if self._log:
            try:
                self._log.seek(0)
                self._log.truncate()
            except Exception:
                pass

    def write(self, s):
        if self._log:                       # tee the FULL (unfiltered) output for review
            try:
                self._log.write(s)
                self._log.flush()
            except Exception:
                pass
        kept = []
        for line in s.splitlines(keepends=True):
            t = line.strip()
            if any(t.startswith(p) for p in self._DROP_PREFIX):
                continue
            if self._TIMING.match(t):
                continue
            kept.append(line)
        if kept:
            self._real.write("".join(kept))

    def flush(self):
        self._real.flush()
        if self._log:
            try:
                self._log.flush()
            except Exception:
                pass

    def __getattr__(self, name):
        return getattr(self._real, name)


def _card_type(design: str) -> str:
    """Backward-compatible wrapper for older imports from run.py."""
    return classify_card_type(design)


def _existing_card_blocks_card_clone(card_type: str) -> bool:
    """Only new-design orders treat an existing CARD-MD row as a duplicate guard."""
    return card_type == "new"


# Friendly shorthand.  Grammar:  <type> [touch] <id>
#   type:  s|system   r|route   p|parts   c|cards   m|cardmod
#   touch: first | final     (required for system/route; ignored for parts/cards)
# Examples:  s first 19697 | s final 19697 | p 19697 | c 19697 STARWASH
# Plus:  intake | inspect <sor> | read <id>
TYPE_MAP = {
    "s": "system", "system": "system",
    "r": "route", "route": "route",
    "p": "parts", "parts": "parts",
    "c": "cards", "cards": "cards",
    "m": "cardmod", "cardmod": "cardmod",
}
_TOUCH_WORDS = {"first", "final"}
_PLAYBOOK_FLAG = {
    ("system", "first"): "--first-touch", ("system", "final"): "--final-touch",
    ("route", "first"): "--first-touch", ("route", "final"): "--final-touch",
    ("parts", None): "--parts-order",
    ("cards", None): "--cards-order",
    ("cardmod", None): "--card-modify",
}


def _usage(msg):
    """Print a one-line usage hint and abort this command (caught by the console loop)."""
    print(f"[usage]  python run.py {msg}")
    raise SystemExit(2)


def _expand_verb(argv):
    """Translate '<type> [touch] <id>' shorthand into the --flag form argparse expects.
    Falls through untouched if the first token is already a --flag."""
    if len(argv) < 2 or argv[1].startswith("-"):
        return argv
    verb, rest = argv[1].lower(), argv[2:]
    if verb == "intake":
        return [argv[0], "--intake"] + rest
    if verb == "recopy":  # re-copy the last EFS snippet to the clipboard
        return [argv[0], "--recopy"]
    if verb == "inspect":
        return [argv[0], "--inspect-sor"] + rest
    if verb == "inspect-form":
        return [argv[0], "--inspect-form"] + rest
    if verb == "sf-search":  # SF dedupe discovery: type a query, dump the typeahead dropdown
        if not rest:
            return _usage('sf-search "<query>"')
        return [argv[0], "--sf-search", " ".join(rest)]
    if verb == "dedup-sor":  # read a raw SOR like an order, then dedup its contact/name
        if not rest:
            print("[usage]  python run.py dedup-sor <sor_id>")
            raise SystemExit(2)
        return [argv[0], "--dedup-sor", rest[0]]
    if verb == "dedup":
        if not rest:
            print('[usage]  python run.py dedup <email | phone | "laundromat or contact name">')
            raise SystemExit(2)
        return [argv[0], "--dedup-only", " ".join(rest)]
    if verb == "sx":  # system first-touch, no-ITF + dedup test (run-many test flow)
        if not rest:
            print("[usage]  python run.py sx <so_id>   (first-touch: no ITF + dedup test)")
            raise SystemExit(2)
        return [argv[0], "--so-id", rest[0], "--first-touch",
                "--no-itf", "--dedup-test"] + rest[1:]
    if verb == "createcust":
        if not rest:
            print("[usage]  python run.py createcust <so_id> [cust_id] [--preview]")
            raise SystemExit(2)
        oid, extra = rest[0], rest[1:]
        cmd = [argv[0], "--so-id", oid, "--create-customer"]
        if extra and not extra[0].startswith("-"):
            cmd += ["--cust-id", extra[0]]
            extra = extra[1:]
        cmd += extra  # pass through flags like --preview
        return cmd
    if verb == "apiuser":  # post-save: fill API user (POS) on the saved customer page
        if not rest:
            print("[usage]  python run.py apiuser <cust_id>")
            raise SystemExit(2)
        return [argv[0], "--api-user", rest[0]]
    if verb == "inspect-lp":  # log into LaundroPortal for a cust, then dump a portal form
        if len(rest) < 2:
            print("[usage]  python run.py inspect-lp <cust_id> <portal_url>")
            raise SystemExit(2)
        return [argv[0], "--inspect-lp", rest[0], rest[1]]
    if verb == "addloc":  # LaundroPortal Add Location for <cust_id>, address from <so_id>'s SOR
        if len(rest) < 2:
            print("[usage]  python run.py addloc <so_id> <cust_id>")
            raise SystemExit(2)
        return [argv[0], "--so-id", rest[0], "--add-location", rest[1]]
    if verb == "adduser":  # LaundroPortal Add User for <cust_id>, contact from <so_id>
        if len(rest) < 2:
            print("[usage]  python run.py adduser <so_id> <cust_id>")
            raise SystemExit(2)
        return [argv[0], "--so-id", rest[0], "--add-user", rest[1]]
    if verb == "provision":  # re-run the guided no-ITF chain for an existing customer
        if len(rest) < 2:
            print("[usage]  python run.py provision <so_id> <cust_id>")
            raise SystemExit(2)
        return [argv[0], "--so-id", rest[0], "--provision", rest[1]]
    if verb == "custid":  # standalone Cust ID workflow (create customer + finalize), for testing
        if not rest:
            print("[usage]  python run.py custid <so_id> [cust_id]")
            raise SystemExit(2)
        cmd = [argv[0], "--so-id", rest[0], "--custid"]
        if len(rest) > 1:
            cmd += ["--cust-id", rest[1]]
        return cmd
    if verb == "stripe":  # initiate Stripe at the location (guarded to cust_id)
        if len(rest) < 2:
            print("[usage]  python run.py stripe <cust_id> <location_key>")
            raise SystemExit(2)
        return [argv[0], "--stripe", rest[0], rest[1]]
    if verb == "inspect-pp":  # dump the per-location Payment Processing form (via panel + click)
        if not rest:
            print("[usage]  python run.py inspect-pp <location_key>")
            raise SystemExit(2)
        return [argv[0], "--inspect-pp", rest[0]]
    if verb == "intro":  # Admin: send intro email for a customer's admin users
        if not rest:
            print("[usage]  python run.py intro <cust_id>")
            raise SystemExit(2)
        return [argv[0], "--intro", rest[0]]
    if verb == "card":  # run ONLY the chain's card step on a system order (safe -- no
        # tag/order-type/shipment changes, unlike the `c` cards-only playbook)
        if not rest:
            return _usage("card <so_id> [cust_id]")
        cmd = [argv[0], "--so-id", rest[0], "--card-step"]
        if len(rest) > 1:
            cmd.append(rest[1])
        return cmd
    if verb == "read":  # read-only
        if not rest:
            print("[usage]  python run.py read <id>")
            raise SystemExit(2)
        return [argv[0], "--so-id", rest[0]] + rest[1:]
    if verb in ("snapshot", "plan"):  # read-only workflow state + rerun plan
        if not rest:
            return _usage("snapshot <id>")
        return [argv[0], "--so-id", rest[0], "--snapshot"] + rest[1:]
    if verb in ("s", "system"):
        # Main system run = the no-ITF flow (dedup -> tag -> schedule -> customer -> chain).
        # 'final' = pre-ship audit; 'first' = legacy ITF first-touch (kept, not headlined).
        sub = rest[0].lower() if rest else ""
        if sub == "final":
            if len(rest) < 2:
                return _usage("system final <id>")
            return [argv[0], "--so-id", rest[1], "--final-touch"] + rest[2:]
        if sub == "first":
            if len(rest) < 2:
                return _usage("system first <id>")
            return [argv[0], "--so-id", rest[1], "--first-touch"] + rest[2:]
        if not rest:
            return _usage("system <id>")
        return [argv[0], "--so-id", rest[0], "--first-touch", "--no-itf", "--dedup-test"] + rest[1:]
    if verb in ("sf", "salesforce"):  # STANDALONE Salesforce workflow (not part of `s <id>`)
        if not rest:
            return _usage("sf <id>")
        return [argv[0], "--so-id", rest[0], "--salesforce"] + rest[1:]
    if verb == "itf":  # open the IT provisioning form (Jira) standalone -- does NOT submit
        if not rest:
            return _usage("itf <id>")
        return [argv[0], "--so-id", rest[0], "--itf"]
    if verb == "final":  # pre-ship audit (alias for `s final`)
        if not rest:
            return _usage("final <id>")
        return [argv[0], "--so-id", rest[0], "--final-touch"]
    if verb == "tasks":  # read the task checklist states
        if not rest:
            return _usage("tasks <id>")
        return [argv[0], "--so-id", rest[0], "--read-tasks"]
    if verb == "settasks":  # set the system task checklist + save
        if not rest:
            return _usage("settasks <id>")
        return [argv[0], "--so-id", rest[0], "--set-tasks"]
    if verb in ("schedule", "sched"):  # show assembly-week capacity
        if not rest:
            return _usage("schedule <id>")
        return [argv[0], "--so-id", rest[0], "--check-schedule"]
    typ = TYPE_MAP.get(verb)
    if not typ:
        return argv  # unknown token / already --flag form: let argparse handle it
    touch = None
    if rest and rest[0].lower() in _TOUCH_WORDS:
        touch, rest = rest[0].lower(), rest[1:]
    if typ in ("system", "route") and touch is None:
        print(f"[usage]  python run.py {verb} first <id>   (or 'final')")
        raise SystemExit(2)
    if not rest:
        print(f"[usage]  python run.py {verb}{(' ' + touch) if touch else ''} <id>")
        raise SystemExit(2)
    oid, extra = rest[0], rest[1:]
    key = (typ, touch) if typ in ("system", "route") else (typ, None)
    flag = _PLAYBOOK_FLAG.get(key)
    cmd = [argv[0], "--so-id", oid]
    if flag:
        cmd.append(flag)
    return cmd + extra


def _do_create_customer(page, so_id, cust_id=None, preview=False, data=None):
    """Gather SO/SOR data and fill the Create Customer form (no submit).
    Shared by the CLI dispatch and the console.
    If `data` is provided (already read by the snapshot), fill from it instead of
    re-reading the SO/SOR -- the snapshot-driven path. Returns the cust id used."""
    if data is not None:
        cid = cust_id or provisioning.next_customer_id(page)
        provisioning.fill_create_customer(page, {
            "so_id": so_id,
            "customer_name": data.get("customer_name", ""),
            "contact_name": data.get("contact_name", ""),
            "contact_email": data.get("contact_email", ""),
            "contact_phone": data.get("contact_phone", ""),
            "is_route": bool(data.get("is_route")),
        }, cust_id=cid, preview=preview)
        return cid
    from core.moops import (read_internal_notes, read_sale_or_route,
                            read_existing_customer_id, read_sor_data)
    navigate_to_so(page, so_id)
    notes = read_internal_notes(page)
    is_route = read_sale_or_route(page).lower() == "route"
    if is_route:
        print("[WARN] create-customer is for System orders only right now "
              "-- routes attach to an existing dealer. Filling as Laundromat anyway.")
    existing = read_existing_customer_id(page)
    if existing and existing.get("id"):
        print(f"[WARN] SO already has Existing End Customer "
              f"{existing['name']} ({existing['id']}) -- a NEW customer may not be needed.")
    contact_name = notes.get("contact_name", "")
    contact_email = notes.get("contact_email", "")
    contact_phone = notes.get("contact_phone", "")
    if not contact_name:
        sor = read_sor_data(page)
        contact_name = sor.get("contact_name", "")
        contact_email = contact_email or sor.get("contact_email", "")
        contact_phone = contact_phone or sor.get("contact_phone", "")
        if contact_name:
            print(f"[INFO] Contact not in notes -- pulled from SOR: {contact_name}")
    cid = cust_id or provisioning.next_customer_id(page)
    provisioning.fill_create_customer(page, {
        "so_id": so_id,
        "customer_name": notes.get("location_name", ""),
        "contact_name": contact_name,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "is_route": is_route,
    }, cust_id=cid, preview=preview)
    return cid


_STATE_CODE = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA',
    'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE', 'florida': 'FL', 'georgia': 'GA',
    'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD', 'massachusetts': 'MA',
    'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO', 'montana': 'MT',
    'nebraska': 'NE', 'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM',
    'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
    'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT',
    'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV', 'wisconsin': 'WI',
    'wyoming': 'WY', 'district of columbia': 'DC',
}


def _state_code(s):
    s = (s or "").strip()
    return s.upper() if len(s) == 2 else _STATE_CODE.get(s.lower(), s)


# State (2-letter) -> dominant IANA timezone. Multi-zone states use the major zone;
# always VERIFY on the form (the LP default is America/Toronto, wrong for US).
_STATE_TZ = {
    'CA': 'America/Los_Angeles', 'WA': 'America/Los_Angeles', 'OR': 'America/Los_Angeles',
    'NV': 'America/Los_Angeles', 'AZ': 'America/Phoenix', 'ID': 'America/Boise',
    'UT': 'America/Denver', 'CO': 'America/Denver', 'NM': 'America/Denver', 'MT': 'America/Denver',
    'WY': 'America/Denver', 'TX': 'America/Chicago', 'OK': 'America/Chicago', 'KS': 'America/Chicago',
    'NE': 'America/Chicago', 'SD': 'America/Chicago', 'ND': 'America/Chicago', 'MN': 'America/Chicago',
    'IA': 'America/Chicago', 'MO': 'America/Chicago', 'AR': 'America/Chicago', 'LA': 'America/Chicago',
    'WI': 'America/Chicago', 'IL': 'America/Chicago', 'MS': 'America/Chicago', 'AL': 'America/Chicago',
    'TN': 'America/Chicago', 'MI': 'America/New_York', 'IN': 'America/New_York', 'KY': 'America/New_York',
    'OH': 'America/New_York', 'GA': 'America/New_York', 'FL': 'America/New_York', 'SC': 'America/New_York',
    'NC': 'America/New_York', 'VA': 'America/New_York', 'WV': 'America/New_York', 'DC': 'America/New_York',
    'MD': 'America/New_York', 'DE': 'America/New_York', 'PA': 'America/New_York', 'NJ': 'America/New_York',
    'NY': 'America/New_York', 'CT': 'America/New_York', 'RI': 'America/New_York', 'MA': 'America/New_York',
    'VT': 'America/New_York', 'NH': 'America/New_York', 'ME': 'America/New_York',
    'HI': 'Pacific/Honolulu', 'AK': 'America/Anchorage',
}


def _tz_for_state(code):
    return _STATE_TZ.get((code or "").upper(), "")


def _parse_address(blob):
    """Best-effort split of a SOR location-address blob into street/city/state/zip.
    The SOR Location Address often LEADS with the location name, so the street is
    detected as the first part beginning with a house number; city/state/zip follow.
    State is normalized to its 2-letter code. VERIFY before saving."""
    parts = [p.strip() for p in _re.split(r'[\n,]+', blob or '') if p.strip()]
    parts = [p for p in parts if p.lower().replace('.', '') not in ('united states', 'usa', 'us')]
    street, idx = '', 0
    for i, p in enumerate(parts):
        if _re.match(r'^\d+\s', p):        # "1701 Southeast Flower Mound Road"
            street, idx = p, i
            break
    if not street and parts:               # fallback: no leading-number line found
        street, idx = parts[0], 0
    m = _re.search(r'\b(\d{5})(?:-\d{4})?\b', blob or '')
    zip_ = m.group(1) if m else ''
    cleaned = [_re.sub(r'\b\d{5}(?:-\d{4})?\b', '', p).strip() for p in parts[idx + 1:]]
    cleaned = [c for c in cleaned if c]
    city = cleaned[0] if cleaned else ''
    state = _state_code(cleaned[1]) if len(cleaned) > 1 else ''
    return {"street": street, "city": city, "state": state, "zip": zip_}


def _do_addloc(page, so_id, cust_id, location_id=None, sor=None, seats=None):
    """Add Location in LaundroPortal for cust_id, address from the SOR.
    location_id: explicit id (existing customers → next 01/02); defaults to 0100001 (new).
    ONE-PASS: when `sor` (address/name) AND `seats` (VAC count) are threaded in, this does NOT
    navigate to the SO at all -- it goes straight to LaundroPortal. Falls back to an SO/SOR read
    only when they aren't supplied (e.g. the standalone `addloc` verb).
    Fill-only (provisioning.fill_location pauses for human Save)."""
    from core.browser import MOOPS_BASE

    if sor is not None and seats is not None:
        # Fully threaded -- no trip to the SO. Use whatever address data exists; human
        # corrects blanks during the Save pause. seats was read from the SO by the chain.
        loc_addr = sor.get("location_address", "")
        loc_name = sor.get("location_name", "")
        print(f"[ADDLOC] Using threaded SOR + seat count (no SO nav); seats = {seats}")
    elif sor is not None:
        # SOR threaded but no seat count -- one SO visit just to count VAC seats.
        navigate_to_so(page, so_id)
        loc_addr = sor.get("location_address", "")
        loc_name = sor.get("location_name", "")
        try:
            seats = page.evaluate(r"""() => {
                let n = 0;
                document.querySelectorAll('tr[id^="existing_part_order_"], tr[id^="new_part_order_"]').forEach(r => {
                    const a = r.querySelector('th[scope="row"] a');
                    const pn = a ? (a.innerText || '').trim() : '';
                    if (/^VAC/i.test(pn)) {
                        const inp = r.querySelector('input[type="number"]');
                        n += (inp ? parseInt(inp.value || '0', 10) : 0) || 0;
                    }
                });
                return n;
            }""")
        except Exception:
            seats = 0
        print(f"[ADDLOC] Reusing threaded SOR read; seats (VACs on SO) = {seats}")
    else:
        navigate_to_so(page, so_id)
        href = ""
        try:
            href = page.locator('a[href*="/order-requests/"]').first.get_attribute("href") or ""
        except Exception:
            pass
        detail = {}
        if href:
            sor_url = href if href.startswith("http") else f"{MOOPS_BASE}{href}"
            detail = intake.read_sor_detail(page, sor_url)
        else:
            print("[WARN] No SOR link on the SO -- address fields will be blank (fill manually)")
        loc_addr = detail.get("location_address", "")
        loc_name = detail.get("location_name", "")
        try:
            seats = sum(int(str(v.get("qty", 0)) or 0) for v in detail.get("vacs", []))
        except Exception:
            seats = 0

    addr = _parse_address(loc_addr)
    addr["customer_name"] = provisioning._proper_case((loc_name or "").split(" - ")[0].strip())
    addr["location_id"] = location_id or "0100001"
    addr["timezone"] = _tz_for_state(addr.get("state", ""))
    addr["seats"] = seats
    provisioning.fill_location(page, cust_id, addr)


def _do_adduser(page, so_id, cust_id, sor=None):
    """Add User in LaundroPortal for cust_id. ONE-PASS: prefer the threaded SOR contact so we
    DON'T navigate back to the SO; only fall back to reading SO notes when no contact was
    threaded. Fill-only + guarded against wrong-customer writes (see provisioning.fill_user)."""
    from core.moops import read_internal_notes, read_sor_data
    cn = ce = cp = ""
    if sor is not None:
        # Threaded SOR (already merged with SO notes by the chain) -- no SO nav needed.
        # If contact fields are blank it means no contact data exists; human fills manually.
        cn = sor.get("contact_name", "")
        ce = sor.get("contact_email", "")
        cp = sor.get("contact_phone", "")
    else:
        # Standalone call (adduser verb) -- no threaded data; read SO notes + SOR directly.
        navigate_to_so(page, so_id)
        notes = read_internal_notes(page)
        cn = notes.get("contact_name", "")
        ce = notes.get("contact_email", "")
        cp = notes.get("contact_phone", "")
        if not cn:
            sor = read_sor_data(page)
            cn = sor.get("contact_name", "")
            ce = ce or sor.get("contact_email", "")
            cp = cp or sor.get("contact_phone", "")
    provisioning.fill_user(page, cust_id,
                           {"contact_name": cn, "contact_email": ce, "contact_phone": cp})


def _do_cards(page, so_id, cust_id, sor=None, shortname=None):
    """Card workflow — single implementation used by system run AND cards-order playbook.
      new     -> clone + add + design email    -> returns "new"     (task 3 done)
      modify  -> version-bump clone + email    -> returns "new"     (task 3 done)
      reprint -> Create PO + PO email          -> returns "reprint" (task 5 done)
      none / generic / other -> nothing        -> returns "none"
    `sor` threaded from caller avoids re-fetching the SOR.
    `shortname` overrides the auto-generated card shortname (cards-order CLI arg)."""
    import time as _t
    from core.moops import (read_customer_name, read_sor_data, generate_card_shortname,
                            clone_temp_card, action_add_card_to_so, save_so, open_card_design_email,
                            read_products, read_card_end_customer, create_card_po, open_po_email)
    navigate_to_so(page, so_id)
    cust_name = read_customer_name(page)
    products = read_products(page)
    if sor is None:
        sor = read_sor_data(page)
    design = (sor.get("card_design_type", "") or "").strip()
    ct = _card_type(design)
    new_design = ct in ("new", "modify")
    reprint = ct == "reprint"

    if not design:
        print("\n[CARDS] No cards on the order -- nothing to do.")
        return "none"

    if new_design:
        # New design duplicate guard only. Modify orders often include the existing CARD-MD
        # as the source card; task state decides whether to run the modify workflow.
        if _existing_card_blocks_card_clone(ct):
            for p in products:
                if p["part_number"].upper().startswith("CARD-MD-"):
                    print(f"\n[CARDS] {p['part_number']} already on the SO -- card was made on a prior "
                          "touch; not cloning a new one. (Card tasks left as previously set.)")
                    return "exists"
        c_name = sor.get("contact_name", "")
        c_email = sor.get("contact_email", "")
        if cust_id and (not c_name or not c_email):
            try:
                from core.portal import read_admin_contact
                admin = read_admin_contact(page, cust_id)
                c_name = c_name or admin.get("contact_name", "")
                c_email = c_email or admin.get("contact_email", "")
            except Exception as e:
                print(f"[CARDS] Could not read Admin contact ({e})")
        # Shortname: caller override -> modify version-bump -> auto-generate
        if shortname and shortname != "auto":
            pass  # use provided
        elif ct == "modify":
            existing_card_row = next((p for p in products
                                      if p["part_number"].upper().startswith("CARD-MD-")), {})
            existing_card = existing_card_row.get("part_number", "")
            base = existing_card.upper().replace("CARD-MD-", "", 1) if existing_card else ""
            if base:
                stem = base.rstrip("0123456789")
                num = base[len(stem):]
                shortname = f"{stem}{(int(num) + 1) if num else 2}"
                print(f"[CARDS] Modify -> bumping {existing_card} to CARD-MD-{shortname}")
                if not cust_id:
                    owner = read_card_end_customer(page, existing_card, existing_card_row.get("href", ""))
                    cust_id = owner.get("id", "")
                    if cust_id:
                        print(f"[CARDS] Modify owner inherited from {existing_card}: {cust_id}")
            else:
                shortname = generate_card_shortname(cust_name)
        else:
            shortname = generate_card_shortname(cust_name)
        print(f"\n--- Cards ({ct}): CARD-MD-{shortname}, owner {cust_id} ---")
        card_part = clone_temp_card(page, shortname, end_customer_id=cust_id)
        expected_part = f"CARD-MD-{shortname.upper()}"
        if ct == "modify" and card_part != expected_part:
            print(f"[CARDS] Expected {expected_part} after clone, got {card_part}.")
            print("[CARDS] Stopping before adding/deleting cards; verify the cloned card manually.")
            return "none"
        navigate_to_so(page, so_id)
        action_add_card_to_so(page, card_part)
        save_so(page, accept_sor=False, clear_customer_location_blocker=False)
        open_card_design_email(page, card_part, contact_name=c_name, contact_email=c_email)
        try:
            input("\n[CHAIN] Review and send the card design email, then press Enter.")
        except (EOFError, KeyboardInterrupt):
            pass
        print(f"[CARDS] Done (new design) -- {card_part}")
        return "new"

    if reprint:
        # Existing card already on the SO -> create the PO + send the PO email (human-gated;
        # PO creation is never automated). This is task 5; tasks 3/4 are N/A for a reprint.
        print(f"\n--- Cards (reprint of existing design) ---")
        card_part = ""
        for p in products:
            if p["part_number"].upper().startswith("CARD-MD-"):
                if p.get("has_po"):
                    print(f"[CARDS] {p['part_number']} already has PO "
                          f"{p.get('po_link') or ''} -- skip card workflow.")
                    return "exists"
                card_part = p["part_number"]
                break
        if not card_part:
            print("[CARDS] No CARD-MD-* on the order -- can't create a PO; handle manually.")
            return "none"
        try:
            input(f"\n[CHAIN] Ready to Create PO for {card_part}. Press Enter to continue, or Ctrl+C to skip.")
        except (EOFError, KeyboardInterrupt):
            print("\n[CARDS] Stopped before PO creation.")
            return "none"
        po_url = create_card_po(page, card_part)
        if not po_url:
            print("[CARDS] PO not created -- handle manually.")
            return "none"
        po_page = page
        for p in page.context.pages:
            if "purchase" in (p.url or ""):
                po_page = p
                po_page.bring_to_front()
                break
        print("\n--- PO Email (review, clear CC, send) ---")
        open_po_email(po_page)
        try:
            _t.sleep(1)
            po_page.locator('select[name="purchase_state_id"]').select_option(label="Ordered")
            po_page.evaluate("""() => {
                for (const el of document.querySelectorAll('button, a, input[type=submit]')) {
                    if ((el.textContent || el.value || '').trim().startsWith('Save')) { el.click(); return; }
                }
            }""")
            _t.sleep(3)
            print("[CARDS] Purchase State -> Ordered, PO saved.")
        except Exception as e:
            print(f"[CARDS] Couldn't set Purchase State -> Ordered ({e}) -- do it manually.")
        navigate_to_so(page, so_id)
        save_so(page, accept_sor=False)
        print(f"[CARDS] Done (reprint) -- PO for {card_part}")
        return "reprint"

    print(f"\n[CARDS] Card design '{design}' not actionable in the chain -- handle manually.")
    return "none"


def _post_first_touch(page, so_id, res, no_itf):
    """After a no-ITF first-touch, run the full guided provisioning chain
    (customer setup -> location -> payment -> cards -> SO link/config -> final user/intro).
    VAC config files (task 9) are generated inside the chain after End Customer + location
    are linked on the SO."""
    cid = res.get("cust_id") if isinstance(res, dict) else res
    existing = res.get("existing", False) if isinstance(res, dict) else False
    verify_only = res.get("verify_only", False) if isinstance(res, dict) else False
    ref_loc = res.get("ref_location_id", "") if isinstance(res, dict) else ""
    sor = res.get("sor_data") if isinstance(res, dict) else None
    if no_itf and cid:
        _do_provision_chain(page, so_id, cid, existing=existing or verify_only,
                            verify_only=verify_only, ref_location_id=ref_loc, sor=sor)
    if isinstance(res, dict):
        _print_dedup_summary(res)


def _print_dedup_summary(res):
    """End-of-run dedup readout: the verdict, the signals it matched on, and every
    candidate cust id WITH what triggered it (email / phone / last name / business
    name). Prints for new customers so Matt can sanity-check the matcher's reasoning."""
    if res.get("existing") and not res.get("verify_only"):
        return  # existing customers are resolved by the SO's End Customer field, not dedup
    d = res.get("dedup")
    print("\n" + "=" * 60)
    print("  DEDUP SUMMARY (Admin /customers)")
    print("=" * 60)
    if res.get("verify_only"):
        # Replacement/exchange: dedup signals were empty (no contact), so the matcher
        # said NEW -- but we resolved the real customer from the referenced SO instead.
        print(f"  Resolved as REPLACEMENT/EXCHANGE of SO-{res.get('replacement_ref','?')}")
        print(f"  -> reused existing customer {res.get('cust_id','?')} "
              f"(dedup signals were empty; verdict below is informational only).")
    if not d:
        print("  No dedup run this pass (dedup_test off).")
        print("=" * 60)
        return
    sig = d.get("signals", {})
    print(f"  Signals -> name='{sig.get('customer_name','')}' contact='{sig.get('contact_name','')}'"
          f" email='{sig.get('contact_email','')}' phone='{sig.get('contact_phone','')}'")
    print(f"  Verdict: {d.get('verdict','?').upper()}")
    matches = d.get("matches", [])
    if not matches:
        print("  Candidates: none -- treating as a NEW customer.")
    else:
        print(f"  Candidates ({len(matches)}) -- cust id : trigger:")
        for m in matches:
            print(f"    {m.get('cust_id','?'):<8} {m.get('name','')[:34]:<34} "
                  f"[{m.get('strength','')}/{m.get('signal','')}] matched on '{m.get('detail','')}'")
        print("  STRONG = email/phone (near-certain). WEAK = last name / business-name overlap (review).")
    print("=" * 60)


def _do_system(page, so_id, assembly_week=None, dedup_test=True):
    """`system <id>` -- one idempotent, snapshot-driven system reconciler."""
    plan = _do_snapshot(page, so_id)
    if not plan.get("actionable"):
        print("\n[SYSTEM] Snapshot found no automated MOOPS/Admin/Portal work to run.")
        print("[SYSTEM] Stopping before any write/provisioning actions. No save attempted.")
        return
    if plan.get("hard_blocked"):
        print("\n[SYSTEM] Snapshot found actionable work, but also hard blockers:")
        for line in plan["hard_blocked"]:
            print(f"  - {line}")
        print("[SYSTEM] Stopping before write/provisioning actions so it does not run blocked steps.")
        print("[SYSTEM] Use targeted commands or fix the missing inputs, then re-run snapshot/system.")
        return

    if plan.get("effective_customer_id"):
        snap = plan.get("_snapshot", {})
        end_customer = snap.get("end_customer", {})
        cust_id = plan.get("effective_customer_id", "")
        if _can_run_chain_from_snapshot(plan):
            print("\n[SYSTEM] Snapshot found chain-only work; skipping setup reread.")
        else:
            print("\n[SYSTEM] Existing customer identified; running snapshot-driven setup.")
            print("[SYSTEM] This avoids re-reading SOR and avoids clearing/recreating customer/location state.")
        pre_done = _do_existing_customer_setup_from_snapshot(page, so_id, plan, assembly_week=assembly_week)
        print("[SYSTEM] Continuing directly with provisioning/config chain.")
        result = _do_provision_chain(
            page,
            so_id,
            cust_id,
            existing=True,
            verify_only=False,
            ref_location_id=end_customer.get("location_id", ""),
            sor=snap.get("sor_data", {}),
            pre_done=pre_done,
            force_config=bool(plan.get("force_config")),
        )
        _print_system_write_summary(result)
        return

    # No effective customer id -> NEW customer. SAME snapshot-driven path as the existing
    # case: setup (tag/week/parts) -> create the customer from the snapshot -> provision
    # chain. The ONLY difference from the existing branch is the create step. No re-read of
    # the SO/SOR, no legacy first_touch flow.
    snap = plan.get("_snapshot", {})
    so_data = snap.get("so_data", {})
    sor_data = snap.get("sor_data", {})
    print("\n[SYSTEM] New customer -- snapshot-driven setup + create (no re-read, no legacy flow).")
    pre_done = _do_existing_customer_setup_from_snapshot(page, so_id, plan, assembly_week=assembly_week)
    print("\n--- Create Customer (fill only, from snapshot) ---")
    cust_id = _do_create_customer(page, so_id, data={
        "customer_name": sor_data.get("location_name", ""),
        "contact_name": sor_data.get("contact_name", ""),
        "contact_email": sor_data.get("contact_email", ""),
        "contact_phone": sor_data.get("contact_phone", ""),
        "is_route": bool(so_data.get("is_route")),
    })
    try:
        input("\n[SYSTEM] Review the Create Customer form, SAVE it in the browser, then press "
              "Enter to continue (Ctrl+C to stop).")
    except (EOFError, KeyboardInterrupt):
        print("\n[SYSTEM] Stopped before the provisioning chain.")
        return
    result = _do_provision_chain(
        page,
        so_id,
        cust_id,
        existing=False,
        verify_only=False,
        sor=sor_data,
        pre_done=pre_done,
        force_config=bool(plan.get("force_config")),
    )
    _print_system_write_summary(result)
    return


def _print_system_write_summary(result):
    """Print what this system command actually wrote, separate from snapshot state."""
    if not isinstance(result, dict):
        return
    actions = result.get("actions", [])
    print("\n--- System Write Summary ---")
    if actions:
        for action in actions:
            print(f"DID:  {action}")
    else:
        print("DID:  no write actions in the provisioning chain")


def _can_run_chain_from_snapshot(plan):
    """True when snapshot reads are enough and setup would only repeat work."""
    if not plan.get("effective_customer_id"):
        return False
    setup_prefixes = (
        "Tag missing",
        "Assembly week missing",
        "Task 1 ",
        "Route task checklist",
    )
    for action in plan.get("actionable", []):
        if action.startswith(setup_prefixes):
            return False
    return True


def _do_existing_customer_setup_from_snapshot(page, so_id, plan, assembly_week=None):
    """Run setup work that does not require customer creation, using snapshot reads."""
    snap = plan.get("_snapshot", {})
    so_data = snap.get("so_data", {}) or {}
    sor_data = snap.get("sor_data", {}) or {}
    tasks = snap.get("tasks", {}) or {}
    done = {}
    changed = False

    needs_tag = not (so_data.get("tag") or "").strip()
    needs_week = not (so_data.get("assembly_week") or "").strip()
    task1_open = tasks.get(1, {}).get("status") != "Completed"

    if not (needs_tag or needs_week or task1_open):
        return done

    print("\n" + "=" * 60)
    print(f"  EXISTING-CUSTOMER SETUP -- SO-{so_id}")
    print("=" * 60)
    navigate_to_so(page, so_id)

    if needs_tag:
        print("\n--- Setup: Set tag ---")
        tag_value = build_tag(so_data.get("products", []), so_data.get("customer_name", ""))
        print(f"Tag: {tag_value}")
        action_set_tag(page, tag_value)
        changed = True
    else:
        print(f"\n--- Setup: Tag already set ({so_data.get('tag')}) -- skip ---")

    if needs_week:
        print("\n--- Setup: Set assembly week ---")
        chosen_week = assembly_week
        chosen_label = assembly_week
        pick_reason = "Manual override via --assembly-week" if assembly_week else ""
        if not chosen_week:
            from core.schedule import calculate_order_weight, pick_assembly_week, planned_week_for_sor
            sor_id = ""
            m = _re.search(r"/order-requests/(\d+)", sor_data.get("sor_url", ""))
            if m:
                sor_id = m.group(1)
            planned = planned_week_for_sor(sor_id)
            if planned:
                chosen_week = planned
                chosen_label = planned
                pick_reason = f"From intake plan (SOR-{sor_id}) -- schedule not re-read"
            else:
                order_weight = calculate_order_weight(so_data.get("products", []))
                schedule = read_schedule_capacity(page)
                print_schedule(schedule)
                chosen_week, chosen_label, pick_reason = pick_assembly_week(
                    schedule,
                    required_date=sor_data.get("required_date", ""),
                    is_expedited=bool(sor_data.get("is_expedited")),
                    order_weight=order_weight,
                )
        if chosen_week:
            print(f">> PICKED: {chosen_label} ({chosen_week})")
            print(f"   Reason: {pick_reason}")
            action_set_assembly_week(page, chosen_week)
            changed = True
        else:
            print(f">> COULD NOT AUTO-PICK ASSEMBLY WEEK: {pick_reason or 'no week returned'}")
    else:
        print(f"\n--- Setup: Assembly week already set ({so_data.get('assembly_week')}) -- skip ---")

    if task1_open:
        print("\n--- Setup: Add missing hardware companion parts ---")
        added = action_add_required_parts(
            page,
            processor_type=sor_data.get("processor_type", ""),
            is_route=bool(so_data.get("is_route")),
        ) or []
        if added:
            changed = True
        done[1] = "Completed"
    else:
        print("\n--- Setup: Hardware verified already Completed -- skip ---")

    if plan.get("effective_customer_id"):
        done[2] = "Completed"
    if _card_type(sor_data.get("card_design_type", "")) == "none":
        for n in (3, 4, 5):
            if tasks.get(n, {}).get("status") == "To Do":
                done[n] = "N/A"

    if changed:
        print("\n--- Setup: Save SO ---")
        save_so(page, accept_sor=False, clear_customer_location_blocker=False)
    else:
        print("\n--- Setup: No SO field/part changes to save ---")

    return done


def _apply_config_attachment_signal(plan, so_data, tasks, end_customer, attached_configs):
    """Make config actionable when the SO is linked but expected .cfg files are missing."""
    expected_config_count = sum(
        int(p.get("qty", 0) or 0)
        for p in so_data.get("products", [])
        if (p.get("part_number", "") or "").upper().startswith("VAC")
    )
    attached_configs = attached_configs or []
    plan["config_files"] = {
        "expected": expected_config_count,
        "attached": len(attached_configs),
        "names": attached_configs,
    }
    customer_id = end_customer.get("id") or plan.get("effective_customer_id")
    config_short = (
        expected_config_count
        and customer_id
        and len(attached_configs) < expected_config_count
    )
    if config_short:
        plan["force_config"] = True
        plan["skip"] = [
            line for line in plan["skip"]
            if line not in (
                "Task 9 Completed -> skip VAC config files",
                "Task 9 Completed -> skip SO End Customer/config workflow",
            )
        ]
        if not any("Task 9" in line and "config" in line.lower() for line in plan["actionable"]):
            plan["actionable"].append(
                "Task 9 config workflow needed -> link SO End Customer/location if needed, then upload VAC config files"
            )
        if not any(item.get("step") == "Task 9 config" for item in plan["inputs"]):
            plan["inputs"].append({
                "step": "Task 9 config",
                "ready": True,
                "detail": f"customer identified; attached_cfg={len(attached_configs)}/{expected_config_count}",
            })
    return plan


def _do_snapshot(page, so_id):
    """Read-only state summary for deciding what an optimized rerun should do.

    This intentionally does not call any action/fill/save functions. It is the
    safety bridge toward a checklist-driven reconciler.
    """
    from core.moops import read_config_file_resources, read_so_end_customer

    print("\n" + "=" * 60)
    print(f"  SNAPSHOT / PLAN -- SO-{so_id} (READ ONLY)")
    print("=" * 60)

    metrics = []

    def timed_read(label, fn):
        t0 = time.perf_counter()
        value = fn()
        metrics.append((label, time.perf_counter() - t0))
        return value

    snapshot_start = time.perf_counter()
    so_data = timed_read("read_so", lambda: first_touch.read_so(page, so_id))
    sor_data = timed_read("read_sor_data", lambda: read_sor_data(page))
    tasks = timed_read("read_task_states", lambda: read_task_states(page))
    end_customer = timed_read("task9_read_so_end_customer", lambda: read_so_end_customer(page))
    plan_start = time.perf_counter()

    plan = build_system_rerun_plan(so_data, sor_data, tasks, end_customer)
    attached_configs = timed_read("task9_read_config_files", lambda: read_config_file_resources(page))
    plan = _apply_config_attachment_signal(plan, so_data, tasks, end_customer, attached_configs)
    plan["_snapshot"] = {
        "so_data": so_data,
        "sor_data": sor_data,
        "tasks": tasks,
        "end_customer": end_customer,
    }
    metrics.append(("build_plan", time.perf_counter() - plan_start))
    card_type = plan["card_type"]
    is_route = plan["is_route"]

    print("\n--- SOR Signals ---")
    print(f"Processor: {sor_data.get('processor_type', '') or '(Stripe default)'}")
    req = sor_data.get("required_date", "")
    if req:
        exp = " EXPEDITED" if sor_data.get("is_expedited") else ""
        print(f"Required date: {req}{exp}")
    print(f"Card design: {sor_data.get('card_design_type', '') or '(none)'} -> {card_type}")
    print(f"Order type: {'Route' if is_route else 'System'}")
    print(f"Contact: {sor_data.get('contact_name', '') or '(blank)'} / "
          f"{sor_data.get('contact_email', '') or '(blank)'}")
    print(f"Effective Customer: {plan.get('effective_customer_id') or '(not found)'} "
          f"{plan.get('effective_customer_name') or ''}".rstrip())

    print("\n--- Task States ---")
    for n in sorted(tasks):
        t = tasks[n]
        print(f"Task {n:2d}: {t.get('status', ''):10s} {t.get('label', '')}")

    print("\n--- Optimized Rerun Plan ---")
    for line in plan["skip"]:
        print(f"SKIP: {line}")
    for line in plan["actionable"]:
        print(f"DO:   {line}")
    for line in plan["blocked"]:
        print(f"WAIT: {line}")
    if plan["blocked"] and plan["actionable"] and not plan.get("hard_blocked"):
        print("NOTE: WAIT items above are same-run dependencies; system can continue after the DO steps.")
    if not plan["actionable"]:
        print("RESULT: no automated MOOPS/Admin/Portal actions should run.")
    if plan.get("inputs"):
        print("\n--- Required Inputs ---")
        for item in plan["inputs"]:
            detail = item.get("detail", "")
            same_run_wait = (
                not item.get("ready")
                and "waiting on task 8 location" in detail.lower()
                and not plan.get("hard_blocked")
            )
            status = "READY TO RUN" if item.get("ready") else "WAITING SAME RUN" if same_run_wait else "BLOCKED"
            print(f"{status}: {item.get('step')} - {item.get('detail')}")
    metrics.append(("snapshot_total", time.perf_counter() - snapshot_start))
    print("\n--- Timing ---")
    for label, elapsed in metrics:
        print(f"{label}: {elapsed:.1f}s")
    print("=" * 60)
    return plan


def _do_config_files(page, so_id):
    """Task 9: download each VAC's config (.cfg, sequential VAC0n.cfg) and upload them to the
    SO's File Resources. Saved under vac_configs/SO<id>/ next to run.py. Returns True if any
    file was uploaded."""
    import os
    from pathlib import Path
    from core.moops import download_vac_configs, read_config_file_resources, upload_files_to_so, save_so
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vac_configs", f"SO{so_id}")
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    for old in list(out_path.glob("*.cfg")) + list(out_path.glob("_tmp.cfg")):
        try:
            old.unlink()
        except Exception as e:
            print(f"[CONFIG] Could not remove stale local config {old.name} ({e})")
    navigate_to_so(page, so_id)
    before = read_config_file_resources(page)
    print(f"[CONFIG] File Resources before config run: {len(before)} .cfg file(s)")
    print("[CONFIG] Downloading fresh config files from the current SO VAC rows.")
    paths = download_vac_configs(page, so_id, out_dir)
    if not paths:
        return False
    upload_files_to_so(page, paths)
    # The .cfg attachments don't persist or show in File Resources until the SO is saved.
    # Save ONCE for all the files (not one save per file), THEN verify against what's
    # actually on the SO (Matt: "need to save before you can verify those").
    save_so(page, accept_sor=False, clear_customer_location_blocker=False)
    after = read_config_file_resources(page)
    print(f"[CONFIG] File Resources after save: {len(after)} .cfg file(s)")
    verified = len(after) > len(before)
    if not verified:
        print("[CONFIG] Upload NOT confirmed in File Resources after save (count did not "
              "increase) -- task 9 stays To Do; re-attach or re-run.")
    return verified


def _addr_norm(value):
    return _re.sub(r'[^a-z0-9]+', '', (value or "").lower())


def _location_match_score(sor_addr, portal_loc):
    """Pure-ish score for matching a SOR address to a Portal location read."""
    target = _parse_address(sor_addr or "")
    portal_addr = portal_loc.get("address", "")
    parsed_portal = _parse_address(portal_addr)
    street = _addr_norm(target.get("street", ""))
    city = _addr_norm(target.get("city", ""))
    state = (target.get("state", "") or "").upper()
    zip_code = target.get("zip", "")

    p_street = _addr_norm(parsed_portal.get("street", "") or portal_addr)
    p_city = _addr_norm(portal_loc.get("city", "") or parsed_portal.get("city", ""))
    p_state = (portal_loc.get("state", "") or parsed_portal.get("state", "")).upper()
    p_zip = portal_loc.get("zip", "") or parsed_portal.get("zip", "")

    score = 0
    if zip_code and p_zip and zip_code == p_zip:
        score += 2
    if street and p_street and (street in p_street or p_street in street):
        score += 5
    if city and p_city and city == p_city:
        score += 1
    if state and p_state and state == p_state:
        score += 1
    return score


def _resolve_existing_location_id(page, cust_id, sor):
    """Find a previously-created Portal location ID for this SOR address.

    Used when task 8 is already Completed but the SO End Customer/location was not
    linked. Read-only unless it prompts the operator for a location id fallback.
    """
    loc_addr = (sor or {}).get("location_address", "")
    if not cust_id or not loc_addr:
        return ""
    try:
        from core import portal as _portal
        rows = _portal.read_portal_location_index(page, cust_id)
        matches = []
        for row in rows:
            data = {"address": row.get("address", ""), "city": "", "state": "", "zip": ""}
            score = _location_match_score(loc_addr, data)
            if score >= 6:
                matches.append((score, row.get("location_id", ""), row))
        if len(matches) == 1:
            score, loc_id, data = matches[0]
            print(f"[CHAIN] Matched existing Portal location {loc_id} "
                  f"({data.get('address', '')}) score={score}.")
            return loc_id
        if len(matches) > 1:
            print("[CHAIN] Multiple Portal locations matched the SOR address; not guessing:")
            for score, loc_id, data in matches:
                print(f"  {loc_id}: {data.get('address', '')} score={score}")
        else:
            print("[CHAIN] Could not match a Portal location to the SOR address.")
    except Exception as e:
        print(f"[CHAIN] Existing location lookup failed ({e}).")
        print(f"[CHAIN] Make sure LaundroPortal is signed in and can open customer {cust_id}.")
    try:
        return input("[CHAIN] Paste the existing Location ID to link on the SO "
                     "(blank = skip config this run): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n[CHAIN] No location id provided.")
        return ""


def _existing_chain_done_statuses(did_stripe=False, did_location=False, did_config=False,
                                  card_result="none"):
    """Checklist delta for existing/replacement chain runs."""
    done = {}
    if did_stripe:
        done[7] = "Completed"
    if did_location:
        done[8] = "Completed"
    if did_config:
        done[9] = "Completed"
    if card_result == "new":
        done[3], done[4], done[5] = "Completed", "To Do", "To Do"
    elif card_result == "reprint":
        done[3], done[4], done[5] = "N/A", "N/A", "Completed"
    return done


def _do_provision_chain(page, so_id, cust_id, existing=False, verify_only=False,
                        ref_location_id="", sor=None, pre_done=None, force_config=False):
    """Guided no-ITF provisioning chain -- ONE flow, three flavors (reuses every step):
      NEW      : API user + Stripe-feature fill -> location(0100001) -> stripe -> SO link/config -> user/intro
      EXISTING : verify cust page (check only) -> location(next 01/02) -> stripe -> SO link/config -> [skip user/intro]
      VERIFY   : replacement/exchange -- customer + location already exist. Check only,
                 add NOTHING (no location, no Stripe init, no user/intro); just link the
                 existing End Customer on the SO + set tasks.
    Fill-only steps never submit; the human Saves at each pause."""
    def _pause(msg):
        try:
            input(msg)
            return True
        except (EOFError, KeyboardInterrupt):
            print("\n[CHAIN] Stopped by user.")
            return False

    from core.moops import read_task_states, read_so_end_customer, set_task_checklist

    flavor = ("VERIFY (replacement/exchange)" if verify_only
              else "EXISTING customer" if existing else "NEW customer")
    print("\n" + "=" * 60)
    print(f"  PROVISIONING CHAIN ({flavor}) -- customer {cust_id} / SO {so_id}")
    print("=" * 60)

    # Task-driven: run ONLY the steps whose task is still To Do. Never re-run Completed
    # work, never blanket-reset the checklist. Task map: 7=payment/Stripe, 8=location,
    # 10=portal user+intro, 3/4/5=card.
    navigate_to_so(page, so_id)
    tasks = read_task_states(page)
    todo = {n for n, t in tasks.items() if t.get("status") == "To Do"}
    print(f"  Tasks To Do: {sorted(todo) or 'none'} -- running only those.")

    # ONE-PASS read while we're already on the SO: VAC seat count + internal notes.
    # Merge notes with the threaded SOR so Location and User steps NEVER navigate back.
    from core.moops import read_products as _read_products, read_internal_notes as _read_notes
    try:
        vac_seats = sum(int(p.get("qty", 0) or 0) for p in _read_products(page)
                        if (p.get("part_number", "") or "").upper().startswith("VAC"))
    except Exception:
        vac_seats = 0
    _notes = _read_notes(page)
    # Build merged SOR: notes first (written by first_touch), SOR fallback.
    # This is the single authoritative contact+address dict for the entire chain.
    _merged_sor = dict(sor) if sor else {}
    for _k in ("contact_name", "contact_email", "contact_phone",
               "location_name", "location_address"):
        _merged_sor[_k] = (_notes.get(_k) or _merged_sor.get(_k) or "")
    # Log what we resolved so Matt can verify without a second read.
    _cn = _merged_sor.get("contact_name", "")
    _ce = _merged_sor.get("contact_email", "")
    _la = _merged_sor.get("location_address", "")
    print(f"  [CHAIN] Contact: {_cn or '(blank)'} / {_ce or '(blank)'} | "
          f"Location addr: {(_la[:40] + '...') if len(_la) > 40 else _la or '(blank)'}")
    sor = _merged_sor   # replace sor with the merged dict for all downstream steps

    loc_id = ref_location_id   # for the End Customer link; set by the location step if it runs
    loc_key = ""
    # Per-workflow completion flags -> used to check off the matching task at the end
    # (Matt's rule: each workflow maps to a task; if the chain completes it, mark it Completed).
    card_result = "none"   # "new" (task 3) | "reprint" (task 5) | "none"
    did_location = False
    did_stripe = False
    did_user_intro = False
    did_config = False
    verified_existing_location = False
    pre_done = dict(pre_done or {})
    write_actions = []

    # Customer page (API user + Stripe feature) -- prereq for payment(7)/user(10).
    if not verify_only and ({7, 10} & todo):
        filled = False
        if existing:
            # Existing customers are live accounts. Check only; do not change customer-level
            # Admin settings from a system rerun just because task 7/10 is still open.
            print("\n--- Customer page (check) ---")
            chk = provisioning.check_customer_setup(page, cust_id)
            if chk is not None and (not chk.get("pos") or not chk.get("stripe")):
                print("[CHAIN] Customer setup check found missing POS/Stripe feature flags.")
                print("[CHAIN] Not changing existing customer settings in this run; use targeted apiuser if needed.")
        else:
            print("\n--- API user (POS) + Stripe feature (Admin) ---")
            provisioning.fill_api_user(page, cust_id)
            filled = True
        # The green LOGIN button is an <a target="_blank" href="/portal/<cust_id>"> -- clicking
        # it opens LaundroPortal in a NEW TAB that Playwright isn't driving, so we don't click
        # it; we bridge to the same href via login_to_portal() (same call the dedup lookups and
        # fill_location use). If we filled the customer, the human must SAVE first so the API
        # user / Stripe feature persists; then we open LP automatically -- no manual click.
        if filled:
            if not _pause("\n[CHAIN] SAVE the customer, then press Enter (I'll open LaundroPortal)..."):
                return
        else:
            print("[CHAIN] Nothing to save on the customer page -- opening LaundroPortal.")
        portal.login_to_portal(page, cust_id)
    else:
        print("\n--- Customer page: skip (tasks 7 & 10 done / replacement) ---")

    # Location (task 8). Skip if done or replacement (location already exists).
    if 8 in todo and not verify_only:
        print("\n--- Add Location (LaundroPortal) ---")
        if existing:
            print("[CHAIN] Existing customer: checking Portal location index before creating anything.")
            matched_loc_id = _resolve_existing_location_id(page, cust_id, sor)
            if matched_loc_id:
                loc_id = matched_loc_id
                verified_existing_location = True
                print(f"[CHAIN] Existing location {loc_id} will be linked on the SO; no new location created.")
                write_actions.append(f"verified existing Portal location {loc_id}")
            else:
                print("[CHAIN] No existing location selected.")
                try:
                    create_ok = input("[CHAIN] Type CREATE to make a new Portal location, "
                                      "or press Enter to stop this run: ").strip().upper()
                except (EOFError, KeyboardInterrupt):
                    create_ok = ""
                if create_ok != "CREATE":
                    print("[CHAIN] Stopping before location creation. No new Portal location made.")
                    return
                print("[CHAIN] CREATE confirmed -- continuing to create a new Portal location.")
                acc = (sor.get("access_sharing", "") if sor else "").strip().lower()
                shared = False if acc.startswith("no") else True
                if acc:
                    print(f"[CHAIN] Access Sharing = {acc!r} -> {'01 (grouped)' if shared else '02 (new group)'} series")
                else:
                    print("[CHAIN] Access Sharing not read -- defaulting to 01 (grouped) series; VERIFY.")
                loc_id = provisioning.next_location_id(page, cust_id, shared=shared)
        else:
            loc_id = "0100001"
        if not verified_existing_location:
            _do_addloc(page, so_id, cust_id, location_id=loc_id, sor=sor, seats=vac_seats)
            if not _pause("\n[CHAIN] Save the location, then press Enter (I'll read the Location Key)..."):
                return
            for _ in range(12):  # Location_Key lands in the URL after save (maybe on the LP tab)
                for p in [page] + list(page.context.pages):
                    try:
                        mm = _re.search(r'Location_Key=(\d+)', p.url or "")
                    except Exception:
                        mm = None
                    if mm:
                        loc_key = mm.group(1)
                        break
                if loc_key:
                    break
                page.wait_for_timeout(500)
            if loc_key:
                print(f"[CHAIN] Location Key = {loc_key}")
            else:
                loc_key = input("[CHAIN] Couldn't read Location Key from any tab -- paste it: ").strip()
            did_location = bool(loc_key)  # a Location Key only exists once the location is saved
            if did_location:
                write_actions.append(f"created/saved Portal location {loc_id} (Location_Key {loc_key})")
    else:
        print("\n--- Add Location: skip (task 8 done / replacement) ---")

    # Stripe (task 7) -- ONLY for Stripe processors. Fortis/EBT orders (KIT-A35) run on
    # Fortis, not a Stripe merchant, so skip Stripe entirely for them. Needs a location key.
    pt = (sor.get("processor_type", "") if sor else "").upper()
    is_fortis = ("FORTIS" in pt) or ("EBT" in pt) or pt.strip() == "2"
    if 7 in todo and not verify_only and not is_fortis:
        print("\n--- Stripe (LaundroPortal) ---")
        if loc_key:
            did_stripe = bool(provisioning.open_stripe(page, cust_id, loc_key))
        elif verified_existing_location:
            print("[CHAIN] Existing location matched, but no Location_Key was opened this run.")
            print("[CHAIN] Not creating or reinitializing Stripe from task state alone; verify payment with targeted Stripe/payment check.")
        else:
            print(f"[CHAIN] No location key -- run `stripe {cust_id} <key>` after the location is saved.")
    elif is_fortis:
        print(f"\n--- Stripe: SKIP (Fortis/EBT processor {pt!r} -- payment processing is Fortis, not Stripe) ---")
    else:
        print("\n--- Stripe: skip (task 7 done / replacement) ---")

    # Cards (tasks 3/4/5). _do_cards runs the right card lane and reports which: "new"
    # (design email -> task 3), "reprint" (PO sent -> task 5), or "none".
    card_kind = _card_type((sor or {}).get("card_design_type", ""))
    # The card is CREATED/CLONED at task 3 (new/modify design) or the PO is cut at task 5
    # (reprint). Gate the clone on THAT task being To Do -- once it is Completed the card
    # already exists, and tasks 4/5 (approval / PO look-back) must NOT trigger another clone.
    # Firing on any of {3,4,5} is what bumped CARD-MD-Xn -> Xn+1 on every re-run.
    card_design_todo = (3 in todo) if card_kind in ("new", "modify") else (5 in todo)
    if card_kind != "none" and card_design_todo:
        print("\n--- Cards ---")
        card_result = _do_cards(page, so_id, cust_id, sor=sor)
    elif ({3, 4, 5} & todo) and card_kind == "none":
        print("\n--- Cards: skip (SOR has no actionable card design type) ---")
        for n in (3, 4, 5):
            if n in todo:
                pre_done[n] = "N/A"
    else:
        print("\n--- Cards: skip (card already made / tasks done / N-A) ---")

    # End Customer on the SO -- MUST happen before config: MOOPS uses the linked
    # customer + location to populate CustomerKey and LocationID in the .cfg file.
    navigate_to_so(page, so_id)
    fc = read_so_end_customer(page)
    if fc.get("id"):
        print(f"\n--- End Customer: already linked ({fc['id']}) -- skip ---")
    elif not loc_id and cust_id and 8 not in todo:
        print("\n--- End Customer on SO: locating existing Portal location ---")
        loc_id = _resolve_existing_location_id(page, cust_id, sor)
        navigate_to_so(page, so_id)
        if loc_id:
            set_so_end_customer(page, cust_id, location_id=loc_id, save=True)
            fc = read_so_end_customer(page)
            write_actions.append(f"linked SO End Customer {cust_id} / location {loc_id}")
        else:
            print("[CHAIN] No location id available -- skipping SO link/config this run.")
            print("[CHAIN] Sign in to Admin Portal/LaundroPortal or paste the existing Location ID, "
                  "then rerun `s {}`.".format(so_id))
            return
    else:
        print("\n--- End Customer on SO ---")
        set_so_end_customer(page, cust_id, location_id=loc_id, save=True)
        fc = read_so_end_customer(page)  # re-read to confirm it took
        write_actions.append(f"linked SO End Customer {cust_id} / location {loc_id or '(blank)'}")
    if verified_existing_location and fc.get("id") and loc_id:
        did_location = True
        if 10 in todo:
            pre_done[10] = "Completed"

    # VAC config files (task 9). REQUIRES End Customer + Location linked on the SO --
    # MOOPS uses them to populate CustomerKey and LocationID in the .cfg.
    # Skip and flag if End Customer isn't set yet (e.g. dealer link pending).
    if 9 in todo or force_config:
        if fc.get("id"):
            label = "task 9" if 9 in todo else "config recovery"
            print(f"\n--- VAC config files ({label}) ---")
            did_config = bool(_do_config_files(page, so_id))
            if did_config:
                write_actions.append("downloaded fresh VAC config files and uploaded them to File Resources")
            if not did_config:
                try:
                    ans = input("[CHAIN] Config upload was not confirmed automatically. "
                                "If you verified the config files are attached, type y to mark task 9 complete: ")
                except (EOFError, KeyboardInterrupt):
                    ans = ""
                did_config = ans.strip().lower() in ("y", "yes")
                if did_config:
                    write_actions.append("operator confirmed VAC config files are attached")
        else:
            print("\n--- VAC config files (task 9): SKIPPED -- End Customer not set on SO. ---")
            print(f"[FLAG] Link End Customer {cust_id} / location {loc_id or '?'} on the SO first,")
            print(f"       then run `s {so_id}` again (task 9 still To Do -- chain will retry).")
    else:
        print("\n--- Config files: skip (task 9 done / N-A) ---")

    # Admin Portal user + intro email (task 10) -- final customer-facing step.
    # Skip for existing customers -- they already have LP users. For a never-provisioned
    # existing customer, run `adduser <so> <cust>` as a targeted command.
    if 10 in todo and not existing and not verify_only:
        print("\n--- Add User (LaundroPortal) ---")
        _do_adduser(page, so_id, cust_id, sor=sor)
        if not _pause("\n[CHAIN] >>> SAVE the user in LaundroPortal FIRST (it must appear in the list), "
                      "then press Enter for the intro email..."):
            return
        print("\n--- Intro email (Admin) ---")
        provisioning.send_intro_email(page, cust_id)
        did_user_intro = True
    else:
        print("\n--- User + intro: skip (task 10 done / existing / replacement) ---")

    # Task checklist.
    # NEW customer (first pass) -> set the full baseline map (action_set_system_tasks).
    # EXISTING/replacement -> CHECK OFF each task whose workflow the chain actually completed
    # (Matt's rule: every workflow maps to a task; do it -> mark it Completed). We only set the
    # ones we finished and NEVER blanket-reset, so previously-Completed tasks aren't un-done.
    if not existing and not verify_only:
        print("\n--- Task checklist (baseline + completed workflows) ---")
        navigate_to_so(page, so_id)
        action_set_system_tasks(page)
        # action_set_system_tasks writes the static first-run map (7/8/9/10 = To Do). Override
        # the ones the chain actually completed THIS run so the checklist reflects reality
        # (Matt: it wasn't finishing the checklist even though location/stripe/user/config ran).
        done = {}
        if did_stripe:     done[7] = "Completed"   # payment processing / Stripe
        if did_location:   done[8] = "Completed"   # location added to Portal
        if did_config:     done[9] = "Completed"   # VAC config files uploaded
        if did_user_intro: done[10] = "Completed"  # portal user + intro email
        if done:
            print(f"  Marking completed workflows: {sorted(done)}")
            set_task_checklist(page, done)
            write_actions.append(f"updated task checklist {sorted(done)}")
        save_so(page, accept_sor=False)
        write_actions.append("saved SO after checklist update")
    else:
        # Existing/replacement reruns should touch only workflows completed by THIS pass,
        # plus narrow card-state outcomes. Do not re-write already completed tasks just
        # because they were prerequisites for this rerun.
        done = _existing_chain_done_statuses(
            did_stripe=did_stripe,
            did_location=did_location,
            did_config=did_config,
            card_result=card_result,
        )
        done.update(pre_done)
        # Cards decision tree -> task states:
        #   no card        -> 3/4/5 N/A
        #   new design     -> 3 Completed (email), 4 To Do (approval), 5 To Do (PO after)
        #   reprint        -> 3 N/A, 4 N/A (existing design already approved), 5 Completed (PO sent)
        if card_result == "exists":
            pass  # card made on a prior touch -- leave 3/4/5 as they were set then
        print(f"\n--- Task checklist (checking off completed workflows: {sorted(done) or 'none'}) ---")
        if done:
            navigate_to_so(page, so_id)
            set_task_checklist(page, done)
            save_so(page, accept_sor=False)
            write_actions.append(f"updated task checklist {sorted(done)} and saved SO")
        else:
            print("[CHAIN] No checklist changes from this pass -- no final SO save.")

    print("\n" + "=" * 60)
    print(f"  CHAIN COMPLETE ({flavor}) for {cust_id} -- ran To Do tasks {sorted(todo) or 'none'}.")
    if not existing and not verify_only:
        print(f"  TODO (manual): add {cust_id} to the dealer record.")
        print("  The SO End Customer search may not show this customer until that dealer link exists.")
    print("=" * 60)
    return {
        "actions": write_actions,
        "done": done if 'done' in locals() else {},
        "did_config": did_config,
        "did_location": did_location,
        "did_stripe": did_stripe,
    }


def _do_custid(page, so_id, cust_id=None):
    """Standalone Cust ID workflow (Admin only), broken out for focused testing:
    Create Customer fill -> you Save -> API user + Stripe-feature finalize -> you Save.
    Does NOT run the LP chain. Prints the next command to continue."""
    print("\n" + "=" * 60)
    print(f"  CUST ID WORKFLOW -- SO {so_id}")
    print("=" * 60)
    _do_create_customer(page, so_id, cust_id, preview=False)
    try:
        entered = input("\n[CUSTID] SAVE the Create Customer form in the browser, then type the saved "
                        "Cust ID here and press Enter (blank = use the suggested one): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n[CUSTID] Stopped."); return
    cid = entered or cust_id
    if not cid:
        print("[CUSTID] No cust id captured -- once you know it, run `apiuser <cust>`.")
        return
    provisioning.fill_api_user(page, cid)
    try:
        input("\n[CUSTID] SAVE the customer again (API user + Stripe feature), then press Enter to finish.")
    except (EOFError, KeyboardInterrupt):
        print("\n[CUSTID] Stopped."); return
    print(f"\n[CUSTID] Done -- customer {cid}. Continue with: provision {so_id} {cid}")


def _do_dedup_test(page, query):
    """Dev: scrape Admin /customers, match a single signal (email/phone/name) in
    isolation, and print the candidates. Validates the scrape + matcher before
    they're wired into the intake loop. Shared by CLI dispatch and the console."""
    customers = portal.scrape_admin_customers(page)
    q = (query or "").strip()
    order = {"customer_name": "", "contact_name": "",
             "contact_email": "", "contact_phone": ""}
    if "@" in q:
        order["contact_email"], kind = q, "email"
    elif dedup.normalize_phone(q):
        order["contact_phone"], kind = q, "phone"
    else:
        # plain text: test it as BOTH a contact name and a laundromat name
        order["contact_name"] = order["customer_name"] = q
        kind = "name"
    res = dedup.match_customer(order, customers)
    print(f"\n[dedup] query as {kind}: {q!r}")
    print(f"[dedup] verdict: {res['verdict'].upper()}  ({len(res['matches'])} candidate(s))")
    _print_candidates(page, res["matches"])


def _state_of(text):
    """Pull a US state code out of an address / 'City, ST' string (comma-anchored)."""
    for tok in _re.findall(r',\s*([A-Za-z]{2})\b', text or ""):
        if tok.upper() in _US_STATE_CODES:
            return tok.upper()
    return ""


def _print_candidates(page, matches, order_loc=""):
    """Print dedup candidates with their OWN contact + LaundroPortal city/state, and
    flag a different state -- a strong 'probably not the same customer' signal. The
    city/state is a read-only LP lookup per candidate (cached)."""
    from core import portal
    order_state = _state_of(order_loc)
    if order_state:
        print(f"  (order is in {order_state} — different-state candidates are likely NOT a match)")
    if not matches:
        print("  (no candidates -- would be treated as a NEW customer)")
        return
    for m in matches:
        loc = portal.customer_location_summary(page, m["cust_id"])
        cand_state = _state_of(loc)
        flag = "   <-- DIFFERENT STATE" if (order_state and cand_state and order_state != cand_state) else ""
        print(f"  {m['strength']:6s} {m['signal']:9s} {m['cust_id']:>6}  {m['name'][:34]:34}")
        print(f"           contact: {m.get('contact_name') or '-'} | "
              f"{m.get('contact_email') or '-'} | {m.get('contact_phone') or '-'}")
        print(f"           location: {loc or '(none found)'}{flag}   "
              f"[matched {m['signal']} on '{m.get('detail','')}']")


def _do_dedup_sor(page, sor_id):
    """Read a RAW SOR (order request) like an order -- pull its contact + location --
    and run the full dedup matcher against Admin /customers. For testing the SOR-driven
    dedup path before any SO exists."""
    from core.moops import read_sor_contact
    f = read_sor_contact(page, sor_id)

    # Authoritative: the SOR's "Existing End Customer" field is a human-asserted link to an
    # existing customer ("Swift Wash (01435)"). On existing-customer SORs the New Contact
    # fields are blank, so trust this over name/contact matching -- short-circuit when set.
    eec = (f.get("existing_end_customer", "") or "").strip()
    if eec:
        m_id = _re.search(r'\((\d{3,6})\)', eec)
        eec_name = eec[:m_id.start()].strip() if m_id else eec
        eec_id = m_id.group(1) if m_id else ""
        print(f"\n[dedup-sor {sor_id}] Existing End Customer on SOR: '{eec_name}'"
              + (f" ({eec_id})" if eec_id else ""))
        if eec_id:
            print(f"[dedup-sor] verdict: EXISTING  {eec_id}  (authoritative -- from SOR "
                  "Existing End Customer field)")
            return
        print("[dedup-sor] Existing End Customer set but no id parsed -- falling back to matcher.")

    order = {
        "customer_name": f.get("location_name", ""),
        "contact_name": f.get("contact_name", ""),
        "contact_email": f.get("contact_email", ""),
        "contact_phone": f.get("contact_phone", ""),
    }
    print(f"\n[dedup-sor {sor_id}] signals -> name='{order['customer_name']}' "
          f"contact='{order['contact_name']}' email='{order['contact_email']}' "
          f"phone='{order['contact_phone']}'")
    if not any(order.values()):
        print("[dedup-sor] No contact/location read from the SOR -- nothing to match on. "
              "(Did the SOR render? It's Angular -- try once more.)")
        return
    customers = portal.scrape_admin_customers(page, use_cache=True)
    res = dedup.match_customer(order, customers)
    print(f"[dedup-sor] verdict: {res['verdict'].upper()}  ({len(res['matches'])} candidate(s))")
    _print_candidates(page, res["matches"], order_loc=f.get("location_address", ""))


def _print_task_states(tasks):
    """Pretty-print a task-state dict (shared by the console `tasks` verb)."""
    print("\n--- Task Checklist ---")
    todo = []
    for num, info in tasks.items():
        status, label = info["status"], info["label"]
        mark = "✓" if status == "Completed" else ("-" if status == "N/A" else "○")
        if status not in ("Completed", "N/A"):
            todo.append(num)
        print(f"  Task {num:2d}: {mark} {status:12s}  {label}")
    if todo:
        print(f"Remaining: tasks {', '.join(str(t) for t in todo)}")


def _do_itf(page, so_id):
    """Open the IT provisioning (ITF) Jira form for an SO -- fill from notes, no submit."""
    so_data = read_so(page, so_id)
    notes_data = read_internal_notes(page)
    existing_cust = read_existing_customer_id(page)
    vac_count = sum(int(p["qty"]) for p in so_data["products"]
                    if p["part_number"].upper().startswith("VAC") and str(p["qty"]).isdigit())
    sor_data = read_sor_data(page)
    open_itf_form(page, so_id, notes_data, vac_count,
                  existing_customer=existing_cust if existing_cust else None,
                  card_design_type=notes_data.get("card_design_type", ""),
                  processor_type=sor_data.get("processor_type", ""))


def _console_exec(page, args):
    """Run one parsed command in the persistent console (common verbs only)."""
    if hasattr(sys.stdout, "reset_log"):
        sys.stdout.reset_log()  # fresh run.log per command (latest run only)
    if args.recopy:
        from core import efs
        efs.recopy_last_snippet()
    elif args.inspect_form:
        provisioning.inspect_form(page, args.inspect_form)
    elif args.sf_search is not None:
        provisioning.inspect_sf_search(page, args.sf_search)
    elif args.inspect_lp is not None:
        portal.login_to_portal(page, args.inspect_lp[0])
        provisioning.inspect_form(page, args.inspect_lp[1])
    elif args.inspect_pp is not None:
        provisioning.inspect_payment(page, args.inspect_pp)
    elif args.stripe is not None:
        provisioning.open_stripe(page, args.stripe[0], args.stripe[1])
    elif args.api_user is not None:
        provisioning.fill_api_user(page, args.api_user)
    elif args.intro is not None:
        provisioning.send_intro_email(page, args.intro)
    elif args.dedup_only is not None:
        _do_dedup_test(page, args.dedup_only)
    elif args.dedup_sor is not None:
        _do_dedup_sor(page, args.dedup_sor)
    elif args.inspect_sor is not None:
        intake.inspect_sor(page, args.inspect_sor)
    elif args.intake:
        intake.run(page, limit=args.limit)
    elif args.create_customer:
        _do_create_customer(page, args.so_id, args.cust_id, args.preview)
    elif args.final_touch:
        final_touch.run(page, args.so_id)
    elif args.parts_order:
        parts_order.run(page, args.so_id)
    elif args.cards_order is not None:
        cards_order.run(page, args.so_id,
                        shortname=(None if args.cards_order == "auto" else args.cards_order))
    elif args.card_modify is not None:
        print("[console] card-modify isn't in the console yet -- run `python run.py m <id>` in a terminal.")
    elif args.add_location is not None:
        _do_addloc(page, args.so_id, args.add_location)
    elif args.add_user is not None:
        _do_adduser(page, args.so_id, args.add_user)
    elif args.custid:
        _do_custid(page, args.so_id, args.cust_id)
    elif args.provision is not None:
        _do_provision_chain(page, args.so_id, args.provision)
    elif args.salesforce:
        salesforce.run(page, args.so_id)
    elif args.snapshot:
        _do_snapshot(page, args.so_id)
    elif args.first_touch and args.no_itf:
        # `system <id>` -- one idempotent run (auto first vs second touch).
        _do_system(page, args.so_id, assembly_week=args.assembly_week, dedup_test=args.dedup_test)
    elif args.first_touch:
        # Legacy ITF first touch (`s first <id>`).
        res = first_touch.run(page, args.so_id, assembly_week=args.assembly_week,
                              no_itf=args.no_itf, dedup_test=args.dedup_test)
        _post_first_touch(page, args.so_id, res, args.no_itf)
    elif args.read_tasks:
        navigate_to_so(page, args.so_id)
        _print_task_states(read_task_states(page))
    elif args.set_tasks:
        navigate_to_so(page, args.so_id)
        action_set_system_tasks(page)
        save_so(page, accept_sor=False)
    elif args.check_schedule:
        print_schedule(read_schedule_capacity(page))
    elif args.itf:
        _do_itf(page, args.so_id)
    elif args.card_step is not None:
        _do_cards(page, args.so_id, args.card_step or "")
    elif args.so_id is not None:
        read_so(page, args.so_id)
    else:
        print("[console] try: system <id> | parts <id> | cards <id> [name] | dedup-sor <id> | "
              "tasks <id> | schedule <id> | itf <id> | intake | inspect <sor> | read <id>")


def _start_console(parser):
    """Persistent session: launch the browser once, run typed commands in a loop."""
    import traceback
    print("\n2AUTO2MOOPS console -- the browser opens once and stays open across commands.\n")
    print("MAIN RUNS  (full order processing)")
    print("  system <id>           dedup -> tag -> schedule -> customer -> provision chain")
    print("  parts  <id>           parts / readers order (EFS / VUnics / Slack)")
    print("  cards  <id> [name]    cards-only order\n")
    print("WORKFLOW STEPS  (run any single piece)")
    print("  dedup \"<email|phone|name>\"      dedup-sor <id>         schedule <id>")
    print("  createcust <id> [cust]          custid <so> [cust]     provision <so> <cust>")
    print("  addloc <so> <cust>              adduser <so> <cust>    apiuser <cust>")
    print("  stripe <cust> <key>             intro <cust>           itf <id>")
    print("  tasks <id>                      snapshot <id>          final <id>")
    print("  settasks <id>")
    print("  card <so> <cust>  (card step only, safe on a system order)")
    print("  r first|final <id>              m <id>  (card-modify)\n")
    print("READ / INSPECT")
    print("  read <id>   plan <id>   intake   inspect <sor>   createcust <id> [cust] [--preview]")
    print("  inspect-form <url>   inspect-lp <cust> <url>   inspect-pp <key>   recopy\n")
    print("  -v <run> for full step output                                   quit | exit")
    pw, context, page = launch_browser()
    sys.stdout = _QuietWriter(sys.stdout, logpath=RUN_LOG)  # summary console + full tee to run.log
    try:
        while True:
            try:
                sys.__stdout__.write("\n2auto> ")
                sys.__stdout__.flush()
                line = input().strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in ("quit", "exit", "q"):
                break
            try:
                tokens = _expand_verb(["run.py"] + line.split())[1:]
                cmd_args = parser.parse_args(tokens)
            except SystemExit:
                continue  # _expand_verb/argparse already printed the usage/error -- stay in loop
            try:
                _console_exec(page, cmd_args)
            except Exception as e:
                print(f"[ERROR] {e}")
                traceback.print_exc()
    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


def main():
    sys.argv = _expand_verb(sys.argv)
    parser = argparse.ArgumentParser(
        description="2AUTO2MOOPS -- Sales Order Playbook Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--so-id", type=int, help="Sales Order ID (not needed for --intake)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Full step-by-step output (default prints summary only)")

    # Batch (queue-level, no single SO)
    batch = parser.add_argument_group("batch")
    batch.add_argument("--intake", action="store_true",
                       help="Read-only: scan the SOR queue, build the intake board")
    batch.add_argument("--limit", type=int, default=None,
                       help="Limit --intake to the first N queued orders (dev/testing)")
    batch.add_argument("--inspect-sor", type=int, default=None, metavar="SOR_ID",
                       help="Dump one SOR detail page's fields/attachments and exit")
    batch.add_argument("--sf-search", type=str, default=None, metavar="QUERY",
                       help="SF dedupe discovery: type QUERY into the global search, dump the typeahead")
    batch.add_argument("--inspect-form", type=str, default=None, metavar="URL",
                       help="Dump all fillable controls on a page (form discovery) and exit")
    batch.add_argument("--dedup-only", type=str, default=None, metavar="QUERY",
                       help="Dev: scrape Admin /customers and match QUERY (email/phone/name) in isolation")
    batch.add_argument("--dedup-sor", type=str, default=None, metavar="SOR_ID",
                       help="Read a raw SOR like an order (contact + name) and dedup it against /customers")
    batch.add_argument("--api-user", type=str, default=None, metavar="CUST_ID",
                       help="Post-save: fill the API user (POS) on a saved customer page (no submit)")
    batch.add_argument("--intro", type=str, default=None, metavar="CUST_ID",
                       help="Admin: send the intro email for a customer's admin users (confirms first)")
    batch.add_argument("--recopy", action="store_true",
                       help="Re-copy the last EFS snippet (_efs_snippet.js) to the clipboard")
    batch.add_argument("--inspect-lp", nargs=2, default=None, metavar=("CUST_ID", "URL"),
                       help="Log into LaundroPortal for CUST_ID (admin bridge), then dump URL's form controls")
    batch.add_argument("--inspect-pp", type=str, default=None, metavar="LOCATION_KEY",
                       help="Dump the per-location Payment Processing form (navigates panel -> Payment Processing link)")
    batch.add_argument("--stripe", nargs=2, default=None, metavar=("CUST_ID", "LOCATION_KEY"),
                       help="Initiate Stripe at the location: guarded navigate to Payment Processing (no auto-fill)")
    batch.add_argument("--create-customer", action="store_true",
                       help="Fill the Admin Portal Create Customer form from the SO (no submit)")
    batch.add_argument("--cust-id", type=str, default=None,
                       help="Customer ID to use with --create-customer (else auto-suggested)")
    batch.add_argument("--preview", action="store_true",
                       help="With --create-customer: print the field=value plan only, no fill")

    # Playbooks (pick one)
    playbook = parser.add_argument_group("playbooks")
    playbook.add_argument("--first-touch", action="store_true",
                          help="Full first-touch playbook for System - Laundromat orders")
    playbook.add_argument("--salesforce", action="store_true",
                          help="STANDALONE Salesforce workflow (sf <id>) -- not part of the system run")
    playbook.add_argument("--no-itf", action="store_true",
                          help="First-touch: fill Create Customer (no submit) instead of opening ITF")
    playbook.add_argument("--dedup-test", action="store_true",
                          help="First-touch: print a customer dedup verdict at the schedule step (test)")
    playbook.add_argument("--add-location", type=str, default=None, metavar="CUST_ID",
                          help="LaundroPortal Add Location for CUST_ID (address from --so-id's SOR, no submit)")
    playbook.add_argument("--add-user", type=str, default=None, metavar="CUST_ID",
                          help="LaundroPortal Add User for CUST_ID (contact from --so-id, no submit)")
    playbook.add_argument("--provision", type=str, default=None, metavar="CUST_ID",
                          help="Run the guided no-ITF chain (apiuser->location->stripe->user->intro) for CUST_ID")
    playbook.add_argument("--custid", action="store_true",
                          help="Standalone Cust ID workflow (Create Customer + finalize) for --so-id, for testing")
    playbook.add_argument("--parts-order", action="store_true",
                          help="Parts/Readers Only order playbook (EFS, VUnics, or Slack)")
    playbook.add_argument("--cards-order", nargs="?", const="auto", default=None,
                          help="Cards Only order playbook ('auto' shortname or specify one)")
    playbook.add_argument("--final-touch", action="store_true",
                          help="Final touch playbook — pre-ship audit, complete all To Do tasks")
    playbook.add_argument("--card-modify", nargs="?", const="auto", default=None,
                          help="Modify existing card (clone+add+email). 'auto' or specify new name.")

    # Individual actions (compose as needed)
    actions = parser.add_argument_group("individual actions")
    actions.add_argument("--set-tag", type=str,
                         help="Set Tag field ('auto' to generate from products)")
    actions.add_argument("--add-part", type=str, help="Part number to add")
    actions.add_argument("--qty", type=int, default=1, help="Quantity for --add-part")
    actions.add_argument("--add-missing", action="store_true",
                         help="Add rule-based parts (CARD-03-01, SVC, paper, pinpad kit)")
    actions.add_argument("--add-splicers", action="store_true",
                         help="Update wire splicer qty from missing parts section")
    actions.add_argument("--assembly-week", type=str,
                         help="Set assembly week date (YYYY-MM-DD)")
    actions.add_argument("--set-tasks", action="store_true",
                         help="Set task checklist for system order")
    actions.add_argument("--read-tasks", action="store_true",
                         help="Read and display current task checklist states")
    actions.add_argument("--read-sor", action="store_true",
                         help="Read SOR for processor type, required date, etc.")
    actions.add_argument("--snapshot", action="store_true",
                         help="Read-only SO/SOR/tasks snapshot and optimized rerun plan")
    actions.add_argument("--check-schedule", action="store_true",
                         help="Show assembly week capacity")
    actions.add_argument("--clone-card", type=str, nargs="?", const="auto",
                         help="Clone A-TEMP-CARD-MD ('auto' or specific shortname)")
    actions.add_argument("--add-card-to-so", type=str,
                         help="Add card part to SO (e.g. CARD-MD-THELNDRY)")
    actions.add_argument("--card-email", type=str,
                         help="Open card design email (does NOT send)")
    actions.add_argument("--itf", action="store_true",
                         help="Open IT provisioning form (Jira, does NOT submit)")
    actions.add_argument("--card-step", type=str, nargs="?", const="", default=None, metavar="CUST_ID",
                         help="Run ONLY the card step (clone+add+email) on a system order -- "
                              "does NOT change tag/order-type/shipment")
    actions.add_argument("--save", action="store_true",
                         help="Save the SO after actions")

    if len(sys.argv) == 1:        # bare `python run.py` -> persistent console
        _start_console(parser)
        return

    args = parser.parse_args()

    if (not args.intake and args.inspect_sor is None and args.inspect_form is None
            and args.sf_search is None
            and args.dedup_only is None and args.dedup_sor is None and args.api_user is None
            and args.inspect_lp is None and args.inspect_pp is None and args.stripe is None
            and args.intro is None and not args.recopy and args.so_id is None):
        parser.error("provide an SO id (e.g. `system 19697`), or use `intake` / `inspect <sor>`")

    if not args.verbose:
        sys.stdout = _QuietWriter(sys.stdout, logpath=RUN_LOG)

    print("2AUTO2MOOPS")
    if args.recopy:  # no browser needed -- just re-copy the saved EFS snippet
        from core import efs
        efs.recopy_last_snippet()
        return
    if args.inspect_form is not None:
        print("Mode: form inspect")
    elif args.sf_search is not None:
        print(f"Mode: SF search typeahead inspect '{args.sf_search}'")
    elif args.dedup_only is not None:
        print(f"Mode: dedup test '{args.dedup_only}'")
    elif args.dedup_sor is not None:
        print(f"Mode: dedup SOR {args.dedup_sor}")
    elif args.api_user is not None:
        print(f"Mode: API user fill (customer {args.api_user})")
    elif args.intro is not None:
        print(f"Mode: intro email (customer {args.intro})")
    elif args.inspect_lp is not None:
        print(f"Mode: LaundroPortal form inspect (customer {args.inspect_lp[0]})")
    elif args.inspect_pp is not None:
        print(f"Mode: Payment Processing inspect (location {args.inspect_pp})")
    elif args.stripe is not None:
        print(f"Mode: Stripe initiate (customer {args.stripe[0]}, location {args.stripe[1]})")
    elif args.inspect_sor is not None:
        print(f"Mode: inspect SOR {args.inspect_sor}")
    elif args.intake:
        print("Mode: intake (queue scan)")
    else:
        print(f"SO ID: {args.so_id}")

    pw, context, page = launch_browser()

    try:
        # ── Batch ────────────────────────────────────────────────────
        if args.inspect_form is not None:
            provisioning.inspect_form(page, args.inspect_form)

        elif args.sf_search is not None:
            provisioning.inspect_sf_search(page, args.sf_search)

        elif args.inspect_lp is not None:
            portal.login_to_portal(page, args.inspect_lp[0])
            provisioning.inspect_form(page, args.inspect_lp[1])

        elif args.inspect_pp is not None:
            provisioning.inspect_payment(page, args.inspect_pp)

        elif args.stripe is not None:
            provisioning.open_stripe(page, args.stripe[0], args.stripe[1])

        elif args.api_user is not None:
            provisioning.fill_api_user(page, args.api_user)

        elif args.intro is not None:
            provisioning.send_intro_email(page, args.intro)

        elif args.dedup_only is not None:
            _do_dedup_test(page, args.dedup_only)

        elif args.dedup_sor is not None:
            _do_dedup_sor(page, args.dedup_sor)

        elif args.card_step is not None:
            _do_cards(page, args.so_id, args.card_step or "")

        elif args.inspect_sor is not None:
            intake.inspect_sor(page, args.inspect_sor)

        elif args.intake:
            intake.run(page, limit=args.limit)

        elif args.create_customer:
            _do_create_customer(page, args.so_id, args.cust_id, args.preview)

        # ── Playbooks ────────────────────────────────────────────────
        elif args.final_touch:
            final_touch.run(page, args.so_id)

        elif args.parts_order:
            parts_order.run(page, args.so_id)

        elif args.cards_order is not None:
            shortname = args.cards_order if args.cards_order != "auto" else None
            cards_order.run(page, args.so_id, shortname=shortname)

        elif args.card_modify is not None:
            # Card modify: same as new-design card flow, no tag/order-type/shipment changes
            import time as _t

            print("\n" + "=" * 60)
            print(f"  CARD MODIFY -- SO-{args.so_id}")
            print("=" * 60)
            t_start = _t.time()

            # Step 1: Read SO
            print("\n--- Step 1: Read SO ---")
            so_data = read_so(page, args.so_id)

            # Step 2: Read SOR for contact info
            print("\n--- Step 2: Read SOR ---")
            sor_data = read_sor_data(page)
            contact_name = sor_data.get("contact_name", "")
            contact_email = sor_data.get("contact_email", "")
            if contact_name:
                print(f"Contact: {contact_name} / {contact_email}")

            # Read existing customer
            existing_cust = read_existing_customer_id(page)
            cust_id = existing_cust.get("id", "") if existing_cust else ""
            if cust_id:
                print(f"[INFO] Existing customer: {existing_cust['name']} (ID: {cust_id})")

            # Existing orders don't carry contact onto the SOR (MOOPS fault). If the SOR
            # contact is blank but we have the cust id, recover it from the Admin record
            # (cached /customers list first) so the card email has a recipient.
            if cust_id and (not contact_name or not contact_email):
                try:
                    from core.portal import lookup_customer_contact
                    info = lookup_customer_contact(page, cust_id)
                    contact_name = contact_name or info.get("contact_name", "")
                    contact_email = contact_email or info.get("contact_email", "")
                    if contact_name or contact_email:
                        print(f"[INFO] Contact from Admin record: {contact_name} / {contact_email}")
                    navigate_to_so(page, args.so_id)  # lookup may have navigated away
                except Exception as e:
                    print(f"[INFO] Could not look up Admin contact ({e})")

            # Step 3: Clone card (same as first-touch new design)
            if args.card_modify and args.card_modify != "auto":
                shortname = args.card_modify
            else:
                shortname = generate_card_shortname(so_data["customer_name"])

            t0 = _t.time()
            print(f"\n--- Step 3: Clone card (CARD-MD-{shortname}) ---")
            card_part = clone_temp_card(page, shortname, end_customer_id=cust_id)
            print(f"  [{_t.time() - t0:.1f}s]")

            # Step 4: Add to SO
            t0 = _t.time()
            print(f"\n--- Step 4: Add {card_part} to SO ---")
            navigate_to_so(page, args.so_id)
            action_add_card_to_so(page, card_part)
            print(f"  [{_t.time() - t0:.1f}s]")

            # Step 5: Save
            t0 = _t.time()
            print(f"\n--- Step 5: Save SO ---")
            save_so(page, accept_sor=False)
            print(f"  [{_t.time() - t0:.1f}s]")

            # Step 6: Card design email
            t0 = _t.time()
            print(f"\n--- Step 6: Card design email ---")
            open_card_design_email(page, card_part,
                                   contact_name=contact_name, contact_email=contact_email)
            print(f"  [{_t.time() - t0:.1f}s]")

            print("\n[PAUSE] Review and send the email. Press Enter when done, or Ctrl+C.")
            try:
                input()
            except KeyboardInterrupt:
                print("\n[INFO] Continuing without confirming email send.")

            elapsed = _t.time() - t_start
            print(f"\n  Total: {elapsed:.1f}s")
            print("\n" + "=" * 60)
            print(f"  CARD MODIFY COMPLETE")
            print(f"  SO-{args.so_id}: {card_part}")
            print("=" * 60)

        elif args.add_location is not None:
            _do_addloc(page, args.so_id, args.add_location)

        elif args.add_user is not None:
            _do_adduser(page, args.so_id, args.add_user)

        elif args.custid:
            _do_custid(page, args.so_id, args.cust_id)

        elif args.provision is not None:
            _do_provision_chain(page, args.so_id, args.provision)

        elif args.salesforce:
            salesforce.run(page, args.so_id)

        elif args.snapshot:
            _do_snapshot(page, args.so_id)

        elif args.first_touch and args.no_itf:
            # `system <id>` -- one idempotent run (auto first vs second touch).
            _do_system(page, args.so_id, assembly_week=args.assembly_week, dedup_test=args.dedup_test)

        elif args.first_touch:
            # Legacy ITF first touch (`s first <id>`).
            res = first_touch.run(page, args.so_id, assembly_week=args.assembly_week,
                                  no_itf=args.no_itf, dedup_test=args.dedup_test)
            _post_first_touch(page, args.so_id, res, args.no_itf)

        # ── Individual actions ───────────────────────────────────────
        else:
            so_data = read_so(page, args.so_id)

            # Read SOR if requested
            processor_type = None
            sor_data = None
            if args.read_sor:
                sor_data = read_sor_data(page)
                processor_type = sor_data["processor_type"]
                kit = determine_pinpad_kit(processor_type)
                print(f"\nProcessor type: '{processor_type}' -> Kit: {kit}")
                if sor_data["required_date"]:
                    exp = " *** EXPEDITED ***" if sor_data["is_expedited"] else ""
                    print(f"Required delivery date: {sor_data['required_date']}{exp}")

            if args.read_tasks:
                print("\n--- Task Checklist ---")
                tasks = read_task_states(page)
                completed = []
                todo = []
                na = []
                for num, info in tasks.items():
                    status = info["status"]
                    label = info["label"]
                    if status == "Completed":
                        completed.append(num)
                        print(f"  Task {num:2d}: ✓ {status:12s}  {label}")
                    elif status == "N/A":
                        na.append(num)
                        print(f"  Task {num:2d}: - {status:12s}  {label}")
                    else:
                        todo.append(num)
                        print(f"  Task {num:2d}: ○ {status:12s}  {label}")
                print(f"\nCompleted: {len(completed)}/10  |  To Do: {len(todo)}  |  N/A: {len(na)}")
                if todo:
                    print(f"Remaining: tasks {', '.join(str(t) for t in todo)}")

            if args.check_schedule:
                print("\n--- Checking schedule capacity ---")
                schedule = read_schedule_capacity(page)
                print_schedule(schedule)

            if args.set_tag:
                if args.set_tag == "auto":
                    tag_value = build_tag(so_data["products"], so_data["customer_name"])
                    print(f"\nAuto-generated tag: {tag_value}")
                    action_set_tag(page, tag_value)
                else:
                    action_set_tag(page, args.set_tag)

            if args.assembly_week:
                action_set_assembly_week(page, args.assembly_week)

            if args.add_missing:
                action_add_required_parts(page, processor_type=processor_type)

            if args.add_splicers:
                action_add_splicers(page)

            if args.add_part:
                action_add_part(page, args.add_part, args.qty)

            if args.set_tasks:
                action_set_system_tasks(page)

            if args.add_card_to_so:
                action_add_card_to_so(page, args.add_card_to_so)

            if args.save:
                save_so(page, accept_sor=False)

            if args.card_email:
                if args.add_card_to_so or args.save:
                    print("\n[PAUSE] Verify SO. Press Enter for card email, or Ctrl+C to stop.")
                    try:
                        input()
                    except KeyboardInterrupt:
                        print("\n[ABORT] Stopping before email")
                        args.card_email = None
                if args.card_email:
                    if not sor_data:
                        sor_data = read_sor_data(page)
                    open_card_design_email(
                        page, args.card_email,
                        contact_name=sor_data.get("contact_name", ""),
                        contact_email=sor_data.get("contact_email", ""),
                    )

            if args.clone_card:
                if args.clone_card == "auto":
                    shortname = generate_card_shortname(so_data["customer_name"])
                    print(f"[INFO] Auto shortname: {shortname} (from '{so_data['customer_name']}')")
                else:
                    shortname = args.clone_card
                existing_cust = read_existing_customer_id(page)
                cust_id = existing_cust.get("id", "")
                if cust_id:
                    print(f"[INFO] Existing customer: {existing_cust['name']} (ID: {cust_id})")
                clone_temp_card(page, shortname, end_customer_id=cust_id)

            if args.itf:
                notes_data = read_internal_notes(page)
                existing_cust = read_existing_customer_id(page)
                vac_count = sum(
                    int(p["qty"]) for p in so_data["products"]
                    if p["part_number"].upper().startswith("VAC") and str(p["qty"]).isdigit()
                )
                itf_processor = ""
                if sor_data:
                    itf_processor = sor_data.get("processor_type", "")
                else:
                    sor_data = read_sor_data(page)
                    itf_processor = sor_data.get("processor_type", "")
                open_itf_form(
                    page, args.so_id, notes_data, vac_count,
                    existing_customer=existing_cust if existing_cust else None,
                    card_design_type=notes_data.get("card_design_type", ""),
                    processor_type=itf_processor,
                )

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()

    import time as _time
    print("\nBrowser staying open. Press Ctrl+C to exit.")
    try:
        while True:
            _time.sleep(1)
    except (KeyboardInterrupt, Exception):
        pass

    try:
        context.close()
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass


if __name__ == "__main__":
    main()
