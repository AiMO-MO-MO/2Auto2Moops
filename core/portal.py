"""
Portal verification — Admin Portal + LaundroPortal reads for final touch.

Checks IT provisioning for tasks 7, 8, 10:
  - Admin Portal (admintools): customer record, features, admin user, intro email, API user
  - LaundroPortal (portal): location config, payment processing, portal user

Navigation order (optimized — 5 page loads total):
  1. Admin Portal customer page → all admin checks + location_key mapping
  2. Admin Portal LOGIN → establish portal support session
  3. Portal location edit → address, basic features, VAC licenses, portal fee
  4. Portal payment processing → Stripe merchant, account access
  5. Portal users → user with admin access
"""

import re
import time
from playwright.sync_api import Page

ADMIN_TOOLS = "https://admintools.mitechisys.com"
PORTAL = "https://portal.mitechisys.com"

# city/state per cust_id (dedup enrichment) -- avoids re-logging into LP for repeats
_CUST_LOC_CACHE = {}


def _city_state(addr: str) -> str:
    """'897 N Stiles St, Linden, NJ' -> 'Linden, NJ'. Best-effort from a comma address."""
    parts = [p.strip() for p in (addr or "").split(",") if p.strip()]
    # drop a trailing country / zip-only tail
    while parts and (parts[-1].lower() in ("united states", "usa", "us")
                     or parts[-1].replace("-", "").isdigit()):
        parts.pop()
    if len(parts) >= 2:
        return f"{parts[-2]}, {parts[-1]}"
    return ", ".join(parts)


def customer_location_summary(page, cust_id: str) -> str:
    """Read-only: log into LaundroPortal for cust_id and read its locations' City/State
    from the index Locations table. Returns 'Linden, NJ' (or 'A, NJ; B, NY' for several),
    '' if none. Cached per cust_id.

    NOTE: this re-scopes the LP Support View to cust_id. It only READS, so it doesn't
    trip the write-guard -- but callers that later WRITE to LP must re-establish scope.
    """
    cust_id = (cust_id or "").strip()
    if not cust_id:
        return ""
    if cust_id in _CUST_LOC_CACHE:
        return _CUST_LOC_CACHE[cust_id]
    summary = ""
    try:
        if login_to_portal(page, cust_id):
            addrs = page.evaluate(r"""() => {
                const out = [];
                for (const t of document.querySelectorAll('table')) {
                    if (!(t.innerText || '').includes('Address')) continue;
                    for (const tr of t.querySelectorAll('tr')) {
                        const td = tr.querySelectorAll('td');
                        if (td.length >= 2) {
                            const a = (td[1].innerText || '').replace(/\s+/g, ' ').trim();
                            if (a) out.push(a);
                        }
                    }
                }
                return out;
            }""")
            seen = []
            for a in (addrs or []):
                cs = _city_state(a)
                if cs and cs not in seen:
                    seen.append(cs)
            summary = "; ".join(seen)
    except Exception as e:
        print(f"[dedup] location lookup failed for {cust_id}: {e}")
    _CUST_LOC_CACHE[cust_id] = summary
    return summary


