#!/usr/bin/env python3
"""
render_board.py — build dedupe_board.html from a self-contained dedupe_results.json.

Usage:  python render_board.py [dedupe_results.json] [dedupe_board.html]

dedupe_results.json schema:
{
  "orders": [
    {
      "sor": "SOR-26542",
      "sor_url": "https://moops.mitechisys.com/order-requests/26542",
      "desc": "ESD replacement order",
      "loc": "Bubble Blast Laundromat — 17445 US-192, Clermont FL",
      "contact": "Inigo Sanchez · inigosangon@gmail.com · 213-793-1347",
      "admin": {"verdict": "potential|existing|new",
                "matches": [{"id":"00608","name":"…","signal":"name","strength":"weak|strong","detail":"…","contact":"…"}]},
      "sf":    {"verdict": "existing|potential|new",
                "matches": [{"object":"Account","id":"001…","name":"…","lw":"00138"|null,"signal":"address",
                             "strength":"strong|weak","note":"…","url":"https://…/view"}]},
      "verdict": "NEW | EXISTING — both | EXISTING — SF only | EXISTING — 🚩 FLAG | …",
      "flag": "🚩 HARD TO COMBINE — Cents Location ID 513 …"  (optional banner),
      "why":  "one-line rationale"
    }
  ]
}
"""
import html, json, os, sys

VC = {"existing": "#2F6F4E", "potential": "#BA7517", "new": "#5F5E5A"}
VL = {"existing": "Existing", "potential": "Possible", "new": "New"}
SF_BASE = "https://trycentssf.lightning.force.com/lightning/r/{}/{}/view"


def e(v): return html.escape(str(v if v is not None else ""))


def pill(v):
    c = VC.get(v, "#5F5E5A")
    return (f'<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:7px;'
            f'background:{c}1a;color:{c}">{e(VL.get(v, v))}</span>')


def verdict_color(v):
    s = (v or "").lower()
    if "flag" in s: return "#A32D2D"
    if "existing" in s and "both" in s: return "#2F6F4E"
    if "existing" in s: return "#BA7517"
    return "#5F5E5A"


def arow(m):
    return (f'<div class="m"><span class="st st-{e(m.get("strength") or "weak")}">'
            f'{e(m.get("strength") or "?")}</span><span class="mid">{e(m.get("id"))}</span>'
            f'<span class="mn">{e(m.get("name"))}</span><span class="mo">{e(m.get("detail") or m.get("signal"))}</span>'
            + (f'<span class="mc">{e(m.get("contact"))}</span>' if m.get("contact") else "") + '</div>')


def srow(m):
    obj = m.get("object", "Account")
    url = m.get("url") or (SF_BASE.format(obj, m.get("id")) if m.get("id") else "")
    lw = (f'<span class="lw">cust {e(m["lw"])}</span>' if m.get("lw")
          else '<span class="lw lw-none">no LW link</span>')
    nm = (f'<a href="{e(url)}" class="mn-link">{e(m.get("name"))}</a>' if url
          else f'<span class="mn">{e(m.get("name"))}</span>')
    note = m.get("note", "")
    return (f'<div class="m"><span class="st st-{e(m.get("strength") or "weak")}">'
            f'{e(m.get("strength") or "?")}</span><span class="obj">{e(obj)}</span>{nm}{lw}'
            f'<span class="mo">{e(m.get("detail") or m.get("signal"))}</span>'
            + (f'<span class="mc"><span class="note">{e(note)}</span></span>' if note else "") + '</div>')


def block(label, blk, rowfn):
    blk = blk or {}
    rows = "".join(rowfn(m) for m in blk.get("matches", [])) or '<div class="empty">no match</div>'
    return (f'<div class="sys"><div class="sys-hd"><span class="sys-name">{e(label)}</span>'
            f'{pill(blk.get("verdict"))}</div>{rows}</div>')


