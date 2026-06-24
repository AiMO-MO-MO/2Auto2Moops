"""
Browser session management for 2AUTO2MOOPS.

Launches a persistent Chromium context via Playwright.
User logs in manually once; Playwright reuses the authenticated session
via a local Chrome profile directory.
"""

import os
from playwright.sync_api import sync_playwright, Page

PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chrome_profile")
MOOPS_BASE = "https://moops.mitechisys.com"

# Lightweight run instrumentation -- count SO navigations so the optimization pass can compare
# round-trips before/after a change. Reset at the start of a run, read in the end-of-run summary.
_NAV_COUNT = 0


def reset_nav_count() -> None:
    global _NAV_COUNT
    _NAV_COUNT = 0


def get_nav_count() -> int:
    return _NAV_COUNT


def launch_browser() -> tuple:
    """
    Launch persistent Chromium browser.
    Returns (playwright_instance, context, page) tuple.
    Caller is responsible for context.close() and pw.stop().
    """
    # Clean stale Chromium lock files (leftover from crashes)
    for fname in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(PROFILE_DIR, fname))
        except FileNotFoundError:
            pass

    print(f"Profile: {PROFILE_DIR}")
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=False,
        args=["--start-maximized"],
    )
    page = context.pages[0] if context.pages else context.new_page()
    return pw, context, page


def navigate_to_so(page: Page, so_id: int) -> None:
    """Navigate directly to an SO page by ID.

    Retries on transient MOOPS hiccups (timeouts, ERR_HTTP_RESPONSE_CODE_FAILURE, etc.) --
    the server can be flaky and a single blip shouldn't kill a whole run mid-chain.
    """
    global _NAV_COUNT
    url = f"{MOOPS_BASE}/order?order_id={so_id}"
    print(f"\nNavigating to SO-{so_id}...")
    last_err = None
    for attempt in range(3):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector('tr[id^="existing_part_order_"]', timeout=20000)
            _NAV_COUNT += 1
            return
        except Exception as e:
            last_err = e
            print(f"[WARN] SO-{so_id} load failed (attempt {attempt + 1}/3): "
                  f"{str(e).splitlines()[0]} -- retrying in 3s...")
            page.wait_for_timeout(3000)
    print(f"[ERROR] Couldn't load SO-{so_id} after 3 tries -- MOOPS may be down. Re-run when it's back.")
    raise last_err


def ensure_on_so(page: Page, so_id: int) -> None:
    """Navigate to the SO ONLY if we're not already on it with its content loaded.

    Use in place of navigate_to_so where the previous step already left us on a FRESH SO page
    (e.g. right after a save reloads it, with nothing navigating away in between) -- this skips a
    redundant ~3-9s reload. If the URL doesn't match or the part rows aren't present, it falls back
    to a full navigate_to_so (which does the retry + nav-count increment). A skip does NOT bump the
    nav counter, so the end-of-run summary reflects the reduction."""
    try:
        on_so = (f"order_id={so_id}" in (page.url or "")
                 and page.locator('tr[id^="existing_part_order_"]').count() > 0)
    except Exception:
        on_so = False
    if on_so:
        print(f"[NAV] Already on SO-{so_id} (loaded) -- skip reload.")
        return
    navigate_to_so(page, so_id)


def navigate_to_cards(page: Page) -> None:
    """Navigate to the Cards index page."""
    url = f"{MOOPS_BASE}/cards"
    print(f"[NAV] Going to Cards page")
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_selector('text=Existing Cards', timeout=10000)