def read_portal_location_index(page: Page, cust_id: str) -> list:
    """Read the LaundroPortal location index table for a customer.

    Returns rows with location_id, location_key (the LaundroPortal URL id), address, and
    description/name. This is much cheaper than opening every LocationPanel page and is enough
    for SO config linking and the SaaS handoff (which needs the Location_Key for an
    already-created location).
    """
    rows = []
    if not login_to_portal(page, cust_id):
        return rows
    # The locations table loads via AJAX after the page shell. Reading immediately returns
    # 0 rows even when the customer has locations -- that false "0 rows" is what made the
    # chain think a provisioned customer had none and bail. Wait for a Location ID (7 digits)
    # to render before reading. (A genuinely empty customer just waits out the timeout.)
    try:
        page.wait_for_function(
            r"() => { const t = document.body ? document.body.innerText : ''; return /\b\d{7}\b/.test(t); }",
            timeout=8000,
        )
    except Exception:
        pass
    try:
        rows = page.evaluate(r"""() => {
            const out = [];
            for (const tr of document.querySelectorAll('tr')) {
                const cells = [...tr.querySelectorAll('td')].map(td =>
                    (td.innerText || '').replace(/\s+/g, ' ').trim()
                );
                const joined = cells.join(' ');
                const m = joined.match(/\b(\d{7})\b/);
                if (!m) continue;
                const locId = m[1];
                const keyAnchor = tr.querySelector('a[href*="Location_Key="]');
                const keyM = keyAnchor ? (keyAnchor.getAttribute('href') || '').match(/Location_Key=(\d+)/) : null;
                const locationKey = keyM ? keyM[1] : '';
                let address = '';
                let description = '';
                for (const c of cells) {
                    if (!address && /\d+\s+.+,\s*[^,]+,\s*[A-Z]{2,}/i.test(c)) address = c;
                    if (!description && /Clean Rite|Laundromax|Laundry|Laundromat|Center/i.test(c)) {
                        description = c;
                    }
                }
                if (!address) {
                    const am = joined.match(/(\d+\s+[^|]+?,\s*[^|]+?,\s*[A-Z]{2,}(?:\b|$))/i);
                    if (am) address = am[1].trim();
                }
                if (locId && address) out.push({location_id: locId, location_key: locationKey, address, description});
            }
            const seen = new Set();
            return out.filter(r => {
                if (seen.has(r.location_id)) return false;
                seen.add(r.location_id);
                return true;
            });
        }""")
    except Exception as e:
        print(f"[portal] location index lookup failed for {cust_id}: {e}")
    print(f"[READ] Portal location index {cust_id}: {len(rows)} rows")
    return rows


def current_portal_customer(page):
    """The Customer ID the LaundroPortal Support View is currently scoped to (the
    sidebar shows 'Customer ID: 0xxxx'). Returns '' if not found. Use this to GUARD
    every LP write -- portal actions hit whichever customer is logged in, and writing
    under the wrong account is a serious, sensitive mistake."""
    try:
        txt = page.evaluate("() => document.body.innerText || ''")
    except Exception:
        return ""
    m = re.search(r'Customer ID[:\s]*?(0\d{4})', txt or "")
    return m.group(1) if m else ""


# ── Admin Portal ────────────────────────────────────────────


def read_admin_contact(page: Page, cust_id: str) -> dict:
    """Read only primary contact fields from Admin Portal customer page."""
    url = f"{ADMIN_TOOLS}/customers/{cust_id}"
    page.goto(url, wait_until="domcontentloaded", timeout=15000)
    try:
        page.wait_for_function(
            "() => document.body && document.body.innerText.includes('Primary Contact')",
            timeout=8000,
        )
    except Exception:
        page.wait_for_timeout(1000)
    data = page.evaluate(r"""() => {
        const read = (labelText) => {
            for (const l of document.querySelectorAll('label')) {
                if ((l.textContent || '').trim() === labelText) {
                    const group = l.closest('.form-group, div') || l.parentElement;
                    const inp = group && group.querySelector('input, textarea, select');
                    if (!inp) return '';
                    return (inp.value || inp.textContent || '').trim();
                }
            }
            return '';
        };
        return {
            contact_name: read('Primary Contact Name'),
            contact_email: read('Primary Contact Email'),
            contact_phone: read('Primary Contact Phone Number')
        };
    }""")
    print(f"[READ] Admin contact {cust_id}: "
          f"{data.get('contact_name') or '(blank)'} / {data.get('contact_email') or '(blank)'}")
    return data


