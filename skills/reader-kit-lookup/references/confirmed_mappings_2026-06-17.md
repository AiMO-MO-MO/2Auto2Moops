# Reader kit determinations + regex to add (2026-06-17)

For each model: the machine, the kit to assign (per that SOR's install type), and the
`READER_LOOKUP` regex + `READER_LOOKUP_PARTS` mapping to insert so it resolves going forward.
Source: live `GET /reader_lookup/index`.

| Model (SOR) | Machine | Install | Kit to assign | Regex + part mapping to add |
|---|---|---|---|---|
| SCT040NY0FXU800000 (27944) | SpeedQueen 40 lb ACA hardmount, card-ready/coin | HYBRID | **KIT-ALLIANCE-SERIAL-HYBRID-WASHER-ACA** (part 2245) | regex `[SHI]C[NT][0-1][0-9]0[QNW]Y.{10}$` already matches (row 4); add `part_hybrid_id = 2245` to its kit (part_id 1246) |
| SWNNY2SP116TW01 (27944) | SpeedQueen ACA small-chassis washer | HYBRID | card-only KIT-ALLIANCE-SERIAL07; **hybrid kit must be created** (CR-10-136-00 2-stud + hybrid harness 30-01341) | regex `([SHBK][FWD].[KNB]Y.....[3-9]....$)` matches (row 19); needs a new small-chassis ACA hybrid kit, then set it as `part_hybrid_id` |
| C50MD2 / C50MD2AU2 (27888) | 1996 Raytheon/Alliance 50 lb 3-phase **mechanical** washer-extractor, coin board | CARD-ONLY | **KIT-ALLIANCE-MECHANICAL-02** (part 1269, CR-08-117-06 + HV sensor) | change row 13 regex `[USH]C[0-9][0-9]MD2` → **`[USH]?C[0-9][0-9]MD2`** so bare `C50MD2…` resolves |
| WC0600XA (27302) | Dexter **C-Series** washer (full: WC0600XA-12EC2X-SSBCS-US) | CARD-ONLY | **KIT-DEXTER-CSERIES-WASHER-READER-PRICING-WITHBLOCKOUTS** (part 1838; no factory blockouts) | append to row 32 regex: **`|(WC[0O][0-9][0-9][0-9]XA)`** to catch the short form (won't hit WX X-Series) |
| REM025X (27755) | Continental REM small coin-box washer, post-2015 COM/serial | HYBRID | **KIT-CONTINENTAL-SERIAL-CARDREADY** (part 1360) | regex `REM025` matches (row 107); add `part_hybrid_id = 1360` to its KIT-CONTINENTAL-SERIAL-SMALL kit (part 1343). Broaden regex to `REM0[0-9][0-9]` to cover REM020/030 |
| HT075NVP0RXS6NC000 (27723) | SpeedQueen "P" App-ready Touch **single dryer** | CARD-ONLY | **KIT-ALLIANCE-SERIAL17** (part 1296) | assign part 1296 to the stale TBD row 162 (`[SHK]T[0O][1-9][0-9][NE]VP`) and dedupe vs row 205 so the "multiple match" clears |

Same hybrid order 27755 also has **EH040X / EH060X** (Continental EH large, COM/serial, hybrid) →
**KIT-CONTINENTAL-SERIAL-CARDREADY**: add `part_hybrid_id = 1360` to row 106's KIT-CONTINENTAL-SERIAL-LARGE
kit (part 1344). The pulse kit on that row carries 1360 by mistake — move it.

### New READER_LOOKUP rows you could add for the bare nameplate forms

```
-- Alliance mechanical washer-extractor (catch C50MD2 with or without brand letter)
reg_ex: [USH]?C[0-9][0-9]MD2   mfr: Alliance   desc: Washer extractor, mechanical, coin board
  parts: KIT-ALLIANCE-MECHANICAL-02 (1269)   hybrid: 3320

-- Dexter C-Series washer short form
reg_ex: WC[0O][0-9][0-9][0-9]XA   mfr: Dexter   desc: C-series washer (short form)
  parts: KIT-DEXTER-CSERIES-WASHER-READER-PRICING (1838)   hybrid: 2264   question_id: 17

-- Continental REM (broadened)
reg_ex: REM0[0-9][0-9]   mfr: Continental-Girbau   desc: REM washer, small coin-box   question_id: 5
  parts: SERIAL-SMALL(1343, hybrid 1360) | PULSE-SMALL(1346) | SERIAL-CARDREADY(1360)
```

No UI for these — Oleg applies them to `READER_LOOKUP` / `READER_LOOKUP_PARTS` in Snowflake.
