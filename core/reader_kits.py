"""
Reader-kit resolution for intake (read-only, advisory).

Intake's job here: for each System/Route SOR, surface the reader kits MOOPS could
NOT assign ("No reader kit assigned"), plus any machine model numbers dealers bury
in the SOR comments, and PROPOSE the likely KIT-* for each with a confidence.

Never writes to MOOPS. Proposals are suggestions for the human at the intake gate
(and, ultimately, for Oleg when a model has no regex at all).

Two data sources, one matcher:
  1. Card Reader Kits table on the SOR  -> extract_reader_table(page)  (per-SOR read)
  2. Model tokens in the SOR comments    -> extract_comment_models(text) (pure python)
  -> resolve_models(page, models): fetch /reader_lookup/index ONCE per batch and
     regex-match every model locally (the reader analogue of the single Admin
     /customers scrape; never drive the /reader_lookup search box per model).

Strength rubric (see build_order_summary):
  strong  - exactly 1 regex row hit, model from the reader-kit TABLE (coded model)
  medium  - exactly 1 hit but model from COMMENTS, or the hit row is question-dependent
  weak    - >1 regex rows hit (collision) -> best guess shown
  none    - 0 hits -> decode + new regex needed (Oleg)
"""

import json
import os
import re
from datetime import datetime

from core import reader_decode


# ---------------------------------------------------------------------------
# 1. Card Reader Kits table (per-SOR; page already on /order-requests/<id>)
# ---------------------------------------------------------------------------

# Ported from skills/moops-dedupe/references/sor_readers_extract.js
# (validated on SOR-28090, 2026-06-23). Returns {install_type, machines:[...]}.
_READER_TABLE_JS = r"""
() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();

  // Installation type (CARD-ONLY / COIN+CARD (HYBRID) / ...)
  let installType = '';
  const all = Array.from(document.querySelectorAll('*'));
  for (let i = 0; i < all.length; i++) {
    if (all[i].children.length === 0 && /^installation type$/i.test(norm(all[i].textContent))) {
      for (let j = i + 1; j < all.length; j++) {
        const t = norm(all[j].textContent);
        if (t && !/^installation type$/i.test(t)) { installType = t; break; }
      }
      break;
    }
  }

  // Card Reader Kits table, found by its header cells.
  let tbl = null;
  for (const t of Array.from(document.querySelectorAll('table'))) {
    const head = norm((t.querySelector('tr') || {}).innerText);
    if (/Target ID or Model/i.test(head) && /Reader Kit/i.test(head)) { tbl = t; break; }
  }

  const machines = [];
  if (tbl) {
    const trs = Array.from(tbl.querySelectorAll('tr'));
    for (let i = 0; i < trs.length; i++) {
      const cells = Array.from(trs[i].querySelectorAll('th,td')).map(c => norm(c.innerText));
      if (cells.length < 4) continue;
      const model = cells[0];
      if (!model || /^target id/i.test(model) || /^comment/i.test(model)) continue;
      if (!/[A-Z0-9]{4,}/i.test(model)) continue;
      const kit = cells[3] || '';
      let qty = '', kitsNeeded = '';
      const nxt = trs[i + 1] && Array.from(trs[i + 1].querySelectorAll('th,td')).map(c => norm(c.innerText));
      if (nxt && nxt.length === 1) {
        const q = nxt[0].match(/Quantity:\s*(\d+)/i);
        const k = nxt[0].match(/Kits?\s*Needed:\s*(\d+)/i);
        qty = q ? q[1] : ''; kitsNeeded = k ? k[1] : '';
      }
      machines.push({
        model: model,
        desc: cells[1] || '',
        kit: kit,
        secondary: cells[4] || '',
        assigned: !/no reader kit assigned/i.test(kit),
        qty: qty,
        kits_needed: kitsNeeded,
      });
    }
  }
  return { install_type: installType, machines: machines };
}
"""


def extract_reader_table(page):
    """Read the Card Reader Kits table from the SOR page currently loaded.
    Returns {"install_type": str, "machines": [ {model, desc, kit, secondary,
    assigned, qty, kits_needed} ]}. Safe: returns empty on any failure."""
    try:
        data = page.evaluate(_READER_TABLE_JS)
        return {
            "install_type": (data or {}).get("install_type", "") or "",
            "machines": (data or {}).get("machines", []) or [],
        }
    except Exception as e:
        print(f"[WARN] reader table read failed: {e}")
        return {"install_type": "", "machines": []}