def read_admin_portal(page: Page, cust_id: str) -> dict:
    """
    Navigate to Admin Portal customer page and read all verification data.

    Returns dict with:
      customer_name, contact_name, contact_email, contact_phone,
      api_login_vac, operation_type, features (dict of checkbox states),
      admin_users (list of {login, email, intro_email_status}),
      api_access (str), billing_status (str),
      location_keys (dict of location_id -> location_key)
    """
    t0 = time.time()
    url = f"{ADMIN_TOOLS}/customers/{cust_id}"
    page.goto(url, wait_until="domcontentloaded", timeout=15000)
    time.sleep(2)

    # Verify we landed on the right page. Admin Portal can render slowly or without
    # the expected h1 during redirects, so fall back to the body text before giving up.
    title = ""
    body = ""
    try:
        title = page.locator("h1").first.text_content(timeout=8000) or ""
    except Exception:
        pass
    try:
        body = page.evaluate("() => document.body.innerText || ''")
    except Exception:
        body = ""
    if cust_id not in title and cust_id not in body:
        print(f"[WARNING] Admin Portal customer {cust_id} not confirmed "
              f"(title={title or '(no h1)'}, url={page.url}).")
        return {}

    data = page.evaluate(r"""() => {
        const r = {
            customer_name: '', contact_name: '', contact_email: '', contact_phone: '',
            api_login_vac: '', operation_type: '',
            features: {}, admin_users: [], api_access: '', billing_status: '',
            location_keys: {}
        };

        // Helper: read input/select value by its label text
        const byLabel = (text) => {
            for (const l of document.querySelectorAll('label')) {
                if (l.textContent.trim() === text) {
                    const sib = l.nextElementSibling;
                    if (sib && /^(INPUT|TEXTAREA|SELECT)$/.test(sib.tagName)) {
                        return sib.tagName === 'SELECT'
                            ? (sib.options[sib.selectedIndex]?.text || '').trim()
                            : (sib.value || '').trim();
                    }
                }
            }
            return '';
        };

        // Customer basics
        r.customer_name = byLabel('Customer Name');
        r.contact_name = byLabel('Primary Contact Name');
        r.contact_email = byLabel('Primary Contact Email');
        r.contact_phone = byLabel('Primary Contact Phone Number');
        r.api_login_vac = byLabel('API Login (VAC)');
        r.operation_type = byLabel('Laundry Operation Type');

        // Extended Features checkboxes
        const headings = [...document.querySelectorAll('h5')];
        const efh = headings.find(h => h.textContent.includes('Extended Features'));
        if (efh) {
            let el = efh.nextElementSibling;
            while (el && el.tagName === 'UL') {
                el.querySelectorAll('label').forEach(l => {
                    const cb = l.querySelector('input[type="checkbox"]');
                    if (cb) {
                        const spans = l.querySelectorAll('span');
                        const name = spans.length > 0
                            ? [...spans].map(s => s.textContent.trim()).filter(Boolean).join(' ')
                            : l.textContent.replace(/^\s*/, '').trim();
                        if (name) r.features[name] = cb.checked;
                    }
                });
                el = el.nextElementSibling;
            }
        }

        // Admin Users table
        const adminDiv = document.getElementById('adminUsers');
        if (adminDiv) {
            const rows = adminDiv.querySelectorAll('table tr, tr');
            for (const row of rows) {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    const login = (cells[0]?.textContent || '').trim();
                    const email = (cells[1]?.textContent || '').trim();
                    // Email status is in the last cell
                    const statusCell = cells[cells.length - 1];
                    const status = (statusCell?.textContent || '').trim();
                    if (login && login !== 'Login') {
                        r.admin_users.push({
                            login: login,
                            email: email,
                            intro_email_status: status
                        });
                    }
                }
            }
        }

        // API Access — find the POS/All/Card Loader select
        for (const s of document.querySelectorAll('select')) {
            const opts = [...s.options].map(o => o.text.trim());
            if (opts.includes('POS') && opts.includes('All') && opts.includes('Card Loader')) {
                r.api_access = (s.options[s.selectedIndex]?.text || '').trim();
                break;
            }
        }

        // Location key mapping from select options (7-digit location IDs)
        const seen = new Set();
        document.querySelectorAll('select option').forEach(o => {
            const loc = o.textContent.trim();
            const key = o.value;
            if (/^\d{7}$/.test(loc) && /^\d+$/.test(key) && !seen.has(loc)) {
                r.location_keys[loc] = key;
                seen.add(loc);
            }
        });

        // Billing status (first billing-related select with Account Set Up option)
        const bsh = headings.find(h => h.textContent.includes('Existing Billing'));
        if (bsh) {
            // Walk siblings to find the select
            let el = bsh;
            while (el) {
                const sel = el.querySelector ? el.querySelector('select') : null;
                if (sel) {
                    const hasSetup = [...sel.options].some(o => o.text === 'Account Set Up');
                    if (hasSetup) {
                        r.billing_status = (sel.options[sel.selectedIndex]?.text || '').trim();
                        break;
                    }
                }
                el = el.nextElementSibling;
            }
        }

        return r;
    }""")

    elapsed = time.time() - t0
    print(f"[READ] Admin Portal {cust_id}: {data.get('customer_name', '?')}")
    n_users = len(data.get("admin_users", []))
    n_locs = len(data.get("location_keys", {}))
    print(f"  Features: {sum(data.get('features', {}).values())} enabled, "
          f"Admin users: {n_users}, Locations: {n_locs}")
    print(f"  [{elapsed:.1f}s]")
    return data


