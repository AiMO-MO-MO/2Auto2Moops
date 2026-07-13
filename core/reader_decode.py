"""
Reader-kit DECODER -- the knowledge layer.

When the live reader_lookup regex table can't assign a kit (and MOOPS therefore
shows "No reader kit assigned"), this module applies the trained decode knowledge
(Reader Kit Cheat Sheet Steps 1-3, the reader-kit-lookup skill, and Oleg's
confirmed equivalencies) to make a *judgement*: decode the model number ->
manufacturer, machine type, control era, comm (serial/pulse), board type, mount ->
and propose the most likely KIT-* with a confidence and a written rationale.

This is deliberately deterministic (rules, not ML). It does NOT "learn" on its own;
it improves when a human/Claude reviews the assessment log and edits the rules here
(see KNOWLEDGE PROVENANCE at bottom). Every proposal it makes is logged so those
review passes have real cases to sharpen against.

Honesty about limits:
  - Dexter (C/X/A/N), LG, ADC, Compass Pro, Continental, Wascomat-mechanical, Fagor
    decode to a specific kit with good confidence.
  - Alliance decodes reliably to family + board + mount, but the exact SERIALxx
    variant depends on chassis/plate nuances -> proposed as best-guess, flagged
    "confirm variant".
  - Some calls truly need a nameplate photo (slot width, ADC button count) -> the
    decoder says so rather than guessing.

Public API:
  decode(model) -> dict of decoded attributes
  assess(model, install_type="") -> {model, brand, machine_type, comm, board,
      proposed_kit, proposed_kit_hybrid, confidence, reasoning, escalate, attrs}
"""

import re

CARD_ONLY = "card"
HYBRID = "hybrid"


def _norm(model):
    return re.sub(r"\s+", "", (model or "")).upper()


def _is_hybrid(install_type):
    return "HYBRID" in (install_type or "").upper() or "COIN+CARD" in (install_type or "").upper()


# ---------------------------------------------------------------------------
# Brand detection
# ---------------------------------------------------------------------------

def _brand(m):
    # Alliance mechanical bare form (C50MD2 / UC50MD2 -- no brand letter). Check
    # before Dexter so it isn't swallowed, and before the generic Alliance rule.
    if re.match(r"^[USH]?C[0-9][0-9]M[DXYC]", m):
        return "Alliance"
    # Pellerin Milnor washer-extractor (WCR.. etc) -- NOT in our kit library. Recognize
    # it BEFORE Dexter so a "WC" prefix isn't mis-read as a Dexter C-Series washer.
    if re.match(r"^(WCR|MWR|MWF|WSN|MDR)[0-9]", m):
        return "Milnor"
    # Dexter -- NOTE: WC/DC/SC require a digit next so Milnor WCR / Alliance SCT/SCN/SDG don't collide.
    if re.match(r"^(WC[0-9O]|WCAD|WSAD|WCVD|WCN|WCK|WCH|WX|DC[0-9O]|DCWD|DDAD|DSTD|DDTD|DDBD|DCBD|DDH|DLC|DL2|DLH|DRC|SC[0-9O]|SCO|WCO)", m):
        return "Dexter"
    # Electrolux / Wascomat / Laundrylux
    if m.startswith("COMPASSPRO") or re.match(r"^(ELD|EED|EUD|ESD|DE6|DE-?6|W\d{3}CC|W\d{2}\d?CC)", m):
        return "Electrolux"
    if m.startswith("COMPASS"):
        return "Electrolux"
    # Continental Girbau
    if re.match(r"^(EH|EM|REM|RMG|GS\d|L10)", m):
        return "Continental"
    # LG
    if re.match(r"^(TCW|CTD|CWG|CWD|GCWM|GDL|GS0)", m):
        return "LG"
    # ADC
    if re.match(r"^(AD|ADG|ADC)", m):
        return "ADC"
    # Maytag / Whirlpool
    if re.match(r"^(MDG|MLG|MLE|MAT|MHN|MYR|MFR|MDE|MXR|MDC7|WED|WQD|WUD|WHLFP|WLD|GDL|CAE)", m):
        return "Maytag/Whirlpool"
    # Fagor / Domus
    if re.match(r"^(FWS|FD|LR|LN|DTCK)", m):
        return "Fagor"
    # Wascomat mechanical (bare W### / T#### / D### shorts)
    if re.match(r"^(W\d{2,4}|T\d{3,4}|D\d{3}|WSTR|DSTR|TD\d)", m):
        return "Wascomat"
    # Alliance family (SpeedQueen S / Huebsch H / Primus P / IPSO B or I / K)
    if re.match(r"^[SHPBIK][CTFWDSG]", m):
        return "Alliance"
    return "Unknown"