def build(orders):
    cards = []
    for o in orders:
        a = block("Admin Portal (live)", o.get("admin"), arow)
        s = block("Salesforce (live)", o.get("sf"), srow)
        vc = verdict_color(o.get("verdict"))
        flag = (f'<div class="flag" style="background:{vc}14;color:{vc}">{e(o["flag"])}</div>'
                if o.get("flag") else "")
        link = (f'<a href="{e(o.get("sor_url"))}">{e(o.get("sor"))}</a>' if o.get("sor_url")
                else e(o.get("sor")))
        cards.append(
            f'<div class="card"><div class="hd"><span class="cust">{e(o.get("desc") or o.get("sor"))}</span>'
            f'<span class="loc">{e(o.get("loc"))}</span><span class="links">{link}</span></div>'
            f'<div class="oi"><span class="oi-l">This order</span> {e(o.get("contact"))}</div>'
            f'<div class="cols">{a}{s}</div>{flag}'
            f'<div class="verdict"><span class="vb" style="background:{vc}1a;color:{vc}">'
            f'{e(o.get("verdict"))}</span> <span class="why">{e(o.get("why"))}</span></div></div>')
    n = len(orders)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Dedupe board</title><style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#2C2C2A;background:#F1EFE8;margin:0;padding:24px}}
.wrap{{max-width:1040px;margin:0 auto}}h1{{font-size:22px;font-weight:500;margin:0 0 2px}}
.sub{{color:#5F5E5A;font-size:13px;margin-bottom:18px}}
.card{{background:#fff;border:.5px solid #D3D1C7;border-radius:12px;padding:14px 16px;margin-bottom:12px}}
.hd{{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:6px}}
.cust{{font-weight:600;font-size:15px}}.loc{{color:#5F5E5A;font-size:13px}}
.links{{margin-left:auto}}.links a{{color:#185FA5;text-decoration:none;font-size:12px;font-family:ui-monospace,Menlo,monospace}}
.oi{{font-size:12.5px;color:#5F5E5A;margin-bottom:10px}}.oi-l{{font-weight:600;color:#2C2C2A}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:740px){{.cols{{grid-template-columns:1fr}}}}
.sys{{border:.5px solid #D3D1C7;border-radius:9px;padding:9px 11px;background:#FAFAF7}}
.sys-hd{{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px}}
.sys-name{{font-size:11.5px;font-weight:600;text-transform:uppercase;color:#5F5E5A}}
.m{{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px;padding:5px 0;border-top:.5px solid #D3D1C7;font-size:12px}}
.m:first-of-type{{border-top:0}}
.st{{font-size:10px;font-weight:700;text-transform:uppercase;padding:1px 6px;border-radius:6px}}
.st-strong{{background:#2F6F4E1a;color:#2F6F4E}}.st-weak{{background:#BA75171a;color:#BA7517}}
.mid,.lw{{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#5F5E5A}}.lw-none{{font-style:italic}}
.obj{{font-size:9.5px;font-weight:600;text-transform:uppercase;color:#185FA5;background:#185FA51a;padding:1px 5px;border-radius:6px}}
.note{{font-style:italic;color:#BA7517}}.mn,.mn-link{{font-weight:600}}.mn-link{{color:#185FA5;text-decoration:none}}
.mo{{color:#5F5E5A}}.mc{{flex-basis:100%;color:#5F5E5A;font-size:11px}}
.empty{{color:#5F5E5A;font-size:12px;font-style:italic;padding:3px 0}}
.flag{{margin-top:10px;padding:7px 10px;border-radius:8px;font-size:12.5px;font-weight:600}}
.verdict{{margin-top:10px;padding-top:9px;border-top:.5px solid #D3D1C7;font-size:12.5px}}
.vb{{font-weight:700;font-size:11px;text-transform:uppercase;padding:2px 8px;border-radius:7px;margin-right:8px}}
.why{{color:#5F5E5A}}
</style></head><body><div class="wrap">
<h1>Dedupe board — Admin Portal + Salesforce (live)</h1>
<div class="sub">{n} system order{"s" if n != 1 else ""} &middot; all signals &middot; 🚩 red flag = Cents Location ID present</div>
{''.join(cards) if cards else '<div class="card">No orders.</div>'}
</div></body></html>"""


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "dedupe_results.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "dedupe_board.html"
    if not os.path.exists(src):
        sys.exit(f"results file not found: {src}")
    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    orders = data.get("orders", []) if isinstance(data, dict) else data
    with open(out, "w", encoding="utf-8") as f:
        f.write(build(orders))
    print(f"[OK] wrote {out} ({len(orders)} orders)")


if __name__ == "__main__":
    main()