# ── LaundroPortal ───────────────────────────────────────────


def login_to_portal(page: Page, cust_id: str) -> bool:
    """
    Navigate to LaundroPortal via Admin Portal LOGIN redirect.
    Establishes support session for this customer.
    Returns True if portal loaded successfully.
    """
    t0 = time.time()
    login_url = f"{ADMIN_TOOLS}/portal/{cust_id}"
    page.goto(login_url, wait_until="domcontentloaded", timeout=20000)

    # The admin->LP bridge can sit on a loading/login page for a few seconds. Poll until
    # LP is actually up AND its Support View is scoped to cust_id, rather than a flat sleep.
    for _ in range(24):  # up to ~12s
        on_portal = "portal.mitechisys.com" in (page.url or "")
        if on_portal and current_portal_customer(page) == str(cust_id):
            print(f"[NAV] LaundroPortal ready for {cust_id} [{time.time() - t0:.1f}s]")
            return True
        time.sleep(0.5)

    cur = current_portal_customer(page)
    on_portal = "portal.mitechisys.com" in (page.url or "")
    print(f"[WARNING] LaundroPortal login for {cust_id} not confirmed "
          f"(url={page.url}, scope={cur or '?'}). The admin->LP bridge may still be loading.")
    return on_portal


def read_portal_location(page: Page, location_key: str) -> dict:
    """
    Read location details from LaundroPortal location edit page.

    Returns dict with:
      location_id, address, city, state, zip, deployment_phase, operation_type,
      basic_features (bool), vac_licenses (int), portal_fee (int)
    """
    t0 = time.time()
    url = f"{PORTAL}/LocationPanel.php?Location_Key={location_key}"
    page.goto(url, wait_until="domcontentloaded", timeout=15000)
    time.sleep(2)

    data = page.evaluate(r"""() => {
        const r = {
            location_id: '', address: '', city: '', state: '', zip: '',
            deployment_phase: '', operation_type: '', ownership: '',
            basic_features: false, vac_licenses: 0, portal_fee: 0
        };

        // Read all label -> input pairs
        const labels = document.querySelectorAll('label');
        for (const l of labels) {
            const text = l.textContent.trim().replace(/:$/, '');
            const group = l.closest('.form-group') || l.parentElement;
            if (!group) continue;
            const inp = group.querySelector('input, select, textarea');
            if (!inp) continue;
            const val = inp.tagName === 'SELECT'
                ? (inp.options[inp.selectedIndex]?.text || '').trim()
                : (inp.value || '').trim();

            if (text.startsWith('Location ID')) r.location_id = val;
            else if (text.startsWith('Street Address')) r.address = val;
            else if (text === 'City') r.city = val;
            else if (text.startsWith('State')) r.state = val;
            else if (text.startsWith('Zip')) r.zip = val;
            else if (text.startsWith('Deployment')) r.deployment_phase = val;
            else if (text.startsWith('Laundry Operation')) r.operation_type = val;
            else if (text.startsWith('Ownership')) r.ownership = val;
            else if (text.startsWith('Portal Fee')) r.portal_fee = parseInt(val) || 0;
        }

        // Basic Features checkbox (under "User Site Access")
        const allChecks = document.querySelectorAll('input[type="checkbox"]');
        for (const cb of allChecks) {
            const lbl = cb.closest('label') || cb.parentElement;
            if (lbl && lbl.textContent.includes('Basic Features')) {
                r.basic_features = cb.checked;
                break;
            }
        }

        // VAC Licenses — input near "Remaining Seat Licenses" text
        const body = document.body.innerText;
        const seatMatch = body.match(/Remaining Seat Licenses/);
        if (seatMatch) {
            // Find the input in the VAC Authorization section
            const sections = document.querySelectorAll('h5, h4, strong, .font-weight-bold');
            for (const s of sections) {
                if (s.textContent.includes('VAC Authorization')) {
                    const parent = s.closest('section') || s.closest('div') || s.parentElement;
                    if (parent) {
                        const inp = parent.querySelector('input[type="number"], input');
                        if (inp) r.vac_licenses = parseInt(inp.value) || 0;
                    }
                    break;
                }
            }
        }

        return r;
    }""")

    elapsed = time.time() - t0
    addr = f"{data.get('address', '')}, {data.get('city', '')} {data.get('state', '')}"
    print(f"[READ] Portal location {data.get('location_id', '?')}: {addr}")
    print(f"  Basic Features: {data.get('basic_features')}, "
          f"VAC licenses: {data.get('vac_licenses')}, "
          f"Portal fee: ${data.get('portal_fee')}")
    print(f"  [{elapsed:.1f}s]")
    return data