# ---------------------------------------------------------------------------
# Alliance decode  (Cheat Sheet Step 1)
# ---------------------------------------------------------------------------

_ALLIANCE_BRAND = {"S": "SpeedQueen", "H": "Huebsch", "P": "Primus", "B": "IPSO", "I": "IPSO", "K": "IPSO"}

def _decode_alliance(m):
    d = {"brand": "Alliance", "sub_brand": _ALLIANCE_BRAND.get(m[:1], "Alliance")}
    two = m[1:3]
    # machine type by 2nd-3rd letters
    if two in ("CT",):
        d["machine_type"] = "washer"; d["chassis"] = "hardmount tumbler (large)"
    elif two in ("CN",):
        d["machine_type"] = "washer"; d["chassis"] = "coin hardmount"
    elif two == "TT":
        d["machine_type"] = "stack dryer"
    elif two == "WN":
        d["machine_type"] = "washer"; d["chassis"] = "small-chassis"
    elif two == "FN":
        d["machine_type"] = "washer"; d["chassis"] = "front-load"
    elif two in ("DG",):
        d["machine_type"] = "dryer"; d["chassis"] = "gas"
    elif two in ("DE", "SG", "GT"):
        d["machine_type"] = "dryer"
    elif two[0] == "T" or re.match(r"^[SHKIP]T[0O]", m):
        d["machine_type"] = "single dryer"
    else:
        d["machine_type"] = "washer"  # default for hardmount codes

    # control era (Cheat Sheet Step 1 table)
    era, comm = "", "serial"
    n = len(m)
    c11 = m[10] if n >= 18 else (m[10] if n >= 11 else "")
    if re.search(r"V[PL]", m) or "VP" in m:
        era = "Alliance 'P' App-ready Touch"; comm = "serial"
    elif re.match(r"^[SHKIP]C[0-9][0-9]MD2", m) or re.search(r"MD2", m) or re.match(r"^[USH]?C[0-9][0-9]M", m):
        era = "Mechanical (Raytheon-era coin board)"; comm = "pulse"
    elif re.match(r"^[SH]C[0-9][0-9]M[XYC]", m):
        era = "Mechanical"; comm = "pulse"
    elif n == 15:
        if c11 in ("1", "2"):
            era = "MDC / Centurion"; comm = "pulse" if "dryer" in d.get("machine_type", "") else "serial-w-jumper"
        else:
            era = "MDC/Centurion-era (15-digit)"; comm = "serial-w-jumper"
    elif n >= 18:
        if c11 in ("6", "8"):
            era = "Titanium (Platinum/Quantum Pro)"; comm = "serial"
        elif c11 in ("3", "5"):
            era = "ACA (Quantum Gold/MDC2/Galaxy 600)"; comm = "serial"
        else:
            era = "Midas/ACA-era (18-digit)"; comm = "serial"
    else:
        era = "unknown era"; comm = "serial"

    # NetMaster / EDC overrides (pulse)
    if re.match(r"^[SH]C[0-9][0-9]N[CX]", m) or re.search(r"^..(NC|NR|NX|ZC|ZR|ZX|ZY)", m):
        era = "NetMaster"; comm = "pulse"
    if re.search(r"E[CXY]\d", m) and d.get("machine_type", "").endswith("dryer"):
        era = "EDC"; comm = "pulse"
    # B-Micro
    if re.match(r"^[SH]C1?[0-9][0-9]B[CY]", m):
        d["machine_type"] = "washer"; d["chassis"] = "B-Micro"

    d["era"] = era
    d["comm"] = comm
    d["soft_mount"] = len(m) > 1 and m[1] == "Y"  # cheat sheet: Y in 2nd pos = soft mount
    return d


