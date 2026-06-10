/* admin_dedupe.js — live Admin Portal /customers dedupe via the Chrome JavaScript tool.
 *
 * Run on https://admintools.mitechisys.com/customers (after the table has rendered) using the
 * Claude-in-Chrome `javascript_tool` (action: javascript_exec). It reads EVERY customer row from
 * the DOM (the full ~2,000-row list), parses the "Main Contact" cell into name/email/phone, and
 * matches each order on email > phone > last name > business name — mirroring the proven
 * dedup.py logic. Returns ONLY the matches per order, so the full list never re-enters the chat.
 *
 * BEFORE running: replace the ORDERS array below with the in-scope SORs (from the SOR pages).
 * Use 10-digit phones; email lowercased; business = Description/Location Name; cname = contact name.
 */
(() => {
  // ----- fill this in from the SOR detail pages -----
  const ORDERS = [
    // {sor:'27302', email:'directlaundry2@aol.com', phone:'9176782095', cname:'Billy', biz:'Laundry Palace'},
  ];
  // --------------------------------------------------

  const tbl = document.querySelector('table');
  if (!tbl) return {error: 'no customer table on page'};
  let trs = Array.from(tbl.querySelectorAll('tbody tr'));
  if (!trs.length) trs = Array.from(tbl.querySelectorAll('tr')).slice(1);
  if (trs.length < 50) return {error: 'customer list not loaded yet', rows: trs.length};

  const STOP = new Set(["laundromat","laundromats","laundry","laundries","wash","washateria","coin",
    "cleaners","cleaner","llc","inc","incorporated","corp","co","the","and","of","center","centre",
    "express","services","service"]);
  const JUNK = /\b(delete|moved|not used|free to use|temp|tbd|demo|test|please delete|unused)\b/i;
  const normEmail = s => { const m=(s||'').toLowerCase().match(/[\w.+-]+@[\w.-]+\.\w+/); return m?m[0]:''; };
  const normPhone = s => { const d=(s||'').replace(/\D/g,''); return d.length>=10?d.slice(-10):''; };
  const lastName = n => { let p=(n||'').toLowerCase().replace(/[^a-z\s]/g,'').split(/\s+/).filter(Boolean);
    while(p.length>1 && STOP.has(p[p.length-1])) p.pop(); return p.length?p[p.length-1]:''; };
  const tokens = n => new Set((n||'').toLowerCase().replace(/[^a-z0-9\s]/g,' ').split(/\s+/)
    .filter(t => t.length>1 && !STOP.has(t)));
  const parseContact = raw => { const email=normEmail(raw), phone=normPhone(raw); let name='';
    for (const line of (raw||'').split('\n')){ const t=line.trim(); if(!t||t.includes('@'))continue;
      if(normPhone(t)&&!/[a-z]/i.test(t))continue; name=t; break; } return {name,email,phone}; };

  const custs = [];
  for (const r of trs) {
    const cells = Array.from(r.querySelectorAll('td,th')).map(c => (c.innerText||'').trim());
    if (cells.length < 4) continue;
    if (JUNK.test(cells[2]||'') || /(temp@temp|na@na|name@name|tbd@tbd)/i.test(cells[3]||'')) continue;
    const pc = parseContact(cells[3]||'');
    custs.push({cust_id: cells[0]||'', name: cells[2]||'', cname: pc.name, cemail: pc.email, cphone: pc.phone});
  }

  const results = {};
  for (const o of ORDERS) {
    const oe=normEmail(o.email), op=normPhone(o.phone), ol=lastName(o.cname), ot=tokens(o.biz);
    const strong=[], weak=[]; const seen=new Set();
    for (const c of custs) {
      const contact = `${c.cname} / ${c.cemail} / ${c.cphone}`;
      if (oe && c.cemail && oe===c.cemail){ if(!seen.has(c.cust_id)){seen.add(c.cust_id);
        strong.push({cust_id:c.cust_id,name:c.name,signal:'email',strength:'strong',detail:oe,contact});} continue; }
      if (op && c.cphone && op===c.cphone){ if(!seen.has(c.cust_id)){seen.add(c.cust_id);
        strong.push({cust_id:c.cust_id,name:c.name,signal:'phone',strength:'strong',detail:op,contact});} continue; }
      const cl=lastName(c.cname);
      if (ol && cl && ol===cl && !STOP.has(ol)){ weak.push({cust_id:c.cust_id,name:c.name,signal:'last_name',strength:'weak',detail:cl,contact}); continue; }
      const ct=tokens(c.name);
      if (ot.size && ct.size){ const ov=[...ot].filter(x=>ct.has(x));
        if (ov.length && ([...ot].every(x=>ct.has(x)) || ov.length>=2))
          weak.push({cust_id:c.cust_id,name:c.name,signal:'name',strength:'weak',detail:ov.join(' '),contact}); }
    }
    const uw=[]; const s2=new Set();
    for (const w of weak){ if(s2.has(w.cust_id))continue; s2.add(w.cust_id); uw.push(w); }
    const verdict = strong.length ? 'existing' : (uw.length ? 'potential' : 'new');
    results[o.sor] = {verdict, matches: strong.concat(uw.slice(0,8))};
  }
  return {n_customers: custs.length, results};
})()