def read_saved_location_id(page: Page, location_key: str) -> str:
    """Read the ACTUAL saved Location ID (the 7-digit id, e.g. 0100005) from the location's
    EDIT form -- this is the field the human may have changed at the save pause to dodge a
    collision. Mirrors the card-part re-read (lesson #12): trust what was saved, not what we
    generated, so the End Customer link + config use the right location. Returns '' on failure.

    LocationPanel.php (the read-only panel) does NOT expose the id as a labeled input, which is
    why read_portal_location() comes back blank -- so read it from EditLocation.php instead."""
    url = f"{PORTAL}/EditLocation.php?Location_Key={location_key}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        val = (page.locator("#location_id").input_value(timeout=8000) or "").strip()
        print(f"[READ] Saved Location ID (key {location_key}): {val or '(empty)'}")
        return val
    except Exception as e:
        print(f"[READ] Could not read saved Location ID (key {location_key}): {e}")
        return ""


def read_portal_payment(page: Page, location_key: str) -> dict:
    """
    Read payment processing data from LaundroPortal.

    Returns dict with:
      processor (str), merchant_account (str),
      onboarding_complete (bool), onboarding_status (str),
      account_access (list of {login, registration}),
      bank_configured (bool)
    """
    t0 = time.time()
    url = f"{PORTAL}/PaymentProcessing.php?Location_Key={location_key}"
    page.goto(url, wait_until="domcontentloaded", timeout=15000)
    time.sleep(2)

    data = page.evaluate(r"""() => {
        const r = {
            processor: '', merchant_account: '',
            onboarding_complete: false, onboarding_status: '',
            account_access: [], bank_configured: false
        };

        // Payment Processor select
        const selects = document.querySelectorAll('select');
        for (const s of selects) {
            const opts = [...s.options].map(o => o.text.trim());
            if (opts.includes('Stripe') || opts.includes('Fortis')) {
                r.processor = (s.options[s.selectedIndex]?.text || '').trim();
                break;
            }
        }

        // Merchant Account — select whose options contain 'acct_'
        for (const s of selects) {
            for (const o of s.options) {
                if (o.text.includes('acct_')) {
                    r.merchant_account = (s.options[s.selectedIndex]?.text || '').trim();
                    break;
                }
            }
            if (r.merchant_account) break;
        }

        // Onboarding status from page text
        const text = document.body.innerText;
        if (text.includes('Onboarding for the account is still pending')) {
            r.onboarding_status = 'pending';
        } else if (r.merchant_account) {
            r.onboarding_status = 'active';
            r.onboarding_complete = true;
        }

        // Bank account
        r.bank_configured = !text.includes('Not configured');

        // Account Access table — has Login + Registration columns
        const tables = document.querySelectorAll('table');
        for (const t of tables) {
            const ths = [...t.querySelectorAll('th')].map(h => h.textContent.trim());
            if (ths.includes('Login') && ths.includes('Registration')) {
                const rows = t.querySelectorAll('tbody tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        const login = (cells[0]?.textContent || '').trim();
                        const reg = (cells[1]?.textContent || '').trim();
                        if (login) r.account_access.push({ login, registration: reg });
                    }
                }
                break;
            }
        }

        return r;
    }""")

    elapsed = time.time() - t0
    n_access = len(data.get("account_access", []))
    print(f"[READ] Portal payment: {data.get('processor', '?')}, "
          f"merchant: {'yes' if data.get('merchant_account') else 'no'}, "
          f"onboarding: {data.get('onboarding_status', '?')}, "
          f"access: {n_access} users")
    print(f"  [{elapsed:.1f}s]")
    return data