def _kit_alliance(d, hybrid):
    """Best-guess Alliance kit. Family/board are reliable; exact SERIALxx flagged."""
    mt = d.get("machine_type", "")
    era = d.get("era", "")
    chassis = d.get("chassis", "")
    conf = "medium"
    notes = ["confirm exact SERIAL variant / mount plate"]

    if "Mechanical" in era:
        return ("KIT-ALLIANCE-MECHANICAL-02" if "MD2" in era or "coin board" in era
                else "KIT-ALLIANCE-MECHANICAL-01"), None, "high", ["needs HV sensor (CR-08-117-06)"]
    if era == "NetMaster":
        kit = "KIT-ALLIANCE-NETMASTER-01" if "washer" in mt else "KIT-ALLIANCE-NETMASTER-DRYER-01"
        return kit, None, "medium", ["pulse/relay board"]
    if era == "EDC":
        kit = "KIT-ALLIANCE-EDC-WASHER" if "washer" in mt else "KIT-ALLIANCE-EDC-01"
        return kit, None, "medium", ["EDC pulse; hybrid EDC mapping is a known gap"]
    if chassis == "B-Micro":
        return "KIT-ALLIANCE-BMICRO", None, "medium", []
    if "P' App-ready Touch" in era or "'P'" in era:
        if "stack" in mt:
            return "KIT-ALLIANCE-SERIAL18", None, "medium", ["'P' touch stack dryer"]
        if "single dryer" in mt:
            return "KIT-ALLIANCE-SERIAL17", None, "high", ["'P' touch single dryer (part 1296)"]
        return "KIT-ALLIANCE-SERIAL21", None, "medium", ["'P' touch washer"]
    if "MDC" in era and "dryer" in mt:
        return "KIT-ALLIANCE-MDC-PULSE06", None, "medium", ["MDC/Centurion dryer = pulse"]

    # Serial-era ACA/Midas/Titanium
    if "stack" in mt:
        return "KIT-ALLIANCE-SERIAL05", None, "medium", ["stack dryer serial; may split w/ MDC-PULSE06"] + notes
    if "single dryer" in mt:
        return "KIT-ALLIANCE-SERIAL04", None, "medium", notes
    # washers
    if chassis == "small-chassis" or chassis == "front-load":
        return "KIT-ALLIANCE-SERIAL07", None, "medium", ["small-chassis/front-load ACA washer"] + notes
    # large hardmount washer-extractor
    return "KIT-ALLIANCE-SERIAL01", None, "medium", ["hardmount washer-extractor (card-ready)"] + notes


# ---------------------------------------------------------------------------
# Dexter decode  (Cheat Sheet Step 1)
# ---------------------------------------------------------------------------

def _decode_dexter(m):
    d = {"brand": "Dexter"}
    # Order matters: match the SPECIFIC multi-letter prefixes before the generic
    # WC / DC (else WCAD/WCVD/WCN get swallowed by "WC").
    if re.match(r"^(SC|SCO)\d", m) or m.startswith("SC0"):
        d.update(series="C-Series", machine_type="stacked washer/dryer", comm="serial", board="Generic Serial", stacked=True)
    elif m.startswith(("WCAD", "WSAD", "WCVD")):
        d.update(series="A/V-Series", machine_type="washer", comm="pulse", board="Generic Relay")
    elif m.startswith("WCN"):
        d.update(series="N-Series", machine_type="washer", comm="pulse", board="Generic Relay", note="9-pin adapter (30-01400)")
    elif m.startswith(("DDAD", "DSTD", "DDTD")):
        d.update(series="A-Series/Legacy", machine_type="stack dryer", comm="pulse", board="Generic Relay")
    elif m.startswith(("DL2", "DLH")):
        d.update(series="N-Series", machine_type="stack dryer", comm="pulse", board="Generic Relay", note="4-pin coin connector")
    elif m.startswith(("DLC", "DDH")):
        d.update(series="N/A-Series", machine_type="single dryer", comm="pulse", board="Generic Relay", note="11-pin adapter (30-01334)")
    elif m.startswith(("WX", "WXO")):
        d.update(series="X-Series", machine_type="washer", comm="serial", board="Generic Serial")
    elif m.startswith("DRC"):
        d.update(series="C-Series", machine_type="dryer", comm="serial", board="Generic Serial")
    elif m.startswith(("WC", "WCO")):
        d.update(series="C-Series", machine_type="washer", comm="serial", board="Generic Serial")
    elif m.startswith(("DC", "DCO")):
        d.update(series="C-Series", machine_type="dryer", comm="serial", board="Generic Serial")
    else:
        d.update(series="?", machine_type="?", comm="serial", board="Generic Serial")
    return d


