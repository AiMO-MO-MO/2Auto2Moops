#!/usr/bin/env python3
"""
render_dedupe_board.py -- combined Admin Portal + Salesforce dedupe board.

Additive: reads intake_plan.json (System orders, with the Admin `customer_check.matches`
the intake tool computed and the `customer_check.sf_match` the moops-dedupe skill wrote),
and renders dedupe_board.html showing both systems side by side per SOR. Does NOT touch
intake.py or its intake_board.html builder.

Usage:  python render_dedupe_board.py [intake_plan.json] [dedupe_board.html]
"""
import html
import json
import os
import sys

# Cents palette (matches intake_board.html)
BG, CARD, LINE, INK, MUTE, LINK = "#F1EFE8", "#fff", "#D3D1C7", "#2C2C2A", "#5F5E5A", "#185FA5"
SF_BASE = "https://trycentssf.lightning.force.com/lightning/r/{}/{}/view"
ADMIN_BASE = "https://admintools.mitechisys.com/customers"  # cust id is shown; link is best-effort

VERDICT_LBL = {"existing": "Existing", "potential": "Possible", "new": "New"}
VERDICT_COL = {"existing": "#2F6F4E", "potential": "#BA7517", "new": "#5F5E5A"}
SIG_LBL = {"email": "email", "phone": "phone", "last_name": "last name",
           "name": "business name", "sor_assigned": "named on SOR"}


def _esc(v):
    return html.escape(str(v if v is not None else ""))


def _verdict_pill(verdict):
    col = VERDICT_COL.get(verdict, MUTE)
    lbl = VERDICT_LBL.get(verdict, verdict or "?")
    return (f'<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:7px;'
            f'background:{col}1a;color:{col}">{_esc(lbl)}</span>')


def _admin_match_row(m):
    sig = SIG_LBL.get(m.get("signal", ""), m.get("signal", "") or "-")
    detail = m.get("detail", "")
    on = f"{sig}: {detail}" if detail else sig
    contact = " &middot; ".join(_esc(x) for x in (m.get("contact_name"), m.get("contact_email"),
                                                  m.get("contact_phone")) if x)
    strg = m.get("strength", "")
    return (f'<div class="m"><span class="st st-{_esc(strg) or "weak"}">{_esc(strg or "?")}</span>'
            f'<span class="mid">{_esc(m.get("cust_id"))}</span>'
            f'<span class="mn">{_esc(m.get("name"))}</span>'
            f'<span class="mo">matched {_esc(on)}</span>'
            + (f'<span class="mc">{contact}</span>' if contact else "") + '</div>')


def _sf_match_row(m):
    sig = SIG_LBL.get(m.get("signal", ""), m.get("signal", "") or "-")
    detail = m.get("detail", "")
    on = f"{sig}: {detail}" if detail else sig
    geo = " &middot; ".join(_esc(x) for x in (m.get("billing_city"), m.get("billing_state")) if x)
    contact = " &middot; ".join(_esc(x) for x in (m.get("contact_name"), m.get("contact_email"),
                                                  m.get("contact_phone")) if x)
    strg = m.get("strength", "")
    obj = m.get("object", "Account")
    name = m.get("name") or m.get("account_name")
    rid = m.get("record_id") or m.get("account_id")
    url = m.get("sf_url") or (SF_BASE.format(obj, rid) if rid else "")
    # LW id badge: customer id, else location id, else "no LW link"
    lw_acct, lw_loc = m.get("lw_account_id"), m.get("lw_location_id")
    if lw_acct:
        lw_cell = f'<span class="lw">cust {_esc(lw_acct)}</span>'
    elif lw_loc:
        lw_cell = f'<span class="lw">loc {_esc(lw_loc)}</span>'
    else:
        lw_cell = '<span class="lw lw-none">no LW link</span>'
    obj_cell = f'<span class="obj">{_esc(obj)}</span>'
    name_cell = (f'<a href="{_esc(url)}" class="mn-link">{_esc(name)}</a>'
                 if url else f'<span class="mn">{_esc(name)}</span>')
    note = m.get("note", "")
    note_cell = f'<span class="note">{_esc(note)}</span>' if note else ""
    return (f'<div class="m"><span class="st st-{_esc(strg) or "weak"}">{_esc(strg or "?")}</span>'
            f'{obj_cell}{name_cell}{lw_cell}'
            f'<span class="mo">matched {_esc(on)}</span>'
            + (f'<span class="mc">{contact}{(" &middot; " + geo) if geo else ""}{(" &middot; " + str(note_cell)) if note else ""}</span>'
               if (contact or geo or note) else "")
            + '</div>')


def _system_block(label, verdict, rows_html):
    inner = rows_html or '<div class="empty">no candidates</div>'
    return (f'<div class="sys"><div class="sys-hd"><span class="sys-name">{label}</span>'
            f'{_verdict_pill(verdict)}</div>{inner}</div>')