def read_portal_users(page: Page) -> list:
    """
    Read users from LaundroPortal Users page.

    Returns list of {login, name, email, locations, access}.
    """
    t0 = time.time()
    url = f"{PORTAL}/Users.php"
    page.goto(url, wait_until="domcontentloaded", timeout=15000)
    time.sleep(2)

    users = page.evaluate(r"""() => {
        const users = [];
        const tables = document.querySelectorAll('table');
        for (const t of tables) {
            const ths = [...t.querySelectorAll('th')].map(h => h.textContent.trim());
            if (ths.includes('Login') && ths.includes('Access')) {
                const rows = t.querySelectorAll('tbody tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 4) {
                        // Registration cell has name + email on separate lines
                        const regText = (cells[1]?.textContent || '').trim();
                        const regParts = regText.split('\n').map(s => s.trim()).filter(Boolean);
                        users.push({
                            login: (cells[0]?.textContent || '').trim(),
                            name: regParts[0] || '',
                            email: regParts[1] || '',
                            locations: (cells[2]?.textContent || '').trim(),
                            access: (cells[3]?.textContent || '').trim()
                        });
                    }
                }
                break;
            }
        }
        return users;
    }""")

    elapsed = time.time() - t0
    for u in users:
        print(f"[READ] Portal user: {u['login']} / {u['access']} / {u['locations']}")
    print(f"  [{elapsed:.1f}s]")
    return users


# ── Verification (called from final_touch.py) ──────────────


