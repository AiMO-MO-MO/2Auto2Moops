"""
Direct portal provisioning (eliminate-ITF path).

Phase 0: form discovery. `inspect_form(page, url)` dumps every fillable control on
a page (Admin Portal Create Customer, LaundroPortal location/user, etc.) so the
field-fill can be wired against real selectors -- never guessed.

Fill routines added here will FILL ONLY and never submit: the human reviews the
populated form and clicks save, same gate as the ITF form and card emails.
"""

import re

ADMIN_BASE = "https://admintools.mitechisys.com"
CREATE_CUSTOMER_URL = f"{ADMIN_BASE}/customers/create"
CUSTOMERS_URL = f"{ADMIN_BASE}/customers"
PORTAL_BASE = "https://portal.mitechisys.com"
SF_BASE = "https://trycentssf.lightning.force.com"


def inspect_sf_search(page, query):
    """Read-only DISCOVERY: type `query` into the Salesforce global search and dump the
    typeahead dropdown (shadow-piercing) so the dedupe candidate parsing can be wired. Types
    only -- no submit, no create. Run INSIDE the persistent console so the Okta session stays
    authenticated (a fresh browser launch re-triggers Okta)."""
    home = f"{SF_BASE}/lightning/page/home"
    print(f"[NAV] SF home: {home}")
    page.goto(home, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)
    if "okta.com" in (page.url or ""):
        print("[SF] Bounced to Okta -- sign in in this window, then re-run `sf-search`.")
        return
    box = page.locator('#global-search-01')
    if box.count() == 0:
        print("[SF] Global search (#global-search-01) not found -- is SF loaded / signed in?")
        return
    try:
        box.first.click()
        box.first.fill(query)
        page.wait_for_timeout(3000)  # let the typeahead dropdown render
    except Exception as e:
        print(f"[SF] Couldn't type into the global search ({e}).")
        return
    items = page.evaluate(
        """() => {
            const out = [];
            const visit = (root) => {
                root.querySelectorAll('[role=option], lightning-base-combobox-item, li.slds-listbox__item').forEach(o => {
                    const t = (o.innerText || o.getAttribute('aria-label') || '').replace(/\\s+/g,' ').trim().slice(0,140);
                    if (t) out.push({kind:'option', text:t, href:''});
                });
                root.querySelectorAll('a[href*="/lightning/r/"]').forEach(a => {
                    const t = (a.innerText || '').replace(/\\s+/g,' ').trim().slice(0,90);
                    out.push({kind:'link', text:t, href:(a.getAttribute('href')||'').slice(0,100)});
                });
                root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) visit(el.shadowRoot); });
            };
            visit(document);
            const seen = new Set(), uniq = [];
            for (const x of out) { const k = x.kind+'|'+x.text+'|'+x.href; if (!seen.has(k)) { seen.add(k); uniq.push(x); } }
            return uniq.slice(0, 80);
        }"""
    )
    print(f"\n=== SF SEARCH TYPEAHEAD for {query!r} ({len(items)} items) ===")
    for it in items:
        if it.get("kind") == "link":
            print(f"  [link]   {it['text']!r} -> {it['href']!r}")
        else:
            print(f"  [option] {it['text']!r}")
    print("\n  -> Paste back so the dedupe candidate parsing (name / type / account / record id) "
          "can be wired.")