def _kit_dexter(d, hybrid):
    s = d.get("series", "")
    mt = d.get("machine_type", "")
    if d.get("stacked"):
        return ("KIT-DEXTER-CSERIES-WASHER-READER-PRICING", "KIT-DEXTER-CSERIES-DRYER-MACHINE-PRICING",
                "high", ["STACKED -> split kit: washer + dryer portions"])
    if s == "C-Series" and mt == "washer":
        return "KIT-DEXTER-CSERIES-WASHER-READER-PRICING", None, "high", ["add -WITHBLOCKOUTS if no factory coin blockouts"]
    if s == "C-Series" and mt == "dryer":
        return "KIT-DEXTER-CSERIES-DRYER-MACHINE-PRICING", None, "high", []
    if s == "X-Series":
        return "KIT-DEXTER-XSERIES-TOUCH", None, "high", ["X-Series != C-Series kit!"]
    if s.startswith("A/V") or s == "A/V-Series":
        return "KIT-DEXTER-ASERIES01", None, "high", ["A/V-Series pulse"]
    if "Legacy" in s or s == "A-Series/Legacy":
        return "KIT-DEXTER-OLDDRYERS01", None, "medium", ["legacy stacked dryer (DDAD family)"]
    if s == "N-Series" and "dryer" in mt:
        return "KIT-DEXTER-DL2X", None, "medium", ["N-Series stack dryer, 4-pin"]
    if s == "N-Series" and mt == "washer":
        return "KIT-DEXTER-ASERIES01", None, "low", ["WCN N-Series washer, 9-pin adapter -- confirm kit"]
    return None, None, "low", ["Dexter series unclear -- confirm"]


# ---------------------------------------------------------------------------
# Other manufacturers
# ---------------------------------------------------------------------------

