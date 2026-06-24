# Chain Optimization Plan (`chain-optimization` branch)

> **Session pickup (2026-06-22) — read first.** All work is uncommitted on `chain-optimization`
> (up to date with origin). The working tree mixes two efforts — commit them SEPARATELY:
> chain-opt (`core/browser.py`, `core/moops.py`, `core/portal.py`, `core/provisioning.py`, `run.py`,
> + this doc) vs. the dedupe skill rewrite (`skills/moops-dedupe/SKILL.md` + `references/*.js`, a
> different concern). Untracked stragglers `reader-kit-lookup_SKILL_v2.md` and
> `reader_kit_lookup_fixes_2026-06-17.md` look stale — remove or ignore, don't sweep into a commit.
>
> **Status by item:**
> - **Item 1 (read-once / cut navs):** ~built, NOT yet validated or committed. Done: `ensure_on_so`
>   guard (browser.py), nav counter + end-of-run `[SUMMARY]` line, `next_customer_id` reuses the
>   cached Admin scrape, `ensure_on_so` wired into config + task-checklist steps. Pending: confirm
>   nothing downstream re-reads the SOR; intro step still hops to the Admin page (fold the pass-through).
>   **NEXT ACTION: run a real `system <id>`, compare `[SUMMARY]` NAV count + total time vs an old-branch
>   run, confirm identical task/field outcomes, then commit Item 1.**
> - **Item 2 (one validity-field helper):** partial. `_commit_ownership_location` is shared by
>   End-Customer + card-ownership; product "Add To Order" not yet folded onto it.
> - **Item 3 (fail-fast guards):** partial. `read_so_end_customer` count-guard + `navigate_to_so` 3×
>   retry in; `action_add_part` bounded-click + the unbounded-`page.*`-wait audit not done.
>
> Restart the `2auto>` console before any validation run (no hot-reload). Don't run Playwright from
> here — Matt runs it locally and pastes `run.log`.

Goal: the `system <id>` chain works end-to-end but is **brittle and slow**. This pass makes it
**faster and fail-soft** without changing what it does. Ground rules (from CLAUDE.md): incremental,
one change at a time, reuse one shared path, **read the real code + live DOM before changing**, never
ship a behavior change unverified, keep business logic separable from page interaction.

Each item below lists: the evidence (run-log/DOM), the approach, and how we verify. Do them top-down;
each is independently shippable so we can commit + you can run a real order after each.

## 0. Guiding principle: get each piece of information ONCE, the cheap way
The chain's slowness is mostly **re-fetching information it already has** (or fetching it the
expensive way). Optimize the data flow first; everything else follows. Rules:
- **Read once, thread everywhere.** The snapshot is the single read pass (SO + SOR + tasks + end
  customer + config + schedule). Setup and the provisioning chain should **consume the snapshot**,
  never re-navigate to re-read the same thing. The `_snapshot` dict already carries `so_data`/`sor`/
  `tasks` — make every downstream step take it instead of re-reading.
- **Cheapest source.** The SOR is an Angular page (~6s nav away-and-back) — read it exactly once.
  The schedule read navigates to /orders and back — only when the assembly week is actually missing
  (already gated; keep it that way).
- **No redundant scrapes.** Fetch a list once and reuse it for every consumer.
- **Don't re-read to verify what we just wrote** unless MOOPS actually requires a reload (e.g. the
  config File Resources repaint). Prefer the value we set over a fresh read.

### Redundant / expensive fetches observed (holistic, from the run logs)
- **Admin `/customers` scraped TWICE per new-customer run:** once for dedup in the snapshot
  (`[READ] Admin /customers: 2030 rows`, SO-20081 L93) and again for the next Cust ID
  (`[NAV] Reading customer IDs …` L180). It's ~2000 rows each time. Scrape once (already cached per
  session via `portal.scrape_admin_customers`) and compute `next_customer_id` from that same list.