def inspect_form(page, url):
    """Read-only: navigate to `url` and dump all input/select/textarea controls
    with their label, name, id, type, placeholder, current value, and (for selects)
    the first dozen options. Writes nothing."""
    print(f"[NAV] Form: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # Wait for real controls to render. LP builds some forms client-side, and a page
    # behind a lost session shows none -- fall back after a short wait either way.
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('input,select,textarea,button').length > 0", timeout=8000)
    except Exception:
        page.wait_for_timeout(2000)
    # Salesforce Lightning lazy-renders its body well after the page chrome (the search box
    # satisfies the wait above immediately), so give it extra settle before dumping.
    cur = page.url or ""
    if "okta.com" in cur:
        print("[SF] Bounced to Okta sign-in -- sign in in this window, then re-run the inspect.")
    elif "lightning.force.com" in cur:
        page.wait_for_timeout(7000)
    _report_controls(page, url)


def _condense(name, n=10):
    """Alphanumeric, condensed to n chars (for 10-char company name / login)."""
    return re.sub(r'[^A-Za-z0-9]', '', name or '')[:n]


_KEEP_UPPER = {"LLC", "INC", "LP", "LLP", "USA", "US", "NY", "NJ", "DBA", "II", "III", "IV"}

# Business-type words dropped from the ROOT login (but kept in Customer Name / 10-char).
_LOGIN_DROP = {
    "laundromat", "laundromats", "laundry", "laundries", "washateria", "cleaners", "cleaner",
    "company", "co", "llc", "inc", "incorporated", "corp", "corporation", "center", "centre",
    "services", "service", "group",
}


def _proper_case(s):
    """Title-case dealer ALL-CAPS input while preserving digits and known acronyms
    (LLC, INC, II ...). 'PATS WASH PHASE 1' -> 'Pats Wash Phase 1'."""
    if not s:
        return s
    out = []
    for w in s.split():
        if w.upper() in _KEEP_UPPER:
            out.append(w.upper())
        elif w.isdigit():
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def next_customer_id(page):
    """Suggest the next Customer ID: max of the normal '0xxxx' series + 1, zero-padded
    to 5. The misnumbered rows (10347+, 80211) do NOT start with 0, so they're excluded.

    Reuses the cached /customers scrape -- the system run's dedup pass already read the whole
    list THIS run (and resets the cache per run), so we skip a second ~2000-row nav+scrape.
    Falls back to a direct read when the cache is empty (standalone/legacy paths).
    Best-effort -- returns '' if the list can't be read. Always VERIFY before submit."""
    from core import portal
    nums = set()
    try:
        # use_cache=True: returns the cached list with no nav if it's populated; otherwise it
        # does ONE scrape (and caches it). Either way, no duplicate fetch.
        for c in (portal.scrape_admin_customers(page, use_cache=True) or []):
            cid = (c.get("cust_id") or "").strip()
            if re.fullmatch(r"0\d{4}", cid):
                nums.add(int(cid))
    except Exception as e:
        print(f"[INFO] Cached customer list unavailable ({e}) -- reading IDs directly.")
        nums = set()
    if not nums:
        print(f"[NAV] Reading customer IDs: {CUSTOMERS_URL}")
        page.goto(CUSTOMERS_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_function("() => !document.body.textContent.includes('{{')", timeout=15000)
        except Exception:
            page.wait_for_timeout(2000)
        ids = page.evaluate("() => (document.body.innerText.match(/\\b0\\d{4}\\b/g) || [])")
        nums = {int(x) for x in ids}
    if not nums:
        print("[WARN] No 0xxxx customer IDs found -- enter Customer ID manually")
        return ""
    nxt = max(nums) + 1
    print(f"[INFO] Highest 0xxxx ID = {max(nums):05d} -> suggest {nxt:05d} (from /customers list)")
    return f"{nxt:05d}"


def fill_create_customer(page, data, cust_id="", preview=False):
    """Build the Create Customer field->value plan and (unless preview) fill it.
    FILL ONLY -- never submits. With preview=True it just prints the plan and stops
    (no navigation to the form) -- the cheap mapping-feedback loop.

    data keys: so_id, customer_name, contact_name, contact_email, contact_phone
    """
    raw_name = data.get("customer_name", "")
    biz = raw_name.split(" - ")[0].strip() or raw_name      # drop the " - Location" suffix
    biz = _proper_case(biz)                                 # dealers enter ALL CAPS -> "Pats Wash Phase 1"
    ten = _condense(biz, 10).upper()                        # 10-char company name, UPPERCASE no spaces (full name)
    if len(ten) < 10:                                       # min 10 chars: "Spin Cycle" -> SPINCYCLE (9) is too short
        ten = (ten + "LDMT" * 3)[:10]                       # pad with LDMT (laundromat) as needed, cap at 10
    # ROOT/API login = business name minus the type-suffix, concatenated, + "Admin".
    # "The Graybill Company" -> "TheGraybillAdmin"; "Pure Wash Laundromats" -> "PureWashAdmin".
    login_words = [w for w in biz.split() if w.lower() not in _LOGIN_DROP]
    login_base = re.sub(r'[^A-Za-z0-9]', '', "".join(login_words)) or _condense(biz)
    root = f"{login_base}Admin" if login_base else ""
    op_type = "Laundromat"  # system orders only for now; route/multi-family support later

    contact_name = _proper_case(data.get("contact_name", ""))   # "TROY LESTER" -> "Troy Lester"
    contact_email = data.get("contact_email", "")               # email left EXACT (no case change)

    # (field, value, is_select, verify). Receipt Header + Internal Notes left blank.
    plan = [
        ("Customer_ID", cust_id, False, True),
        ("Customer_Name", biz, False, True),
        ("Protocol_Password", ten, False, True),
        ("Customer_Region_ID", "US", True, False),
        ("Laundry_Operation_Type_ID", op_type, True, False),
        ("Primary_Contact_Name", contact_name, False, False),
        ("Primary_Contact_Email", contact_email, False, False),
        ("Primary_Contact_Phone", data.get("contact_phone", ""), False, False),
        ("Root_Login", root, False, True),
    ]

    print("\n[PLAN] Create Customer  field = value:")
    for field, value, _is_sel, verify in plan:
        print(f"  {field:28s} = {value!r}{'   <-- VERIFY' if verify else ''}")

    if preview:
        print("\n[PREVIEW] Nothing navigated or filled. Correct the mapping and re-run.")
        return

    print(f"\n[NAV] Create Customer: {CREATE_CUSTOMER_URL}")
    page.goto(CREATE_CUSTOMER_URL, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_function(
            "() => document.querySelector('[name=\"Customer_Name\"]') !== null", timeout=15000)
    except Exception:
        page.wait_for_timeout(2000)

    for field, value, is_sel, _verify in plan:
        if not value:
            continue
        try:
            if is_sel:
                page.locator(f'select[name="{field}"]').first.select_option(label=value)
            else:
                page.locator(f'[name="{field}"]').first.fill(str(value))
            print(f"  filled {field}: {value}")
        except Exception as e:
            print(f"  [skip] {field}: {e}")
    # Is_Active + add_new_billing_account: left at their default (checked).

    print("\n[PAUSE] Review the filled form in the browser and submit it yourself if correct.")
    print("        NOTHING was submitted.")


def fill_api_user(page, cust_id="", is_fortis=False):
    """Post-save Admin finalize (the 'save again' pass). Per Matt's finalize:
      1. Check 'Payment Processing Reports Stripe' under Extended Features -- UNLESS the order
         is Fortis/EBT (those process on Fortis, not Stripe, so the flag must NOT be set).
      2. Create the API user named like the ROOT login but with 'Admin' -> 'API'
         (PureWashAdmin -> PureWashAPI), access = POS, password auto-generated.
    Reads Root_Login from the page so it tracks any manual rename. FILL ONLY --
    human Saves, then clicks the green Login button to enter LaundroPortal.
    """
    if cust_id:
        url = f"{ADMIN_BASE}/customers/{cust_id}"
        # The create-customer submit redirects the browser to THIS same /customers/<id> page. If
        # our goto fires while that redirect is still in flight, Chromium raises "interrupted by
        # another navigation". So: skip the goto if we're already on the page, otherwise retry
        # through the race (the competing navigation is heading to the same URL anyway).
        if (page.url or "").rstrip("/").endswith(f"/customers/{cust_id}"):
            print(f"[NAV] Already on customer {cust_id} (create-customer redirect) -- skip goto")
        else:
            print(f"[NAV] Customer: {url}")
            for attempt in range(3):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    break
                except Exception as e:
                    if "interrupted by another navigation" in str(e) or "Timeout" in str(e):
                        print(f"[NAV] customer-page nav race ({attempt + 1}/3) -- settling, retrying")
                        page.wait_for_timeout(1500)
                        continue
                    raise
        try:
            page.wait_for_function(
                "() => document.querySelector('[name=\"Root_Login\"]') !== null", timeout=15000)
        except Exception:
            page.wait_for_timeout(2000)

    root = ""
    try:
        root = (page.locator('[name="Root_Login"]').first.input_value() or "").strip()
    except Exception:
        pass
    if not root:
        print("[WARN] Could not read Root_Login -- is the customer saved? (run after Create Customer)")
        return

    # API user name: ROOT login with 'Admin' -> 'API'  (PureWashAdmin -> PureWashAPI)
    api_name = (root[:-5] + "API") if root.lower().endswith("admin") else (root + "API")

    # (1) Enable 'Payment Processing Reports Stripe' -- but NOT for Fortis/EBT customers. They
    # process on Fortis, not Stripe, so the Stripe reporting feature must not be set on the cust id.
    if is_fortis:
        print("  Stripe payment reporting: SKIPPED (Fortis/EBT processor -- not a Stripe customer)")
    else:
        # The checkbox names are opaque (extended_features[NN]), so match on the rendered label.
        stripe_ok = page.evaluate(
            """() => {
                const t = 'payment processing reports stripe';
                for (const l of document.querySelectorAll('label')) {
                    if ((l.innerText || '').toLowerCase().includes(t)) {
                        const cb = l.querySelector('input[type="checkbox"]')
                                || (l.htmlFor ? document.getElementById(l.htmlFor) : null);
                        if (cb && cb.type === 'checkbox') { if (!cb.checked) cb.click(); return true; }
                    }
                }
                return false;
            }"""
        )
        print(f"  Stripe payment reporting: {'checked' if stripe_ok else 'NOT FOUND -- tick manually'}")

    # (2) API user (POS). Skip if one with this name already exists on the account.
    existing = page.evaluate(
        """() => Array.from(document.querySelectorAll('input[name^=\"api_user_name_\"]'))
                     .map(i => (i.value || '').trim())"""
    )
    if api_name in existing:
        print(f"  API user {api_name!r} already exists -- not re-adding")
    else:
        print(f"\n[PLAN] new API user = {api_name!r}   access = 'POS'   (password auto-generated)")
        try:
            page.locator('[name="new_api_user_name"]').first.fill(api_name)
            page.locator('select[name="new_api_user_access"]').first.select_option(label="POS")
            print(f"  filled new_api_user_name: {api_name}")
            print(f"  filled new_api_user_access: POS")
        except Exception as e:
            print(f"  [skip] API user: {e}")

    print("\n[PAUSE] Review (Stripe feature + API user), then Save. After saving, click the green")
    print("        Login button (top) to enter LaundroPortal for Add Location. NOTHING was submitted.")


def fill_location(page, cust_id, addr):
    """LaundroPortal Add Location (EditLocation.php) -- FILL ONLY.

    addr keys: location_id, street, city, state, zip, customer_name.
    Logs into LaundroPortal for cust_id (admin bridge), opens the Add Location form,
    and fills address + Description ('Customer Name - Street') + Basic Features.
    Location ID defaults to 0100001 (first, card-capable). Timezone + Portal fee are
    left at the form defaults for the human to confirm. Nothing is submitted.
    """
    url = f"{PORTAL_BASE}/EditLocation.php"
    print(f"[NAV] Add Location: {url}")

    def _open_and_check():
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_function(
                "() => document.getElementById('location_id') !== null", timeout=8000)
            return True
        except Exception:
            return False

    from core.portal import login_to_portal, current_portal_customer

    # Establish the form AND the right customer scope. The LP session may already be
    # logged in -- but possibly to the WRONG customer (e.g. left scoped by a dedup
    # lookup). So drive the admin->LP bridge for THIS cust_id whenever the form is
    # missing OR the scope doesn't match, then re-check. Only abort if still wrong.
    have_form = _open_and_check()
    cur = current_portal_customer(page)
    if not have_form or (cur and cur != str(cust_id)):
        if cur and cur != str(cust_id):
            print(f"[INFO] LaundroPortal scoped to {cur}, need {cust_id} -- re-logging via admin bridge")
        else:
            print("[INFO] Add Location form not present -- establishing LaundroPortal session via admin bridge")
        login_to_portal(page, cust_id)
        have_form = _open_and_check()
        cur = current_portal_customer(page)

    # GUARD: the portal acts on whichever customer is logged in -- never write to the wrong one.
    if cur and cur != str(cust_id):
        print(f"[ABORT] LaundroPortal still scoped to {cur}, not {cust_id} (admin->LP bridge didn't")
        print(f"        take). In the browser, click LOGIN for {cust_id}, then re-run `addloc`.")
        return
    if not have_form:
        print(f"[WARN] Add Location form not present even after login. Click LOGIN for {cust_id} in")
        print("       LaundroPortal, then re-run addloc.")
        return
    if not cur:
        print(f"[WARN] Could not confirm portal customer -- verify the sidebar shows {cust_id} before saving.")

    loc_id = addr.get("location_id") or "0100001"
    street = addr.get("street", "")
    descr = addr.get("customer_name", "")  # Description = just the name (per Matt), not "name - street"
    tz = addr.get("timezone", "")
    seats = addr.get("seats", 0) or 0

    print("\n[PLAN] Add Location (VERIFY before save):")
    print(f"  Location ID    = {loc_id!r}   <-- VERIFY (first=0100001; +1 if existing; 02xxxxx if no cards)")
    print(f"  Street         = {street!r}")
    print(f"  City           = {addr.get('city', '')!r}")
    print(f"  State          = {addr.get('state', '')!r}")
    print(f"  Zip            = {addr.get('zip', '')!r}")
    print(f"  Timezone       = {tz!r}" + ("" if tz else "   <-- set manually"))
    print(f"  Seat licenses  = {seats!r}" + ("" if seats else "   <-- set manually"))
    print(f"  Description     = {descr!r}")
    print(f"  Basic Features  = checked   |   Portal fee -> defaults $50, confirm")

    report = page.evaluate(
        """
        (d) => {
          const set = (el, v) => { if (!el || v == null || v === '') return false;
            el.value = v; el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true})); return true; };
          const byId = (id) => document.getElementById(id);
          const r = {};
          r.location_id = set(byId('location_id'), d.location_id);
          r.street = set(byId('st_num'), d.street);
          r.city = set(byId('city'), d.city);
          r.state = set(byId('state_prov'), d.state);
          r.descr = set(byId('descr'), d.descr);
          r.timezone = set(byId('time_zone_select'), d.timezone);
          r.seats = false;
          if (d.seats) {
            document.querySelectorAll('[id="portal_fee"]').forEach(f => {
              if (f.type === 'number') { if (set(f, d.seats)) r.seats = true; }
            });
          }
          r.zip = false;
          for (const l of document.querySelectorAll('label')) {
            if (/zip|postal/i.test(l.innerText || '')) {
              const grp = l.closest('.form-group, .col, .form-row, div') || l.parentElement;
              const inp = grp && grp.querySelector('input');
              if (inp) { r.zip = set(inp, d.zip); break; }
            }
          }
          r.basic = false;
          for (const l of document.querySelectorAll('label')) {
            if ((l.innerText || '').trim().toLowerCase() === 'basic features') {
              let cb = l.querySelector('input[type=checkbox]');
              if (!cb && l.htmlFor) cb = document.getElementById(l.htmlFor);
              if (cb && cb.type === 'checkbox') { if (!cb.checked) cb.click(); r.basic = true; }
              break;
            }
          }
          return r;
        }
        """,
        {"location_id": loc_id, "street": street, "city": addr.get("city", ""),
         "state": addr.get("state", ""), "zip": addr.get("zip", ""), "descr": descr,
         "timezone": tz, "seats": str(seats) if seats else ""},
    )
    for k, v in report.items():
        print(f"  {'filled' if v else '[skip]'} {k}")

    print("\n[PAUSE] Set Timezone + Portal fee, review the rest, then Save the location.")
    print("        NOTHING was submitted.")


def fill_user(page, cust_id, contact):
    """LaundroPortal Add User (UserEdit.php) -- FILL ONLY.

    Login = FirstInitial + LastName (Troy Lester -> TLester), Access = Admin,
    name/email/phone from the primary contact, a random 4-digit password in both
    fields (resets when the intro email is sent). GUARDED -- aborts if the portal
    is not scoped to cust_id, so a user is never created on the wrong account.
    """
    url = f"{PORTAL_BASE}/UserEdit.php"
    print(f"[NAV] Add User: {url}")

    def _open_and_check():
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_function(
                "() => document.getElementById('user-login') !== null", timeout=8000)
            return True
        except Exception:
            return False

    if not _open_and_check():
        from core.portal import login_to_portal
        print("[INFO] Form not present -- trying LaundroPortal session via admin bridge")
        login_to_portal(page, cust_id)
        if not _open_and_check():
            print("[WARN] Add User form not present. In the browser, click LOGIN for this customer")
            print("       (LaundroPortal) first, then re-run adduser.")
            return

    # GUARD: confirm the portal is scoped to the intended customer before writing.
    from core.portal import current_portal_customer
    cur = current_portal_customer(page)
    if cur and cur != str(cust_id):
        print(f"[ABORT] LaundroPortal is scoped to customer {cur}, not {cust_id}. Click LOGIN for")
        print(f"        {cust_id} first -- NOT filling (avoids a user on the wrong account).")
        return
    if not cur:
        print(f"[WARN] Could not confirm portal customer -- verify the sidebar shows {cust_id} before saving.")

    page.wait_for_timeout(1000)  # let the form settle before filling (pacing)
    import random
    name = _proper_case(contact.get("contact_name", ""))
    parts = name.split()
    login = (parts[0][0].upper() + parts[-1]) if len(parts) >= 2 else (parts[0] if parts else "")
    email = contact.get("contact_email", "")
    phone = contact.get("contact_phone", "")
    pw = f"{random.randint(0, 9999):04d}"

    print("\n[PLAN] Add User (VERIFY before save):")
    print(f"  Login    = {login!r}")
    print(f"  Access   = 'Admin'")
    print(f"  Name     = {name!r}")
    print(f"  Email    = {email!r}")
    print(f"  Phone    = {phone!r}")
    print(f"  Password = {pw!r} (both fields; resets when the intro email sends)")

    report = page.evaluate(
        """
        (d) => {
          const set = (el, v) => { if (!el || v == null || v === '') return false;
            el.value = v; el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true})); return true; };
          const byId = (id) => document.getElementById(id);
          const r = {};
          r.login = set(byId('user-login'), d.login);
          r.name = set(byId('user-name'), d.name);
          r.email = set(byId('user-email'), d.email);
          r.phone = set(byId('user-phone'), d.phone);
          r.password = set(byId('password'), d.pw);
          r.confirm = set(byId('password-confirm'), d.pw);
          r.access = false;
          const acc = byId('user-access');
          if (acc) {
            for (const o of acc.options) {
              if ((o.text || '').trim() === 'Admin') {
                acc.value = o.value; acc.dispatchEvent(new Event('change', {bubbles:true}));
                r.access = true; break;
              }
            }
          }
          return r;
        }
        """,
        {"login": login, "name": name, "email": email, "phone": phone, "pw": pw},
    )
    for k, v in report.items():
        print(f"  {'filled' if v else '[skip]'} {k}")
    print("\n[PAUSE] Review the user, then Save. NOTHING was submitted.")


_CONTROLS_JS = """
() => {
  const labelFor = (el) => {
    const root = el.getRootNode();
    if (el.id && root.querySelector) {
      try { const l = root.querySelector('label[for="' + (window.CSS && CSS.escape ? CSS.escape(el.id) : el.id) + '"]');
        if (l && l.innerText.trim()) return l.innerText.trim(); } catch (e) {}
    }
    const p = el.closest('.form-group, .row, .form-row, div, fieldset, lightning-input, lightning-combobox, lightning-textarea');
    if (p) { const l = p.querySelector('label'); if (l && l.innerText.trim()) return l.innerText.trim(); }
    return '';
  };
  const out = [];
  // Recurse into shadow roots -- Salesforce Lightning (LWC) puts controls in shadow DOM.
  const visit = (root) => {
    root.querySelectorAll('input, select, textarea').forEach(el => {
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || tag).toLowerCase();
      if (['hidden', 'submit', 'button', 'reset'].includes(type)) return;
      let opts = [];
      if (tag === 'select') opts = Array.from(el.options).map(o => (o.text||'').trim()).filter(Boolean).slice(0, 12);
      out.push({label: labelFor(el) || el.getAttribute('aria-label') || '',
                name: el.getAttribute('name')||'', id: el.id||'',
                type: type, placeholder: el.getAttribute('placeholder')||'',
                value: (el.value||'').slice(0, 40), options: opts});
    });
    root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) visit(el.shadowRoot); });
  };
  visit(document);
  return out;
}
"""


def _report_controls(page, label):
    controls = page.evaluate(_CONTROLS_JS)
    print(f"\n=== FORM CONTROLS ({len(controls)}) — {label} ===")
    for c in controls:
        line = f"  [{c['type']}] label={c['label']!r} name={c['name']!r} id={c['id']!r}"
        if c["placeholder"]:
            line += f" ph={c['placeholder']!r}"
        if c["value"]:
            line += f" val={c['value']!r}"
        if c["options"]:
            line += f" options={c['options']}"
        print(line)
    if not controls:
        diag = page.evaluate(
            "() => ({url: location.href, title: document.title, "
            "body: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 300)})")
        print(f"  [DIAG] landed url = {diag['url']!r}")
        print(f"  [DIAG] title      = {diag['title']!r}")
        print(f"  [DIAG] body[:300] = {diag['body']!r}")
    btns = page.evaluate(
        """() => {
            const out = [];
            const visit = (root) => {
                root.querySelectorAll('button,input[type=submit],input[type=button],a.btn,a[role=button],[role=button]').forEach(b => {
                    const text = (b.innerText||b.value||b.getAttribute('aria-label')||'').trim().slice(0,40);
                    if (text) out.push({text, id:b.id||'', cls:(b.className||'').toString().slice(0,50)});
                });
                root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) visit(el.shadowRoot); });
            };
            visit(document);
            return out;
        }""")
    if btns:
        print("  --- buttons / links ---")
        for b in btns:
            print(f"  [button] text={b['text']!r} id={b['id']!r} cls={b['cls']!r}")
    # Record/nav links -- on a Salesforce search-results page the candidates ARE links to
    # /lightning/r/<Object>/<id>/view, so dump them (shadow-piercing) to read the matches.
    links = page.evaluate(
        """() => {
            const out = [];
            const visit = (root) => {
                root.querySelectorAll('a[href*="/lightning/r/"], a[href*="/lightning/"]').forEach(a => {
                    const text = (a.innerText || a.getAttribute('title') || '').trim().slice(0, 60);
                    const href = (a.getAttribute('href') || '').slice(0, 100);
                    if (text) out.push({text, href});
                });
                root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) visit(el.shadowRoot); });
            };
            visit(document);
            const seen = new Set(), uniq = [];
            for (const l of out) { const k = l.text + '|' + l.href; if (!seen.has(k)) { seen.add(k); uniq.push(l); } }
            return uniq.slice(0, 60);
        }""")
    if links:
        print("  --- record / nav links ---")
        for l in links:
            print(f"  [link] text={l['text']!r} href={l['href']!r}")
    print("\n  -> Paste this back so the field-fill can be wired (fill only, no submit).")


def inspect_payment(page, location_key):
    """Reach the per-location Payment Processing page the way a human does: load the
    Location panel (sets the 'current location' context), then CLICK the Payment
    Processing quick-link -- a direct goto to PaymentProcessing.php bounces back to
    the panel. Dumps the resulting form's controls."""
    panel = f"{PORTAL_BASE}/LocationPanel.php?Location_Key={location_key}"
    print(f"[NAV] Location panel: {panel}")
    page.goto(panel, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_selector('a[href*="PaymentProcessing.php"]', timeout=10000)
    except Exception:
        print("[WARN] Payment Processing link not found on the location panel")
    try:
        page.locator('a[href*="PaymentProcessing.php"]').first.click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)
    except Exception as e:
        print(f"[WARN] Could not open Payment Processing: {e}")
    _report_controls(page, page.url)


def open_stripe(page, cust_id, location_key):
    """Initiate Stripe at the LOCATION level: navigate (guarded to cust_id) to the
    location's Payment Processing page and hand off. Deliberately does NOT auto-poke
    the merchant selects -- it's a payments screen, the New-vs-Existing merchant
    choice is a judgment call, and the application opens in a separate step. Per
    'initiate, don't fill'. Nothing is changed or submitted."""
    from core.portal import login_to_portal, current_portal_customer
    panel = f"{PORTAL_BASE}/LocationPanel.php?Location_Key={location_key}"

    def _open_panel():
        # A direct LocationPanel hit bounces to admintools/customers if the LP session
        # isn't established -- so treat a non-portal URL as failure and let the caller bridge.
        try:
            page.goto(panel, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"[INFO] Location panel nav interrupted ({e}) -- will establish the session.")
            return False
        return "portal.mitechisys.com" in (page.url or "")

    print(f"[NAV] Location panel: {panel}")
    ok = _open_panel()
    cur = current_portal_customer(page)
    if not ok or (cur and cur != str(cust_id)):
        if cur and cur != str(cust_id):
            print(f"[INFO] LaundroPortal scoped to {cur}, need {cust_id} -- re-logging via admin bridge")
        else:
            print("[INFO] Location panel not loaded -- establishing LaundroPortal session via admin bridge")
        login_to_portal(page, cust_id)
        ok = _open_panel()
        cur = current_portal_customer(page)

    if cur and cur != str(cust_id):
        print(f"[ABORT] LaundroPortal scoped to {cur}, not {cust_id} -- not opening Stripe.")
        return False
    if not ok:
        print(f"[WARN] Couldn't load the location panel for {location_key}. Click LOGIN for {cust_id} in")
        print(f"       LaundroPortal, then run `stripe {cust_id} {location_key}`.")
        return False
    if not cur:
        print(f"[WARN] Could not confirm portal customer -- verify the sidebar shows {cust_id}.")

    try:
        page.wait_for_selector('a[href*="PaymentProcessing.php"]', timeout=20000)
        page.locator('a[href*="PaymentProcessing.php"]').first.click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)
    except Exception as e:
        # LP can be slow to render the sidebar after location save -- try reloading once.
        print(f"[INFO] Payment Processing link not found ({e}) -- reloading and retrying.")
        try:
            page.reload(wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            page.wait_for_selector('a[href*="PaymentProcessing.php"]', timeout=10000)
            page.locator('a[href*="PaymentProcessing.php"]').first.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)
        except Exception as e2:
            print(f"[WARN] Could not open Payment Processing after reload ({e2}) -- do Stripe manually.")

    print(f"\n[STRIPE] Payment Processing open for location {location_key} (customer {cust_id}).")

    # 1) Merchant state. The merchant-action select carries '* Add New / * Add Existing'
    # and, once created, an 'acct_...' option. Read-only probe first.
    m = page.evaluate(
        """() => {
            const sels = [...document.querySelectorAll('select')];
            const ms = sels.find(s => [...s.options].some(o => /add new/i.test(o.text)));
            if (!ms) return {found: false};
            const hasAcct = [...ms.options].some(o => /^\\s*acct_/i.test(o.text));
            const add = [...ms.options].find(o => /add new/i.test(o.text));
            return {found: true, hasAcct, addValue: add ? add.value : null};
        }"""
    )
    if not m.get("found"):
        print("[WARN] Merchant-account control not found -- do Stripe manually on this page.")
        return False

    if m.get("hasAcct"):
        print("  Merchant account already exists -- skipping creation (no duplicate).")
    else:
        # Creating a Connect merchant is the one irreversible money action -- so CONFIRM first
        # (human presses Enter), THEN the script initiates it. The click MUST go through a JS
        # evaluate, not Playwright .click()/.select_option(): the control opens a Stripe
        # onboarding popup and Playwright blocks on that navigation (same gotcha as the Save
        # click, lesson #1).
        print(f"\n  [CONFIRM] About to create a NEW Stripe merchant account for location "
              f"{location_key} (customer {cust_id}).")
        # Typed skip ('s'), NOT Ctrl+C -- a SIGINT tears down the browser and the rest of the chain
        # (cards / End Customer / config) then dies with "browser has been closed".
        try:
            resp = input("           Press Enter to create it (script clicks 'Add New Merchant'), "
                         "or type s + Enter to skip: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  [SKIP] Stripe not set up.")
            return False
        if resp in ("s", "skip", "n", "no"):
            print("\n  [SKIP] Stripe not set up (chose skip) -- continuing the run.")
            return False
        initiated = page.evaluate(
            """(addValue) => {
                // Prefer an explicit 'Add New Merchant' button if the page has one...
                const ctrls = [...document.querySelectorAll('button, a, input[type=button], input[type=submit]')];
                const btn = ctrls.find(e => /add\\s*new\\s*merchant/i.test((e.innerText || e.value || '').trim()));
                if (btn) { btn.click(); return 'button'; }
                // ...otherwise pick the 'Add New' option on the merchant dropdown (fires change).
                const sels = [...document.querySelectorAll('select')];
                const ms = sels.find(s => [...s.options].some(o => /add new/i.test(o.text)));
                if (ms && addValue != null) {
                    ms.value = addValue;
                    ms.dispatchEvent(new Event('change', {bubbles: true}));
                    return 'select';
                }
                return '';
            }""",
            m.get("addValue"),
        )
        if initiated:
            print(f"  Initiated 'Add New Merchant' (via {initiated}). Stripe onboarding opens in a")
            print("  SEPARATE tab -- ignore it (no need to wait/close). Refreshing this page so the")
            print("  new merchant account shows...")
        else:
            print("  [WARN] Couldn't find the 'Add New Merchant' control -- click it manually.")
        # After initiating: let the click's navigation settle, reload to show the new
        # merchant account, then verify we're still on PaymentProcessing (the reload can
        # land elsewhere if the click triggered a redirect). Navigate back if needed.
        payment_url = page.url  # capture before any navigation
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        try:
            page.reload(wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
        except Exception as e:
            print(f"  [INFO] Reload interrupted ({e}) -- navigating back to Payment Processing.")
        # If reload took us off the Payment Processing page, go back.
        if "PaymentProcessing" not in (page.url or ""):
            print(f"  [INFO] Page drifted to {page.url!r} -- navigating back to Payment Processing.")
            try:
                page.goto(payment_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
            except Exception as e:
                print(f"  [WARN] Couldn't navigate back to Payment Processing ({e}). "
                      "Run `stripe {cust_id} {location_key}` after settling.")

    # 2) Account Access -> grant the portal user (the 'select a user to grant access' dropdown +
    # 'Assign'). The Account Access control only appears AFTER the new merchant has registered --
    # creating it opens a separate Stripe tab and the LP page needs a refresh, which can lag a few
    # seconds (Matt: assign access AFTER the refresh from creating the Stripe account). So refresh
    # + re-check a few times before giving up.
    access_url = page.url
    ACCESS_JS = """() => {
        const sels = [...document.querySelectorAll('select')];
        const us = sels.find(s => [...s.options].some(o => /select a user to grant access/i.test(o.text)));
        if (!us) return {found: false};
        const real = [...us.options].find(o => o.text.trim() && !/select a user to grant access/i.test(o.text));
        return {found: true, value: real ? real.value : null, label: real ? real.text.trim() : ''};
    }"""
    ua = {"found": False}
    for attempt in range(4):
        try:
            ua = page.evaluate(ACCESS_JS)
        except Exception as e:
            print(f"  [INFO] Couldn't read Account Access control ({e}) -- will refresh and retry.")
            ua = {"found": False}
        if ua.get("found"):
            break
        if attempt < 3:
            print(f"  Account Access not visible yet (merchant still registering) -- refresh {attempt + 1}/3 ...")
            try:
                page.reload(wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                if "PaymentProcessing" not in (page.url or "") and access_url:
                    page.goto(access_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1500)
            except Exception:
                pass
    if not ua.get("found"):
        print("  [INFO] Account Access control not found after refreshes -- grant access manually.")
    elif not ua.get("value"):
        print("  Account Access: no user available to grant (already granted, or no portal user yet).")
    else:
        print(f"  Granting Account Access to {ua['label']!r} ...")
        try:
            page.locator('select').filter(has_text="select a user to grant access").first.select_option(value=ua["value"])
            page.get_by_role("button", name="Assign").first.click()
            page.wait_for_timeout(2000)
            # Verify: re-read the access control. If assigned, the user should no longer
            # appear in the "select a user" dropdown (already listed in the access table).
            try:
                ua2 = page.evaluate(
                    """() => {
                        const sels = [...document.querySelectorAll('select')];
                        const us = sels.find(s => [...s.options].some(o => /select a user to grant access/i.test(o.text)));
                        if (!us) return {found: false};
                        const real = [...us.options].find(o => o.text.trim() && !/select a user to grant access/i.test(o.text));
                        return {found: true, value: real ? real.value : null, label: real ? real.text.trim() : ''};
                    }"""
                )
                if not ua2.get("value"):
                    print(f"  [OK] Bank access assigned -- {ua['label']!r} no longer in grant dropdown (already granted).")
                else:
                    print(f"  [WARN] {ua['label']!r} still in grant dropdown -- assignment may not have saved. Verify manually.")
            except Exception:
                print(f"  Assigned {ua['label']!r} -- could not re-read dropdown to verify (check manually).")
        except Exception as e:
            print(f"  [WARN] Could not grant access ({e}) -- do it manually (select user -> Assign).")

    # Pause for the human whenever the Account Access control is present: confirm the auto-grant
    # actually saved and assign any ADDITIONAL users (multi-user accounts like SBL Ventures often
    # need more than one). The chain waits here -- nothing proceeds until you're done.
    if ua.get("found"):
        try:
            input("\n  [CONFIRM] Bank access -- verify the grant above saved, and assign any more "
                  "users now (select a user -> Assign). Press Enter when done...")
        except (EOFError, KeyboardInterrupt):
            print("  [STRIPE] Continuing without further access changes.")

    print("\n[STRIPE] Done. Verify the merchant account + Account Access on the page. The Stripe")
    print("         application/bank details are completed by Cents/the customer -- not here.")
    return True


def send_intro_email(page, cust_id):
    """Admin customer page final step: click the 'Send Intro Email' envelope for each
    Admin User (a span.cursor-pointer > i.fa-envelope in the #adminUsers table -- not a
    button). Verifies the page is the right customer first; gated by an Enter confirm because
    sending also resets the user's password and must not resend to someone who already got it."""
    url = f"{ADMIN_BASE}/customers/{cust_id}"
    print(f"[NAV] Customer: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1500)

    head = ""
    try:
        head = page.locator("h1, h3").first.inner_text(timeout=3000) or ""
    except Exception:
        pass
    if str(cust_id) not in head and str(cust_id) not in page.url:
        print(f"[ABORT] Page doesn't look like customer {cust_id} (header {head!r}) -- not sending.")
        return

    page.reload(wait_until="domcontentloaded")  # portal user must be saved to appear (doc: refresh)
    page.wait_for_timeout(1500)
    env = page.locator('#adminUsers span.cursor-pointer:has(i.fa-envelope)')
    n = env.count()
    if n == 0:  # fallback if the table isn't wrapped in #adminUsers
        env = page.locator('span.cursor-pointer:has(i.fa-envelope)')
        n = env.count()
    if n == 0:
        print(f"[WARN] No 'Send Intro Email' envelope -- is the portal user saved? It must be saved")
        print(f"       (and appear in Admin Users) first. Save it, then run `intro {cust_id}`.")
        return

    # Confirm before sending -- the intro also RESETS the user's password and shouldn't be resent
    # to a user who already got it (Matt: SO-20070 resent an already-sent intro). Skip via a TYPED
    # 's' (NOT Ctrl+C): a SIGINT tears down the Playwright browser connection, so the next chain
    # step (config nav) then dies with "browser has been closed" -- ending the run (Matt: SO-19738).
    print(f"\n  [CONFIRM] Send the intro email to {n} admin user(s) for customer {cust_id}?")
    print("            Press Enter to SEND, or type s + Enter to SKIP (e.g. user already got it).")
    try:
        resp = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  [SKIP] No intro email sent.")
        return
    if resp in ("s", "skip", "n", "no"):
        print("  [SKIP] No intro email sent (chose skip) -- continuing the run.")
        return
    sent = 0
    for i in range(n):
        try:
            env.nth(i).click()
            page.wait_for_timeout(800)
            # Clicking the envelope opens a confirm dialog ("Send Intro Email ... OK / Cancel"
            # -- it also resets the user's password). Clicking the envelope ALONE leaves Email
            # Status = "Not sent"; we must click OK to actually send.
            ok = page.locator(
                'div[id^="headlessui-dialog-panel"] button:has-text("OK"), '
                'button.btn-success:has-text("OK")').first
            try:
                ok.wait_for(state="visible", timeout=5000)
                ok.click()
                page.wait_for_timeout(1500)
                sent += 1
                print(f"  Confirmed send (clicked OK) for user {i + 1}/{n}.")
            except Exception as e:
                print(f"  [WARN] Confirm dialog 'OK' not clicked for user {i + 1} ({e}) -- the "
                      "email is NOT sent; click OK manually.")
        except Exception as e:
            print(f"  [skip] envelope {i}: {e}")
    print(f"  Sent intro email for {sent}/{n} user(s). Verify 'Email Status' shows sent.")


def check_customer_setup(page, cust_id):
    """Existing-customer CHECK (read-only) of the Admin customer page: is there an API
    user with POS access, and is 'Payment Processing Reports Stripe' enabled?

    Returns {"pos": bool, "stripe": bool, "contact_name/email/phone": str} so the chain can
    decide whether to fill the gap AND thread the contact to the SaaS handoff (read-once),
    or None if the page couldn't be read (caller should not assume anything)."""
    url = f"{ADMIN_BASE}/customers/{cust_id}"
    print(f"[NAV] Customer: {url}")
    # Read-only check -- a slow/failed load must NOT kill the whole chain. Retry, then skip.
    loaded = False
    for attempt in range(2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            loaded = True
            break
        except Exception as e:
            print(f"[WARN] Customer page slow (attempt {attempt + 1}/2): {e}")
    if not loaded:
        print(f"[CHECK] Couldn't load customer {cust_id} -- skipping the check (verify by hand).")
        return None
    page.wait_for_timeout(1500)
    info = page.evaluate(
        """() => {
            let pos = false;
            document.querySelectorAll('select[name^="api_user_access_"]').forEach(s => {
                const o = s.options[s.selectedIndex]; if (o && /pos/i.test(o.text)) pos = true; });
            let stripe = false;
            for (const l of document.querySelectorAll('label')) {
                if (/payment processing reports stripe/i.test(l.innerText || '')) {
                    const cb = l.querySelector('input[type=checkbox]'); if (cb) stripe = cb.checked; } }
            // Read-once: grab the primary contact while we're on this page so the SaaS handoff
            // (task 6) never has to navigate back here. Same label logic as read_admin_contact.
            const read = (labelText) => {
                for (const l of document.querySelectorAll('label')) {
                    if ((l.textContent || '').trim() === labelText) {
                        const group = l.closest('.form-group, div') || l.parentElement;
                        const inp = group && group.querySelector('input, textarea, select');
                        return inp ? (inp.value || inp.textContent || '').trim() : '';
                    }
                }
                return '';
            };
            return {pos, stripe,
                contact_name: read('Primary Contact Name'),
                contact_email: read('Primary Contact Email'),
                contact_phone: read('Primary Contact Phone Number')};
        }"""
    )
    print(f"[CHECK] customer {cust_id}: API user POS = {'yes' if info.get('pos') else 'NO'}; "
          f"Stripe reporting = {'on' if info.get('stripe') else 'OFF'}")
    if not info.get("pos") or not info.get("stripe"):
        print(f"        Gap(s) found -- the chain will fill API user + Stripe feature (or run "
              f"`apiuser {cust_id}` standalone).")
    else:
        print("        Looks set up -- nothing to do on the cust page.")
    return info


def next_location_id(page, cust_id, shared=True, from_current_page=False):
    """Next Location ID under an EXISTING customer. Access-sharing ON -> 01 series
    (max existing 01 + 1; cards shared/grouped); OFF -> 02 series (separate). VERIFY -- the
    01-vs-02 choice depends on the SOR's Access Sharing field, which the human confirms.

    from_current_page=True reads the existing ids off the CURRENT LaundroPortal location index
    (we're already there) instead of navigating to the Admin/cust-id window -- keeps all the
    location work in one pass (cust-id and location are different windows; don't bounce)."""
    if not from_current_page:
        page.goto(f"{ADMIN_BASE}/customers/{cust_id}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)
    # Match the 7-digit id ANYWHERE in the text, not just when it's the whole cell:
    # In-Development rows render the id with a tag (e.g. '0100003 [In Development]'), so an
    # anchored ^...$ match misses them and only the clean active row is counted. (Matt: those
    # still need to count as locations.)
    ids = page.evaluate(
        r"""() => { const s = new Set();
            document.querySelectorAll('option, td, th, span').forEach(e => {
                const ms = (e.textContent || '').match(/\b0[12]\d{5}\b/g);
                if (ms) ms.forEach(m => s.add(m)); });
            return [...s]; }"""
    )
    series = "01" if shared else "02"
    nums = [int(x[2:]) for x in ids if x.startswith(series)]
    nxt = (max(nums) + 1) if nums else 1
    loc = f"{series}{nxt:05d}"
    print(f"[LOC] existing location ids {sorted(ids) or '(none)'} -> next ({series} series) = {loc}")
    return loc