def _decode_other(m, brand):
    d = {"brand": brand}
    if brand == "Electrolux":
        if "WASHER" in m or m.startswith(("ELD", "W")) and "CC" in m:
            d.update(machine_type="washer", comm="serial", board="Compass")
        if m.startswith("COMPASSPRO"):
            if "DRYER" in m:
                d.update(machine_type="dryer", comm="pulse", board="Generic Relay")
            else:
                d.update(machine_type="washer", comm="serial", board="Compass")
        elif m.startswith("ELD"):
            d.update(machine_type="washer", comm="serial", board="Compass")
        elif re.match(r"^(EED|EUD|ESD|DE6|DE-?6)", m):
            d.update(machine_type="dryer", comm="pulse", board="Generic Relay")
        elif re.match(r"^W\d{3}", m):
            d.update(machine_type="washer", comm="pulse", board="Continental", note="Wascomat mechanical -- Gen4 unsupported; Gen5/6 = KIT-WASCOMAT-MECHANICAL")
        d.setdefault("machine_type", "washer"); d.setdefault("comm", "serial"); d.setdefault("board", "Compass")
    elif brand == "Continental":
        if m.startswith("EH"):
            d.update(size="large", machine_type="washer", comm="serial", board="Continental", note="COM board (post-2015) = serial; pre-2015/no-COM = pulse")
        elif m.startswith("REM"):
            d.update(size="small", machine_type="washer", comm="serial", board="Continental")
        elif m.startswith("EM"):
            d.update(size="medium", machine_type="washer", comm="serial", board="Continental")
        else:
            d.update(machine_type="washer", comm="serial", board="Continental")
    elif brand == "LG":
        d.update(machine_type="washer/dryer", comm="LG protocol", board="LG")
    elif brand == "ADC":
        d.update(machine_type="dryer", comm="pulse", board="Generic Relay", note="ADC = always Generic Relay; Phase5(3 buttons)/Phase7(4) -- needs button count")
    elif brand == "Maytag/Whirlpool":
        mt = "dryer" if re.match(r"^(MDG|MLG|MDE|MLE|WED|WQD|WUD|MYR|MHN)", m) else "washer"
        d.update(machine_type=mt, comm="pulse", board="Generic Relay")
    elif brand == "Fagor":
        mt = "dryer" if m.startswith(("FD", "SD", "LN")) else "washer"
        d.update(machine_type=mt, comm="serial", board="Generic Serial")
    elif brand == "Wascomat":
        d.update(machine_type="washer", comm="pulse", board="Continental",
                 note="Wascomat mechanical: Gen4 NOT supported; Gen5/6 = KIT-WASCOMAT-MECHANICAL")
    elif brand == "Milnor":
        mt = "dryer" if re.match(r"^(MDR|DR)", m) else "washer"
        d.update(machine_type=mt, comm="pulse (no busy)", board="Generic Relay",
                 note="Pellerin Milnor washer — reader_lookup row 193 (Milnor) maps to "
                      "KIT-PULSE-WASHER-NO-BUSY but its regex MC[RT][0-9] only covers the MCR/MCT "
                      "prefix, so the WCR form falls through")
    return d