# ---------------------------------------------------------------------------
# 2. Model tokens buried in the SOR comments (pure python; self-filtered later)
# ---------------------------------------------------------------------------

# Obvious non-models that can look model-ish; the regex self-filter (a token only
# survives if it MATCHES a reader_lookup regex) does most of the work, this just
# trims noise up front.
_STOP_TOKENS = {"CARD-MD", "VAC01", "VAC02", "VAC03", "VAC07", "VAC08"}


def extract_comment_models(text):
    """Pull candidate machine-model tokens out of free-text SOR comments.
    Heuristic only: uppercased tokens >=5 chars containing BOTH a letter and a
    digit. Precision comes later from resolve_models (keep only regex matches)."""
    if not text:
        return []
    out, seen = [], set()
    for raw in re.split(r"[\s,;/|]+", text):
        t = raw.strip().strip(".,:;()[]{}\"'").upper()
        if len(t) < 5 or len(t) > 30:
            continue
        if not re.fullmatch(r"[A-Z0-9\-]+", t):
            continue
        if not re.search(r"[A-Z]", t) or not re.search(r"[0-9]", t):
            continue
        if t in _STOP_TOKENS or t.startswith("CARD-") or t.startswith("VAC"):
            continue
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# 3. Batch matcher: fetch /reader_lookup/index ONCE, regex-match every model
# ---------------------------------------------------------------------------

# Ported from skills/moops-dedupe/references/reader_match.js. Runs in the page
# (same-origin fetch off Matt's logged-in MOOPS session).
_MATCH_JS = r"""
async (models) => {
  const idx = await fetch('/reader_lookup/index', {credentials:'include'}).then(r => r.json());
  const results = {};
  for (const m of models) {
    let hits = [];
    for (const row of idx) {
      let re = null;
      try { re = new RegExp(row.reg_ex, 'i'); } catch (e) { continue; }
      if (re.test(m)) {
        const parts = (row.reader_lookup_parts || []);
        hits.push({
          id: row.id,
          mfr: row.manufacturer || '',
          question_id: row.question_id || null,
          card_kit: (parts[0] && parts[0].part && parts[0].part.part_number) || null,
          hybrid_part_id: (parts[0] && parts[0].part_hybrid_id) || null,
          n_kits: parts.length,
        });
      }
    }
    results[m] = { matched: hits.length > 0, n_hits: hits.length, hits: hits };
  }
  return { n_rows: idx.length, results: results };
}
"""