- **SO re-navigated between nearly every step** even when already on it (see item 1).
- **`read_so_end_customer` called repeatedly** across snapshot + chain; cheap now (count-guarded) but
  still a nav each time — fold into the threaded snapshot where the value hasn't changed.
- **SOR fields** are read once in the snapshot (good) — confirm nothing downstream re-reads the SOR.

## 1. Read-once data flow + cut navigation round-trips  (biggest win, lowest risk)
**Evidence:** every chain step re-runs `navigate_to_so` even when already on the SO; the intro step
hops to the Admin customer page right after the user step ("hesitation" Matt saw, SO-20081 line 278);
Admin `/customers` is scraped twice (dedup + next-id); many 3-6s navs/scrapes stack up.
**Approach:**
- Add a cheap `ensure_on_so(page, so_id)` guard: no-op if `page.url` already matches the SO; only
  `navigate_to_so` when the URL actually changed. Replace the blind `navigate_to_so` calls in the
  chain/cards/config steps with it.
- Thread the already-read snapshot (`so_data`/`sor`/`tasks`) through so steps stop re-reading the
  SO/SOR (per principle 0).
- Scrape Admin `/customers` once; reuse the cached list for both dedup and `next_customer_id`.
- Intro step: it already lands on the Admin customer page during the API-user/check step — pass that
  through instead of re-navigating.
**Verify:** run a real `system <id>`; compare the `[NAV]`/scrape count and total time vs a
current-branch run; confirm identical task/field outcomes.

## 2. One hardened validity-field commit helper  (kills a recurring class of bugs)
**Evidence:** End-Customer, Card-Ownership Location, and product "Add To Order" are all the same
search→pick→(Add)→verify pattern, and each has bitten us separately (location pick-only didn't persist;
"Add Location" button matched wrong; product add hung on a disabled button). DOM facts already captured
in memory `card-ownership-location`.
**Approach:** one helper that takes the field selector + whether it has an Add button + how to verify
(row in table vs single value), with **bounded** waits and retries. Refactor `set_so_end_customer`,
`_commit_ownership_location`, and `clone_temp_card`'s ownership block onto it. No behavior change —
just consolidation + bounded waits.
**Verify:** run a new-design card order (exercises End-Customer + location + product add) and confirm
the card part shows the location committed and the SO links correctly, same as today.

## 3. Fail-fast guards — degrade, don't stall  (robustness)
**Evidence:** the wire-splicer dup made `action_add_part` spin 30s on a disabled "Add To Order"
(SO-20080); `read_so_end_customer` hung 60s before its count-guard fix; MOOPS is generally flaky.
**Approach:**
- `action_add_part`: pre-check the part isn't already a row; bound the "Add To Order" click (short
  timeout) and surface a clear `[FLAG]` instead of a 30s spin.
- Audit the remaining unbounded `page.*` waits in the chain for missing/short timeouts (the pattern
  behind the read_so_end_customer freeze).
- Keep the `navigate_to_so` 3× retry but make a sustained failure exit with a clear message + the
  resume command, not a hang.
**Verify:** can't easily force MOOPS failures, so unit-test the pure decision bits where possible and
do a careful read-through; confirm a normal order still runs unchanged.

## 4. Smaller follow-ups (fold in opportunistically)
- The card clone step is ~30-48s (mostly the human review pause) — fine, but confirm no extra waits.
- `download_location_vac_configs` (laundrylux) location-switch mechanic still needs a live shakedown
  (memory `laundrylux-stock-vac-run`) — separate from this pass.
- Consider a single end-of-run summary of `[NAV]`/timing to track regressions.

## Out of scope (do NOT touch here)
- The dedupe skill, intake, or any reader-kit work.
- Anything that changes WHAT the chain does (task logic, provisioning order, card routing) — those were
  just settled and are working.

## Sequencing
1 → 2 → 3, committing after each, with a real `system <id>` run between. Stop and reassess if any run
diverges from current behavior.
