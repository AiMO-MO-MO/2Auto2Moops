"""
Customer dedup -- Stage 1 (Admin Portal /customers).

Pure matching logic over the scraped Admin customer list. No browser here:
core.portal.scrape_admin_customers does the single, cached page read; this module
turns one order's contact signals into match candidates.

Match priority (Matt): email > phone > last name > laundromat name.
  - email / phone  -> STRONG  -> verdict "existing"
  - last name / business-name overlap -> WEAK -> verdict "potential"
  - nothing -> "new"

Scope: System orders only. Routes attach to an existing dealer/multi-housing
umbrella account, so intake never deduplicates them. Address (query-tool) is
Stage 1b and Salesforce is Stage 2 -- both wired separately.
"""

import re

# Placeholder / dead-account rows. Still scraped, but never a real dedup target,
# so they're excluded from matches (avoids "Delete", "TBD", temp@temp noise).
_JUNK_NAME = re.compile(
    r'\b(delete|moved|not used|free to use|temp|tbd|demo|test|please delete|unused)\b',
    re.I,
)
_JUNK_EMAIL = re.compile(r'(temp@temp|na@na|name@name|tbd@tbd)', re.I)

# Dropped when comparing business names so "Wash Works Laundromat LLC" and
# "Wash Works" collapse to the same key.
_NAME_STOP = {
    "laundromat", "laundromats", "laundry", "laundries", "wash", "washateria",
    "coin", "cleaners", "cleaner", "llc", "inc", "incorporated", "corp", "co",
    "the", "and", "of", "center", "centre", "express", "services", "service",
}


def normalize_phone(s):
    """Last 10 digits, or '' if fewer than 10 (strips country code, formatting)."""
    d = re.sub(r'\D', '', s or '')
    return d[-10:] if len(d) >= 10 else ''


def normalize_email(s):
    """First email token found, lowercased; '' if none."""
    m = re.search(r'[\w.+-]+@[\w.-]+\.\w+', (s or '').lower())
    return m.group(0) if m else ''


def last_name(name):
    parts = re.sub(r'[^a-z\s]', '', (name or '').lower()).split()
    # drop trailing business words so "Lassen Laundromat" -> "lassen" (not
    # "laundromat"); avoids generic words colliding as surnames
    while len(parts) > 1 and parts[-1] in _NAME_STOP:
        parts.pop()
    return parts[-1] if parts else ''


def name_tokens(name):
    toks = re.sub(r'[^a-z0-9\s]', ' ', (name or '').lower()).split()
    return {t for t in toks if t not in _NAME_STOP and len(t) > 1}


def parse_contact(raw):
    """Admin /customers 'Main Contact' cell -> (name, email, phone).

    The cell is newline-separated -- usually name / email / phone, but order and
    presence vary (some rows are name/lastname/phone, some just a name, some empty).
    """
    email = normalize_email(raw)
    phone = normalize_phone(raw)
    name = ''
    for line in (raw or '').splitlines():
        t = line.strip()
        if not t or '@' in t:
            continue
        # skip a phone-only line (digits, no letters)
        if normalize_phone(t) and not re.search(r'[a-z]', t, re.I):
            continue
        name = t
        break
    return name, email, phone


def is_junk(cust):
    return bool(_JUNK_NAME.search(cust.get("name", ""))
                or _JUNK_EMAIL.search(cust.get("contact_email", "")))


def match_customer(order, customers):
    """Match one order's contact signals against the scraped customer list.

    order: {customer_name, contact_name, contact_email, contact_phone}
    customers: list from core.portal.scrape_admin_customers

    Returns {"verdict": "existing"|"potential"|"new", "matches": [...]} where each
    match is {cust_id, name, signal, strength, detail}. email/phone hits are STRONG
    (verdict "existing"); last-name / business-name overlap are WEAK ("potential").
    """
    o_email = normalize_email(order.get("contact_email", ""))
    o_phone = normalize_phone(order.get("contact_phone", ""))
    o_last = last_name(order.get("contact_name", ""))
    o_tokens = name_tokens(order.get("customer_name", ""))

    strong, weak = [], []
    seen_strong = set()
    for c in customers:
        if is_junk(c):
            continue
        cid = c.get("cust_id", "")

        # candidate contact, carried on every match so the caller can show it
        cc = {"contact_name": c.get("contact_name", ""),
              "contact_email": c.get("contact_email", ""),
              "contact_phone": c.get("contact_phone", "")}

        if o_email and c.get("contact_email") and o_email == c["contact_email"]:
            if cid in seen_strong:
                continue
            seen_strong.add(cid)
            strong.append({"cust_id": cid, "name": c.get("name", ""),
                           "signal": "email", "strength": "strong", "detail": o_email, **cc})
            continue
        if o_phone and c.get("contact_phone") and o_phone == c["contact_phone"]:
            if cid in seen_strong:
                continue
            seen_strong.add(cid)
            strong.append({"cust_id": cid, "name": c.get("name", ""),
                           "signal": "phone", "strength": "strong", "detail": o_phone, **cc})
            continue

        # weak signals (only worth collecting; verdict downgrades to "potential")
        c_last = last_name(c.get("contact_name", ""))
        if o_last and c_last and o_last == c_last and o_last not in _NAME_STOP:
            weak.append({"cust_id": cid, "name": c.get("name", ""),
                         "signal": "last_name", "strength": "weak", "detail": c_last, **cc})
            continue
        c_tokens = name_tokens(c.get("name", ""))
        if o_tokens and c_tokens:
            overlap = o_tokens & c_tokens
            if overlap and (o_tokens <= c_tokens or len(overlap) >= 2):
                weak.append({"cust_id": cid, "name": c.get("name", ""),
                             "signal": "name", "strength": "weak",
                             "detail": " ".join(sorted(overlap)), **cc})

    # de-dup weak by cust_id, preserve order
    uniq_weak, s = [], set()
    for w in weak:
        if w["cust_id"] in s:
            continue
        s.add(w["cust_id"])
        uniq_weak.append(w)

    if strong:
        return {"verdict": "existing", "matches": strong + uniq_weak[:5]}
    if uniq_weak:
        return {"verdict": "potential", "matches": uniq_weak[:8]}
    return {"verdict": "new", "matches": []}
