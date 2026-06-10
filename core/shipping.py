"""Pure shipping/tracking helpers -- no browser code (browser reads live in core/moops.py).

Carrier names match the SO's main_shipment_carrier_id <select> options exactly.
URL patterns for UPS/FedEx/DHL/USPS are standard public tracking links. The LTL
carriers (Polaris, CSA, RXO/CoyoteGo) point at their tracking landing pages --
VERIFY the deep-link patterns the first time each carrier appears in a real run.
"""

import re

TRACKING_URLS = {
    "UPS": "https://www.ups.com/track?tracknum={n}",
    "FedEx": "https://www.fedex.com/fedextrack/?trknbr={n}",
    "DHL": "https://www.dhl.com/us-en/home/tracking.html?tracking-id={n}",
    "USPS": "https://tools.usps.com/go/TrackConfirmAction?tLabels={n}",
    # LTL carriers -- landing pages; deep-link params unverified (paste the PRO if needed)
    "Polaris": "https://www.polaristransport.com/track",
    "CSA Transportation": "https://www.csatransportation.com/tools/track-trace",
    "RXO/CoyoteGo": "https://www.rxo.com/track-a-shipment",
}


def tracking_url(carrier: str, number: str) -> str:
    """Public tracking URL for a carrier + tracking/PRO number ('' if no number)."""
    number = (number or "").strip()
    if not number:
        return ""
    pattern = TRACKING_URLS.get((carrier or "").strip(), "")
    if not pattern:
        return ""
    return pattern.format(n=number) if "{n}" in pattern else pattern


def parse_assembler_notes(text: str) -> dict:
    """Extract pinpad serials, key serials, and lock codes from notes_from_assemblers.

    Real-world format (one item per line, label variants tolerated):
        PIN PAD S/N : 552-185-795
        KEYS S/N : 7MA
        LOCK CODE: 1234
    Returns {"pinpads": [...], "keys": [...], "locks": [...], "other": [...]}.
    """
    out = {"pinpads": [], "keys": [], "locks": [], "other": []}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'(?i)^(pin\s*pads?|keys?|locks?(?:\s*codes?)?)\b[^:#=]*[:#=]\s*(.+)$', line)
        if not m:
            out["other"].append(line)
            continue
        label, value = m.group(1).lower(), m.group(2).strip()
        # one line can carry several values ("552-185-795, 552-185-796")
        values = [v.strip() for v in re.split(r'[,;/]| and ', value) if v.strip()]
        if label.startswith("pin"):
            out["pinpads"].extend(values)
        elif label.startswith("key"):
            out["keys"].extend(values)
        else:
            out["locks"].extend(values)
    return out


def shipment_flags(info: dict, parsed_notes: dict) -> list:
    """Things a human should look at, derived from one SO's shipment state."""
    flags = []
    state = (info.get("work_state") or "").strip()
    if state in ("Packed", "Shipped") and not (info.get("tracking") or "").strip():
        flags.append("no main tracking number")
    if state == "Shipped" and not parsed_notes.get("pinpads"):
        flags.append("no pinpad serials in assembler notes")
    if (info.get("card_tracking") or "").strip() and not (info.get("tracking") or "").strip():
        flags.append("card shipment tracked but main shipment is not")
    return flags