def resolve_models(page, models):
    """Match a list of model strings against the live reader_lookup regex table.
    Returns {model: {matched, n_hits, hits:[{id,mfr,question_id,card_kit,...}]}}.
    One network round-trip regardless of how many models. Safe on failure."""
    models = sorted({(m or "").strip() for m in models if (m or "").strip()})
    if not models:
        return {}
    try:
        data = page.evaluate(_MATCH_JS, models)
        return (data or {}).get("results", {}) or {}
    except Exception as e:
        print(f"[WARN] reader_lookup match failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# 4. Per-order summary the board renders
# ---------------------------------------------------------------------------

def _strength(source, res):
    """Confidence in the proposed kit. See module docstring rubric."""
    if not res or not res.get("matched"):
        return "none"
    n = res.get("n_hits", 0)
    if n > 1:
        return "weak"
    hit = (res.get("hits") or [{}])[0]
    if source == "comment" or hit.get("question_id"):
        return "medium"
    return "strong"


# decoder confidence -> board strength pill
_DECODE_STRENGTH = {"high": "medium", "medium": "weak", "low": "weak", "none": "none"}


def build_order_summary(order, resolved):
    """Given an order (with reader_table + comment models already attached) and the
    batch `resolved` regex map, produce order['reader_kits'] for the board.

    Two-layer proposal per MISSING kit:
      1. regex table (authoritative) -> method="regex"
      2. if the regex misses, the DECODER (core.reader_decode) makes a judgement from
         the model number -> method="decoder" (proposal + confidence + reasoning)
    Machines MOOPS already assigned are counted but not listed."""
    table = order.get("reader_table") or {}
    machines = table.get("machines", [])
    install_type = table.get("install_type", "")

    assigned = [m for m in machines if m.get("assigned")]
    unassigned = [m for m in machines if not m.get("assigned")]

    missing = []
    seen = set()

    def add(model, source, qty=""):
        key = (model.upper(), source)
        if not model or key in seen:
            return
        seen.add(key)
        res = resolved.get(model.upper()) or resolved.get(model) or {}

        if res.get("matched"):
            # ---- Layer 1: regex table hit ----
            strg = _strength(source, res)
            hit = (res.get("hits") or [{}])[0]
            note = ""
            if strg == "weak":
                note = f'{res.get("n_hits",0)} regex rows matched — best guess'
            elif hit.get("question_id"):
                note = "kit depends on a MOOPS question (verify)"
            if hit.get("card_kit") and not hit.get("hybrid_part_id") \
                    and "hybrid" in (install_type or "").lower():
                note = (note + "; " if note else "") + "no hybrid kit on this row"
            missing.append({
                "model": model, "source": source, "qty": qty,
                "method": "regex",
                "proposed_kit": hit.get("card_kit"),
                "proposed_kit_hybrid": None,
                "strength": strg,
                "n_hits": res.get("n_hits", 0),
                "regex_id": hit.get("id"),
                "mfr": hit.get("mfr", ""),
                "reasoning": f"Matched reader_lookup regex row {hit.get('id')} ({hit.get('mfr','')}).",
                "note": note,
                "escalate": False,
            })
        else:
            # ---- Layer 2: decoder judgement ----
            a = reader_decode.assess(model, install_type)
            missing.append({
                "model": model, "source": source, "qty": qty,
                "method": "decoder",
                "proposed_kit": a.get("proposed_kit"),
                "proposed_kit_hybrid": a.get("proposed_kit_hybrid"),
                "strength": _DECODE_STRENGTH.get(a.get("confidence", "none"), "none"),
                "decode_confidence": a.get("confidence"),
                "n_hits": 0,
                "regex_id": None,
                "mfr": a.get("brand", ""),
                "reasoning": a.get("reasoning", ""),
                "note": ("no regex — decoded proposal (verify / send to Oleg)"
                         if not a.get("escalate")
                         else "no regex + low decode confidence — send to Oleg"),
                "escalate": bool(a.get("escalate")),
            })

    for m in unassigned:
        add(m.get("model", ""), "table", m.get("qty", ""))
    # comment models: surface a model dug out of comments if EITHER the regex matches
    # OR the decoder recognizes the manufacturer (so real buried models aren't lost,
    # but random tokens still are).
    for model in order.get("comment_models", []):
        res = resolved.get(model.upper()) or resolved.get(model) or {}
        if res.get("matched") or reader_decode.decode(model).get("brand", "Unknown") != "Unknown":
            add(model, "comment")

    order["reader_kits"] = {
        "has_missing": len(missing) > 0,
        "install_type": install_type,
        "assigned_count": len(assigned),
        "missing": missing,
    }
    return order["reader_kits"]


# ---------------------------------------------------------------------------
# 5. Assessment log -- the documented record for later review / training
# ---------------------------------------------------------------------------

def write_assessment_log(orders, repo_root, generated_at=None):
    """Append one JSONL line per MISSING kit assessment to reader_kit_assessments.jsonl.

    This is the durable record: what the tool saw and what it proposed, so a later
    review pass (you + Claude) can compare against the kit that actually got assigned,
    correct core/reader_decode.py, and improve the knowledge. Append-only; never
    overwrites history."""
    generated_at = generated_at or datetime.now().isoformat(timespec="seconds")
    path = os.path.join(repo_root, "reader_kit_assessments.jsonl")
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for o in orders:
            rk = o.get("reader_kits") or {}
            for mm in rk.get("missing", []):
                rec = {
                    "ts": generated_at,
                    "sor_no": o.get("sor_no", ""),
                    "sor_id": o.get("sor_id", ""),
                    "install_type": rk.get("install_type", ""),
                    "model": mm.get("model", ""),
                    "source": mm.get("source", ""),
                    "method": mm.get("method", ""),
                    "proposed_kit": mm.get("proposed_kit"),
                    "proposed_kit_hybrid": mm.get("proposed_kit_hybrid"),
                    "strength": mm.get("strength"),
                    "decode_confidence": mm.get("decode_confidence"),
                    "reasoning": mm.get("reasoning", ""),
                    "escalate": mm.get("escalate", False),
                    # left blank for the review pass to fill in later:
                    "actual_kit": None,
                    "review_verdict": None,   # "correct" | "wrong" | "partial"
                    "review_note": None,
                }
                f.write(json.dumps(rec) + "\n")
                n += 1
    print(f"  reader_kit_assessments.jsonl += {n} assessment(s)")
    return n