def build(orders):
    sysorders = [o for o in orders if o.get("classification") == "System"]
    cards = []
    for o in sysorders:
        cc = o.get("customer_check") or {}
        title = o.get("description") or o.get("location_name") or o.get("sor_no")
        oc = " &middot; ".join(_esc(x) for x in (o.get("contact_name"), o.get("contact_email"),
                                                 o.get("contact_phone")) if x) or "no contact on SOR"
        link = (f'<a href="{_esc(o.get("sor_url"))}">{_esc(o.get("sor_no"))}</a>'
                if o.get("sor_url") else _esc(o.get("sor_no")))

        admin_rows = "".join(_admin_match_row(m) for m in (cc.get("matches") or [])[:8])
        admin = _system_block("Admin Portal", cc.get("verdict"), admin_rows)

        sf = cc.get("sf_match")
        if sf is None:
            sf_block = _system_block("Salesforce", None,
                                     '<div class="empty">not yet run &mdash; run the dedupe skill</div>')
        else:
            sf_rows = "".join(_sf_match_row(m) for m in (sf.get("matches") or [])[:8])
            sf_block = _system_block("Salesforce", sf.get("verdict"), sf_rows)

        cards.append(
            f'<div class="card">'
            f'<div class="hd"><span class="cust">{_esc(title)}</span>'
            f'<span class="loc">{_esc(o.get("location_name"))}</span>'
            f'<span class="links">{link}</span></div>'
            f'<div class="oi"><span class="oi-l">This order</span><span class="oi-c">{oc}</span></div>'
            f'<div class="cols">{admin}{sf_block}</div>'
            f'</div>')

    n = len(sysorders)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Dedupe board</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:{INK};background:{BG};margin:0;padding:24px}}
 .wrap{{max-width:960px;margin:0 auto}}
 h1{{font-size:22px;font-weight:500;margin:0 0 2px}}
 .sub{{color:{MUTE};font-size:13px;margin-bottom:18px}}
 .card{{background:{CARD};border:.5px solid {LINE};border-radius:12px;padding:14px 16px;margin-bottom:12px}}
 .hd{{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:6px}}
 .cust{{font-weight:600;font-size:15px}} .loc{{color:{MUTE};font-size:13px}}
 .links{{margin-left:auto}} .links a{{color:{LINK};text-decoration:none;font-size:12px;font-family:ui-monospace,Menlo,monospace}}
 .oi{{font-size:12.5px;color:{MUTE};margin-bottom:10px}} .oi-l{{font-weight:600;margin-right:8px;color:{INK}}}
 .cols{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
 @media(max-width:680px){{.cols{{grid-template-columns:1fr}}}}
 .sys{{border:.5px solid {LINE};border-radius:9px;padding:9px 11px;background:#FAFAF7}}
 .sys-hd{{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}}
 .sys-name{{font-size:12px;font-weight:600;letter-spacing:.02em;text-transform:uppercase;color:{MUTE}}}
 .m{{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px;padding:5px 0;border-top:.5px solid {LINE};font-size:12.5px}}
 .m:first-of-type{{border-top:0}}
 .st{{font-size:10px;font-weight:700;text-transform:uppercase;padding:1px 6px;border-radius:6px}}
 .st-strong{{background:#2F6F4E1a;color:#2F6F4E}} .st-weak{{background:#BA75171a;color:#BA7517}}
 .mid,.lw{{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:{MUTE}}}
 .lw-none{{font-style:italic}}
 .obj{{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.03em;color:#185FA5;background:#185FA51a;padding:1px 6px;border-radius:6px}}
 .note{{font-style:italic;color:#BA7517}}
 .mn,.mn-link{{font-weight:600}} .mn-link{{color:{LINK};text-decoration:none}}
 .mo{{color:{MUTE}}} .mc{{flex-basis:100%;color:{MUTE};font-size:11.5px}}
 .empty{{color:{MUTE};font-size:12px;font-style:italic;padding:3px 0}}
</style></head><body><div class="wrap">
<h1>Dedupe board</h1>
<div class="sub">{n} system order{"s" if n != 1 else ""} &middot; Admin Portal + Salesforce</div>
{''.join(cards) if cards else '<div class="card">No system orders in the plan.</div>'}
</div></body></html>"""


def main():
    plan = sys.argv[1] if len(sys.argv) > 1 else "intake_plan.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "dedupe_board.html"
    if not os.path.exists(plan):
        sys.exit(f"plan not found: {plan} (run `python run.py intake` first)")
    with open(plan, encoding="utf-8") as f:
        data = json.load(f)
    orders = data.get("orders", []) if isinstance(data, dict) else data
    with open(out, "w", encoding="utf-8") as f:
        f.write(build(orders))
    n = sum(1 for o in orders if o.get("classification") == "System")
    print(f"[OK] wrote {out} ({n} system orders)")


if __name__ == "__main__":
    main()
