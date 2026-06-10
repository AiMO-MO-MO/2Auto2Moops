# Code Reference — Structure, Selectors, Playwright Lessons

> Loaded on demand (see CLAUDE.md pointer map). READ THIS BEFORE EDITING ANY CODE in this repo.

## Code Structure

```
run.py                    ← CLI dispatcher + verb shorthand + chain orchestrators
                            (_do_create_customer/_do_addloc/_do_adduser/_do_cards/
                             _do_provision_chain/_do_custid/_do_dedup_test)
core/browser.py           ← Playwright launch + navigation
core/moops.py             ← All MOOPS page actions (~2,300 lines)
core/schedule.py          ← Capacity, FIFO picking, date parsing
core/efs.py               ← EFS catalog, JS snippet builder, kit expansion
core/portal.py            ← Admin/LaundroPortal verification (tasks 7,8,10), scrape_admin_customers
                            (cached), current_portal_customer (LP write guard)
core/dedup.py             ← Pure customer matcher (email>phone>lastname>name); source-agnostic
core/provisioning.py      ← Eliminate-ITF fills: create-customer, fill_api_user (finalize),
                            fill_location, fill_user, open_stripe, send_intro_email, inspect_form
playbooks/first_touch.py  ← System/Route playbook (10 steps; no_itf + dedup_test flags; returns cid)
playbooks/parts_order.py  ← Parts order playbook (3 fulfillment paths + kit expansion)
playbooks/cards_order.py  ← Cards-only order playbook
playbooks/final_touch.py  ← Pre-ship audit: drive all tasks to 100% (RETIRED)
playbooks/intake.py       ← Read-only queue scan → board + plan (classify, schedule, dedup)
```

## MOOPS Selectors (validated)

| Element | Selector |
|---------|----------|
| Tag | `input[name="description"]` |
| Product search | `#validity_product-search` |
| Add To Order | `text=Add To Order` |
| Existing parts | `tr[id^="existing_part_order_"]` |
| New parts | `tr[id^="new_part_order_"]` |
| Part number | `th[scope="row"] a` |
| Qty input | `input[type="number"]` (first in row) |
| Shipment Method | `select[name="delivery_method_id"]` |
| Shipment By | `select[name="part_inventory_location_address_id"]` |
| Work State | `select[name="work_state_id"]` |
| Order Type | `select[name="sales_type_id"]` |
| Task selects | `select[name="task_state"]` (10 total) |
| Internal Notes | `textarea[name="notes_to_admin"]` |
| SOR link | `a[href*="/order-requests/"]` |
| Save | `text=Save` |
| Missing parts | `text=Missing part associations detected` |

**SOR-only selectors:** EBT/Processor label → adjacent span. Required Delivery Date → `span.col-9` with "(Month DD, YYYY)". EXPEDITED → `span.bs-red`.

## Config file (.cfg) notes

- Downloaded via Playwright `expect_download()` to `vac_configs/SO{id}/` — Chrome security prompt is
  bypassed at protocol level. Files are NOT in the user's Downloads folder; they're in the project folder.
- Filename matches MOOPS format: `SO{id}_{name}_{loc}_{VACnn}_{part}.cfg`. For qty>1 rows, `_VAC01_`
  increments to `_VAC02_`, etc. KioskName patched to match the sequential number.
- Upload via `set_input_files` on `#uploadFiles` + form-submit click. Verified by checking filename
  appears in page content after upload.
- **MUST have End Customer set first** — config is skipped with [FLAG] if End Customer not linked.

## Stripe notes

- `open_stripe`: 20s timeout on Payment Processing link + reload+retry if LP sidebar slow after location save.
- After "Add New Merchant": detects page drift post-reload, navigates back to PaymentProcessing.php if needed.
- Bank access assignment verified: re-reads grant dropdown after Assign click; prints [OK] if confirmed.

## Dev-env note

The OneDrive mount serves the sandbox **stale/truncated** copies of just-edited files,
so `py_compile` in the workspace often fails on unchanged lines — the file tools see the true file. Rely
on the file tools + Matt's local run, not the sandbox compile.

## Playwright Critical Lessons (do NOT relearn these)