def verify_provisioning(page: Page, cust_id: str, vac_count: int,
                        processor_type: str = "") -> dict:
    """
    Full provisioning check across Admin Portal and LaundroPortal.

    Args:
        page: Playwright page
        cust_id: Customer ID (e.g. "02139")
        vac_count: Expected number of VAC licenses
        processor_type: "Stripe" or "Fortis" (from SOR)

    Returns dict:
        {
            "admin": { ... admin portal data },
            "location": { ... location data },
            "payment": { ... payment data },
            "users": [ ... portal users ],
            "checks": {
                "task_7": {"status": "pass"|"fail"|"warning", "detail": "..."},
                "task_8": {"status": "pass"|"fail"|"warning", "detail": "..."},
                "task_10": {"status": "pass"|"fail"|"warning", "detail": "..."},
            }
        }
    """
    print("\n--- Portal verification ---")
    result = {"admin": {}, "location": {}, "payment": {}, "users": [], "checks": {}}

    # Determine expected processor
    if not processor_type:
        expected_processor = "Stripe"
    elif "fortis" in processor_type.lower() or "ebt" in processor_type.lower():
        expected_processor = "Fortis"
    else:
        expected_processor = "Stripe"

    # 1. Admin Portal
    admin = read_admin_portal(page, cust_id)
    result["admin"] = admin
    if not admin.get("customer_name"):
        print(f"[ERROR] Customer {cust_id} not found in Admin Portal")
        for t in ["task_7", "task_8", "task_10"]:
            result["checks"][t] = {"status": "fail", "detail": "Customer not found in Admin Portal"}
        return result

    # Get first location key (most new customers have one location)
    location_keys = admin.get("location_keys", {})
    if not location_keys:
        print("[ERROR] No locations found in Admin Portal")
        result["checks"]["task_8"] = {"status": "fail", "detail": "No location in Admin Portal"}
        result["checks"]["task_7"] = {"status": "fail", "detail": "No location — can't check payment"}
    else:
        # Use first location
        loc_id, loc_key = next(iter(location_keys.items()))
        print(f"\n[INFO] Checking location {loc_id} (key={loc_key})")

        # 2. Login to portal
        login_to_portal(page, cust_id)

        # 3. Location check (task 8)
        location = read_portal_location(page, loc_key)
        result["location"] = location

        t8_issues = []
        if not location.get("basic_features"):
            t8_issues.append("Basic Features not checked")
        if location.get("vac_licenses", 0) != vac_count:
            t8_issues.append(
                f"VAC licenses={location.get('vac_licenses')} "
                f"(expected {vac_count})"
            )
        if location.get("portal_fee", 0) != 50:
            t8_issues.append(f"Portal fee=${location.get('portal_fee')} (expected $50)")

        if t8_issues:
            result["checks"]["task_8"] = {
                "status": "warning",
                "detail": "; ".join(t8_issues)
            }
        else:
            result["checks"]["task_8"] = {"status": "pass", "detail": "Location configured correctly"}

        # 4. Payment processing check (task 7)
        payment = read_portal_payment(page, loc_key)
        result["payment"] = payment

        t7_issues = []
        if not payment.get("merchant_account"):
            t7_issues.append("No merchant account")
        if payment.get("processor", "").lower() != expected_processor.lower():
            t7_issues.append(
                f"Processor={payment.get('processor')} "
                f"(expected {expected_processor})"
            )
        if not payment.get("account_access"):
            t7_issues.append("No Account Access — customer can't complete onboarding")

        if t7_issues:
            result["checks"]["task_7"] = {
                "status": "fail" if "No merchant" in str(t7_issues) else "warning",
                "detail": "; ".join(t7_issues)
            }
        else:
            result["checks"]["task_7"] = {
                "status": "pass",
                "detail": f"{expected_processor} merchant configured, access granted"
            }

    # 5. Portal users
    users = read_portal_users(page)
    result["users"] = users

    # 6. Admin portal user check (task 10)
    t10_issues = []

    # Check admin users
    admin_users = admin.get("admin_users", [])
    if not admin_users:
        t10_issues.append("No admin user in Admin Portal")
    else:
        for u in admin_users:
            status = u.get("intro_email_status", "")
            if "Not sent" in status:
                t10_issues.append(f"Intro email NOT SENT for {u['login']}")

    # Check Payment Processing Reports feature
    features = admin.get("features", {})
    stripe_feature = features.get("Payment Processing Reports Stripe", False)
    fortis_feature = features.get("Payment Processing Reports Fortis", False)
    if expected_processor == "Stripe" and not stripe_feature:
        t10_issues.append("Payment Processing Reports Stripe not checked")
    elif expected_processor == "Fortis" and not fortis_feature:
        t10_issues.append("Payment Processing Reports Fortis not checked")

    # Check API access
    api_access = admin.get("api_access", "")
    if not api_access:
        t10_issues.append("No API user configured")
    elif api_access != "POS":
        t10_issues.append(f"API access={api_access} (expected POS)")

    # Check portal user exists with Admin access
    if not users:
        t10_issues.append("No portal user found")
    elif not any(u.get("access") == "Admin" for u in users):
        t10_issues.append("No portal user with Admin access")

    if t10_issues:
        result["checks"]["task_10"] = {
            "status": "fail" if "NOT SENT" in str(t10_issues) or "No admin" in str(t10_issues) else "warning",
            "detail": "; ".join(t10_issues)
        }
    else:
        result["checks"]["task_10"] = {"status": "pass", "detail": "Admin user configured, intro email sent"}

    # Summary
    print("\n--- Provisioning check summary ---")
    for task, check in sorted(result["checks"].items()):
        icon = "✓" if check["status"] == "pass" else "⚠" if check["status"] == "warning" else "✗"
        print(f"  {icon} {task}: {check['detail']}")

    return result


# ── Customer dedup (Stage 1) ────────────────────────────────

# Session cache so a persistent console running many orders scrapes /customers
# once, not per order. Intake passes use_cache=False (fresh each batch run).
_CUSTOMER_CACHE = None


