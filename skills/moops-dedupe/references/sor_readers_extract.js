// sor_readers_extract.js — run via Chrome connector javascript_tool on
//   https://moops.mitechisys.com/order-requests/<id>
//
// Companion to sor_extract.js: pulls the **Card Reader Kits** table + the order-level
// **Installation type** for the reader-kit step of the dedupe run. Run it in the SAME
// browser_batch as sor_extract.js (same navigation, second javascript_tool call) so the
// SOR page is read once.
//
// Output: { sor, installType, machines:[{model,desc,partReq,kit,secondary,assigned,qty,kitsNeeded}] }
//   kit      — the "Reader Kit" cell text; "No reader kit assigned" when MOOPS couldn't resolve
//   assigned — false when kit === "No reader kit assigned"
//   qty / kitsNeeded — parsed from the following "Split up kit" comment row
//
// DOM (validated on SOR-28090, 2026-06-23): a <table> whose header row is
//   ["Target ID or Model #","Description","Part Req.","Reader Kit","Secondary Reader Kit"].
// Each machine is a 5-cell <tr>; the next <tr> is a single cell
//   "Comment: … Split up kit: Quantity: N # Kits Needed: N".

(async () => {
  for (let i = 0; i < 40; i++) { if (!document.body.textContent.includes('{{')) break; await new Promise(r => setTimeout(r, 250)); }
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const sorMatch = location.pathname.match(/order-requests\/(\d+)/);

  // Installation type — labeled field (CARD-ONLY / COIN+CARD (HYBRID) / …)
  let installType = '';
  const all = Array.from(document.querySelectorAll('*'));
  for (let i = 0; i < all.length; i++) {
    if (all[i].children.length === 0 && /^installation type$/i.test(norm(all[i].textContent))) {
      for (let j = i + 1; j < all.length; j++) { const t = norm(all[j].textContent); if (t && !/^installation type$/i.test(t)) { installType = t; break; } }
      break;
    }
  }

  // Find the Card Reader Kits table by its header cells.
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
      if (cells.length < 4) continue;                       // comment/qty rows have 1 cell
      const model = cells[0];
      if (!model || /^target id/i.test(model) || /^comment/i.test(model)) continue;  // header / stray
      if (!/[A-Z0-9]{4,}/i.test(model)) continue;           // must look like a model/target id
      const kit = cells[3] || '';
      // the following single-cell row carries qty / # kits needed
      let qty = '', kitsNeeded = '';
      const nxt = trs[i + 1] && Array.from(trs[i + 1].querySelectorAll('th,td')).map(c => norm(c.innerText));
      if (nxt && nxt.length === 1) {
        const q = nxt[0].match(/Quantity:\s*(\d+)/i); const k = nxt[0].match(/Kits?\s*Needed:\s*(\d+)/i);
        qty = q ? q[1] : ''; kitsNeeded = k ? k[1] : '';
      }
      machines.push({
        model, desc: cells[1] || '', partReq: cells[2] || '', kit, secondary: cells[4] || '',
        assigned: !/no reader kit assigned/i.test(kit), qty, kitsNeeded,
      });
    }
  }

  return JSON.stringify({ sor: sorMatch ? sorMatch[1] : '', installType, machines }, null, 0);
})();