1. **Save click**: Must use `page.evaluate()` JS click, NOT Playwright `.click()`. Playwright blocks 30s+ waiting for post-navigation load event.
2. **Post-save wait**: Must use `time.sleep()`, NOT `page.wait_for_timeout()`. MOOPS save destroys the execution context.
3. **Bytecode cache**: OneDrive sync causes stale `.pyc`. `sys.dont_write_bytecode = True` in run.py handles this.
4. **Product table selector**: `tr[id^="existing_part_order_"]` is always in DOM — don't use to detect save completion.
5. **Customer ID blocker**: Customer ID populated + Location empty → MOOPS blocks save. Auto-cleared before every save.
6. **Card shortname**: Target 6 chars. Strip vowels → 2-char-per-word → acronym → truncate.
7. **Card design type matching**: Use `_card_type(design)` in `run.py` — direct startswith match. The SOR gives you the value directly ("New design", "Reprint", "Existing"). No substring guessing.
8. **Customer name fallback**: Cards orders may have empty name. Fall back to card description first line.
9. **run.py keep-alive**: Use `time.sleep(1)` not `page.wait_for_timeout(1000)`.
10. **Product search**: Use `fill()` then click "Add To Order" immediately. Do NOT use `type(delay=)` or wait for autocomplete dropdown. ~0.5s per part.
11. **Lightweight navigate**: When you just need to be on the SO page (e.g. to add a card or set tasks), use bare `page.goto()` + `wait_for_selector` instead of full `read_so()`. Saves ~3s per navigation.
12. **Card part re-read after clone**: `input_value(timeout=3000)` — page may have navigated after human save, so short timeout with fallback to generated name.
13. **EFS JS escaping**: All string values must escape single quotes (`\'`) before insertion into JS template. Customer names like "Larry's" break otherwise.
14. **action_add_card_to_so**: Finds source card by scanning for any `CARD-` prefix (excluding CARD-03-01 and the new card). Works for both CARD-01-02 (new design) and existing CARD-MD-* (modify).
15. **delete_card_placeholder**: Accepts `part_to_delete` param — not hardcoded to CARD-01-02 anymore.
16. **EFS JS snippet in chat**: When Matt says he already updated the SO and can't re-run the playbook, generate the JS snippet DIRECTLY IN CHAT for him to paste into the EFS browser console. Do NOT tell him to re-run the playbook. Read the Shipping To field from the SO (or from earlier output) to get address/phone/name. Expand kits using KIT_EFS_COMPONENTS. Output the full `(function(){...})();` block ready to paste.
17. **"Continue from where you left off"**: If Matt says this and there's nothing pending, say so briefly. Do NOT say "No response requested" — that's useless. If there IS pending work, do it.
18. **SOR pages are Angular client-rendered**: `fetch()` / XHR returns template tags (`{{orderRequest...}}`), not rendered values. Must use full Playwright navigation to read SOR fields. Use `wait_for_function("() => !document.body.textContent.includes('{{')")` instead of hard `wait_for_timeout(2000)` to detect when Angular has rendered.
19. **$env:PYTHONDONTWRITEBYTECODE is redundant**: `run.py` already sets `sys.dont_write_bytecode = True`. Don't add the env var to commands.
20. **LaundroPortal is per-customer scoped**: every LP write hits whichever customer is logged in. ALWAYS guard with `current_portal_customer(page)` and abort on mismatch — writing to the wrong account is a serious error.
21. **PaymentProcessing.php direct-load bounces** to LocationPanel.php (no "current location" context). Reach it the human way: load `LocationPanel.php?Location_Key=<k>`, then CLICK the Payment Processing link (`inspect_payment`/`open_stripe` do this).
22. **Location_Key** only exists AFTER the location is saved — read it from the page URL (`Location_Key=NNNN`). The chain prompts for it if the URL parse fails.
23. **Persistent console doesn't hot-reload**: code edits need `quit` + relaunch of `python run.py` before new verbs/fixes take effect.
24. **inspect-form now also dumps buttons/links** (`--- buttons / links ---`). Clickable controls that aren't `<button>`/`a.btn` (e.g. the intro-email envelope `span.cursor-pointer > i.fa-envelope`) won't appear — grab those with a DevTools snippet.
25. **`navigate_to_so` retries 3×** on transient MOOPS failures (timeouts, ERR_HTTP_RESPONSE_CODE_FAILURE). MOOPS can be flaky / very slow; a single nav blip shouldn't kill a run mid-chain. Only a sustained outage raises.
26. **Idempotent run is task-driven**: the chain reads the task checklist and runs ONLY To Do steps (7=Stripe, 8=location, 10=user+intro, 3/4/5=card). Never blanket-reset the checklist on a re-run — it would un-complete provisioning tasks. Tag/schedule/parts are check-or-skip (set only if missing).
27. **End Customer is read from the SO field, not just notes**: `read_so_end_customer` reads the live `#validity_customer-search` widget (`02166 - Name` + location). A real cust id there = existing customer → reuse, never create. All-zeros (`00000`) IS a real (test) customer, not a placeholder — don't special-case it.
28. **Config requires End Customer first**: MOOPS uses the linked customer+location to populate CustomerKey and LocationID in the .cfg. Always set End Customer BEFORE downloading config. Chain order: End Customer → config. If End Customer fails (dealer link pending), task 9 stays To Do.
29. **SOR Angular wait**: Use `wait_for_function("() => !document.body.textContent.includes('{{')")` not `wait_for_timeout(2000)`. The fixed sleep is what made SOR reads slow (~5s vs <1s).
30. **User step is new-customer only**: `not existing` guard on task 10 — existing customers already have LP users. For a never-provisioned existing Admin customer, run `adduser <so> <cust>` standalone. Existing cust + task 10 To Do → counted as done (the user/intro happened at first provisioning).
31. **MOOPS save rejection is detectable**: a rejected save shows 'The submission had errors' — `save_so` checks for it and returns True/False; the setup pass ABORTS on False (continuing would lose fills and lie on task states). Don't scrape for 'required'/'error' generally — field labels ('Required Date') and static hints ('...required to create a PO') are false positives.
32. **End Customer pre-filled from SOR = save blocker (MOOPS bug)**: existing-customer SOs arrive with End Customer pulled from the SOR and NO location → every save is rejected. The widgets are `#validity_customer-search` + `[id^="validity_$location_filter"]` (NO name, NO label — label/name hunts find nothing). `_clear_customer_id_if_blocking` clears the cust widget when loc is empty; the chain re-links cust+location later. Never pass `clear_customer_location_blocker=False` on a path that can carry a SOR-pulled End Customer.
33. **Navigating reads BEFORE fills**: any unsaved fill dies on navigation. The schedule-capacity read navigates to /orders — it wiped a just-filled tag. Step order in a same-page pass: navigating reads first, then fill tag/week/parts, then save.
34. **`login_to_portal` confirms the SIDEBAR, not the page**: the scope check passes on every LP page, so an index read can fire before the Locations table renders → silent '0 rows' for a customer WITH locations (almost caused a duplicate create). `read_portal_location_index` polls for rows, only trusts empty when 'Add New Location' is visible with no 7-digit ids, clicks the sidebar Locations link as a retry, and RAISES rather than reporting an unconfirmed 0.
35. **Humans edit at save pauses — re-read after EVERY pause** (generalize lesson 12): location ID renamed at the location-save pause must be re-read (`read_portal_location(loc_key)`) just like renamed card parts. Anything the run generated and a human can edit before saving needs a post-save re-read.
36. **No prompts inside the chain**: fill-only IS the human gate. Prompts get Ctrl-C'd or answered wrong (a 'Create' answer became a literal location id). No match → fill the new location and stop for the save. The ONLY surviving prompt is multi-candidate location ambiguity.
37. **navigate_to_so skips when already on the SO** (URL has order_id + product table rendered). Safe post-save because MOOPS save reloads in place. Chain steps no longer re-navigate back-to-back.
38. **CC rules differ by email**: card DESIGN email keeps MOOPS's CC (Matt needs it); PO email clears CC. Don't share the clearing code path.
39. **EditLocation.php direct-load struggles like PaymentProcessing.php** (lesson 21): reach the Add Location form by loading `index.php` and CLICKING the "Add New Location" button (`a.btn-primary[href="EditLocation.php"]` in the Locations panel heading). `fill_location._open_and_check` does this now.
40. **SOR 'Required Delivery Date' line doubles as shipping method**: when the Shipping Method label parse finds nothing, the Required-Date raw text often carries 'Next Day'/'Ground'/'Freight' — `read_sor_data` falls back to it (urgent orders were silently shipping Ground).