def scrape_admin_customers(page, use_cache=False):
    """Scrape the full Admin Portal /customers list -- one nav, all rows.

    The list is fully client-rendered into a single table; the "Filter
    customers..." box only filters rows already in the DOM. So we read every row
    once and match locally (see core.dedup) rather than driving the filter per
    order. Cache the result for the whole intake batch.

    Columns (validated): Customer ID (0xxxx) | Support ID (xxxxxxxLOGIN) |
    Customer Name | Main Contact (newline name/email/phone) | Notes.
    Each row links /customers/{id}.

    Returns list of:
      {cust_id, support_id, name, contact_name, contact_email, contact_phone,
       contact_raw, notes, url}
    """
    global _CUSTOMER_CACHE
    if use_cache and _CUSTOMER_CACHE is not None:
        return _CUSTOMER_CACHE
    from core.dedup import parse_contact
    t0 = time.time()
    url = f"{ADMIN_TOOLS}/customers"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # Angular render: wait for template tags to clear AND data rows to mount.
    try:
        page.wait_for_function(
            """() => !document.body.textContent.includes('{{')
                 && document.querySelectorAll('table tbody tr').length > 50""",
            timeout=20000,
        )
    except Exception:
        print("[WARN] /customers render wait timed out -- reading whatever mounted")
        time.sleep(3)

    rows = page.evaluate(r"""() => {
        const out = [];
        const tbl = document.querySelector('table');
        if (!tbl) return out;
        let trs = Array.from(tbl.querySelectorAll('tbody tr'));
        if (!trs.length) trs = Array.from(tbl.querySelectorAll('tr')).slice(1);
        for (const r of trs) {
            const cells = Array.from(r.querySelectorAll('td,th')).map(c => (c.innerText||'').trim());
            if (cells.length < 4) continue;
            const hrefs = Array.from(r.querySelectorAll('a')).map(a => a.getAttribute('href')||'');
            const chref = hrefs.find(h => /\/customers\/\w+/.test(h)) || '';
            out.push({
                cust_id: cells[0] || '',
                support_id: (cells[1] || '').replace(/LOGIN$/i, ''),
                name: cells[2] || '',
                contact_raw: cells[3] || '',
                notes: cells[4] || '',
                href: chref,
            });
        }
        return out;
    }""")

    customers = []
    for r in rows:
        name, email, phone = parse_contact(r.get("contact_raw", ""))
        href = r.get("href", "")
        if href and not href.startswith("http"):
            href = f"{ADMIN_TOOLS}{href}"
        customers.append({
            "cust_id": (r.get("cust_id") or "").strip(),
            "support_id": (r.get("support_id") or "").strip(),
            "name": (r.get("name") or "").strip(),
            "contact_name": name,
            "contact_email": email,
            "contact_phone": phone,
            "contact_raw": r.get("contact_raw", ""),
            "notes": r.get("notes", ""),
            "url": href,
        })
    print(f"[READ] Admin /customers: {len(customers)} rows  [{time.time() - t0:.1f}s]")
    if use_cache:
        _CUSTOMER_CACHE = customers
    return customers


def lookup_customer_contact(page, cust_id: str) -> dict:
    """Recover a customer's contact by cust id: {cust_id, name, contact_name,
    contact_email, contact_phone} or {} if not found.

    Existing orders don't carry the customer contact onto the SOR (MOOPS fault), but the
    SOR's "Existing End Customer" id does -- use it to recover the contact for the card
    email (or anywhere else). Prefers the cached /customers list (no extra nav); if the
    cache is cold or the list row has no contact, falls back to the per-customer Admin
    page. cust_id is matched both as-is and zero-padded to 5 digits ('1435' -> '01435').
    """
    if not cust_id:
        return {}
    want = str(cust_id).strip()
    want_pad = want.zfill(5)
    if _CUSTOMER_CACHE:
        for c in _CUSTOMER_CACHE:
            cid = (c.get("cust_id") or "").strip()
            if cid == want or cid.zfill(5) == want_pad:
                if c.get("contact_email") or c.get("contact_name"):
                    print(f"[lookup] contact for {cid} from cached /customers list")
                    return {"cust_id": cid, "name": c.get("name", ""),
                            "contact_name": c.get("contact_name", ""),
                            "contact_email": c.get("contact_email", ""),
                            "contact_phone": c.get("contact_phone", "")}
                break  # row found but no contact in the list -- try the detail page
    try:
        a = read_admin_portal(page, want_pad)
        if a:
            return {"cust_id": want_pad, "name": a.get("customer_name", ""),
                    "contact_name": a.get("contact_name", ""),
                    "contact_email": a.get("contact_email", ""),
                    "contact_phone": a.get("contact_phone", "")}
    except Exception as e:
        print(f"[lookup] contact lookup failed for {want_pad}: {e}")
    return {}