def _kit_other(d, hybrid):
    b = d.get("brand")
    mt = d.get("machine_type", "")
    if b == "Milnor":
        if "washer" in mt:
            return "KIT-PULSE-WASHER-NO-BUSY", None, "low", [
                "Milnor washer -> reader_lookup row 193 kit KIT-PULSE-WASHER-NO-BUSY (pulse, no busy).",
                "Row 193 regex MC[RT][1-9][0-9] misses the WCR prefix — Oleg: broaden to "
                "[MW]C[RT][1-9][0-9] so WCR/MCR/MCT all resolve.",
                "Confirm WCR uses the same reader as MCR/MCT Milnor washers before assigning."]
        return None, None, "none", ["Milnor dryer not in the LW reader-kit library — "
                                    "escalate to Oleg with the nameplate + control/board photo"]
    if b == "Electrolux":
        if "COMPASSPRO" in (d.get("_m") or "") or d.get("board") == "Compass":
            if "dryer" in mt:
                return "KIT-COMPASSPRODRYER-SINGLE", None, "medium", ["Compass Pro dryer = PULSE; -LEFT/-SINGLE per stack"]
            return "KIT-COMPASSPROWASHER", None, "medium", ["Compass Pro washer (serial)"]
        if "dryer" in mt:
            return "KIT-SELECTA2-DRYERS", None, "low", ["Electrolux dryer -- confirm control"]
        return "KIT-COMPASSWASHER", None, "low", ["Electrolux washer -- confirm control"]
    if b == "Continental":
        sz = d.get("size", "")
        if hybrid:
            return "KIT-CONTINENTAL-SERIAL-CARDREADY", None, "medium", ["hybrid/card-ready Continental"]
        if sz == "large":
            return "KIT-CONTINENTAL-SERIAL-LARGE", None, "medium", ["confirm COM board (serial) vs no-COM (pulse)"]
        if sz == "small":
            return "KIT-CONTINENTAL-SERIAL-SMALL", None, "medium", ["confirm COM board (serial) vs no-COM (pulse)"]
        return "KIT-CONTINENTAL-SERIAL-SMALL", None, "low", ["confirm size + COM board"]
    if b == "LG":
        return ("KIT-LG-HYBRID" if hybrid else "KIT-LG-CARDREADY"), None, "high", []
    if b == "ADC":
        return "KIT-ADC-PHASE7", "KIT-ADC-PHASE5", "low", ["NEEDS button count: 3=Phase5, 4=Phase7 (photo)"]
    if b == "Wascomat":
        return "KIT-WASCOMAT-MECHANICAL", None, "low", ["Gen5/6 only; Gen4 unsupported -- confirm generation"]
    if b == "Fagor":
        if "dryer" in mt:
            return "KIT-FAGOR-DOMUS-DRYER01", None, "medium", []
        return "KIT-FAGOR_DOMUS_WASHER02", None, "medium", []
    if b == "Maytag/Whirlpool":
        return None, None, "low", ["Maytag/Whirlpool -- multiple kits (150B / Gen1-pulse / metercase); confirm model + control"]
    return None, None, "none", ["unknown manufacturer -- escalate to Oleg with photos"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def decode(model):
    m = _norm(model)
    brand = _brand(m)
    if brand == "Alliance":
        d = _decode_alliance(m)
    elif brand == "Dexter":
        d = _decode_dexter(m)
    elif brand == "Unknown":
        d = {"brand": "Unknown"}
    else:
        d = _decode_other(m, brand)
    d["_m"] = m
    d.setdefault("brand", brand)
    return d


def assess(model, install_type=""):
    """Decode + propose a kit with confidence and a written rationale."""
    m = _norm(model)
    hybrid = _is_hybrid(install_type)
    d = decode(m)
    brand = d.get("brand", "Unknown")

    if brand == "Alliance":
        kit, kit_h, conf, notes = _kit_alliance(d, hybrid)
    elif brand == "Dexter":
        kit, kit_h, conf, notes = _kit_dexter(d, hybrid)
    elif brand == "Unknown":
        kit, kit_h, conf, notes = (None, None, "none", ["model matches no known manufacturer pattern -- escalate to Oleg"])
    else:
        kit, kit_h, conf, notes = _kit_other(d, hybrid)

    # Build the human-readable rationale (this is what a person would write to Oleg).
    bits = []
    if d.get("sub_brand"):
        bits.append(d["sub_brand"])
    bits.append(brand)
    if d.get("series"):
        bits.append(d["series"])
    if d.get("chassis"):
        bits.append(d["chassis"])
    if d.get("size"):
        bits.append(d["size"])
    mt = d.get("machine_type", "?")
    era = d.get("era", "")
    comm = d.get("comm", "")
    board = d.get("board", "")
    reasoning = (f"Decoded {m} -> {' '.join(x for x in bits if x)}"
                 + (f", {era}" if era else "")
                 + f"; {mt}"
                 + (f", {comm}" if comm else "")
                 + (f", board: {board}" if board else "")
                 + (f", install: {'hybrid' if hybrid else 'card-only'}")
                 + ".")
    if d.get("note"):
        notes = [d["note"]] + (notes or [])
    if d.get("soft_mount"):
        notes.append("soft-mount washer (special power wiring)")
    if notes:
        reasoning += " Notes: " + "; ".join(notes) + "."

    return {
        "model": model,
        "brand": brand,
        "machine_type": mt,
        "comm": comm,
        "board": board,
        "install": "hybrid" if hybrid else "card-only",
        "proposed_kit": kit,
        "proposed_kit_hybrid": kit_h,
        "confidence": conf,          # high | medium | low | none
        "escalate": conf in ("low", "none") or kit is None,
        "reasoning": reasoning,
        "attrs": {k: v for k, v in d.items() if k != "_m"},
    }


# ---------------------------------------------------------------------------
# KNOWLEDGE PROVENANCE  (update these when a review pass corrects the rules)
# ---------------------------------------------------------------------------
# Encoded from:
#   - Reader Kit Assembly Cheat Sheet (April 2026) Steps 1-3
#   - skills/reader-kit-lookup/SKILL.md (decode tables + kit reference)
#   - skills/reader-kit-lookup/references/confirmed_mappings_2026-06-17.md
#   - Oleg equivalencies (cheat sheet Step 3)
# Review loop: read reader_kit_assessments.jsonl, compare proposals to the kit that
# actually got assigned, correct the mappings above, and note the change + date here.
