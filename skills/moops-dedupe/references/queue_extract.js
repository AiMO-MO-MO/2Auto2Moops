// queue_extract.js — run via Chrome connector javascript_tool on
//   https://moops.mitechisys.com/order-requests
//
// Returns ONLY the in-scope SORs (Submitted/In Review AND Laundromat System) as compact JSON.
// This REPLACES get_page_text on the queue — do not dump the whole page into context.
//
// Output: [{ sor, type, linkedSO, desc }]
//   sor      — SOR number (string, no "SOR-" prefix)
//   type     — order type text (e.g. "Laundromat System")
//   linkedSO — linked Sales Order id if the row shows one, else ""
//   desc     — description / store name cell
//
// The page is Angular-rendered; wait briefly for the rows to paint before walking.

(async () => {
  // Wait for Angular to render (braces gone + at least one order-request link present).
  for (let i = 0; i < 40; i++) {
    if (!document.body.textContent.includes('{{') &&
        document.querySelector('a[href*="/order-requests/"]')) break;
    await new Promise(r => setTimeout(r, 250));
  }

  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  let section = '';

  // Walk the document in order so we can track the current status heading above each row group.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  const seen = new Set();

  while (walker.nextNode()) {
    const el = walker.currentNode;
    const tag = el.tagName;

    // Section headings carry the status group (Submitted/In Review, Awaiting Update, etc.).
    if (/^H[1-6]$/.test(tag) || el.classList.contains('status-heading') ||
        el.getAttribute('role') === 'heading') {
      const t = norm(el.textContent);
      if (/submitted|review|awaiting|accepted|cancel/i.test(t) && t.length < 60) section = t;
      continue;
    }

    // Rows: anything containing an order-request link is a SOR row.
    if (tag === 'TR' || el.classList.contains('order-request-row')) {
      const link = el.querySelector('a[href*="/order-requests/"]');
      if (!link) continue;
      const m = (link.getAttribute('href') || '').match(/order-requests\/(\d+)/);
      if (!m) continue;
      const sor = m[1];
      if (seen.has(sor)) continue;
      seen.add(sor);

      const rowText = norm(el.textContent);
      const typeMatch = rowText.match(/Laundromat System|Multi-?family[^,]*|Cards?|Parts[^,]*|Readers?[^,]*/i);
      const type = typeMatch ? norm(typeMatch[0]) : '';
      const soLink = el.querySelector('a[href*="order_id="]');
      const soMatch = soLink && (soLink.getAttribute('href') || '').match(/order_id=(\d+)/);

      out.push({
        sor,
        section: section || '',
        type,
        linkedSO: soMatch ? soMatch[1] : '',
        desc: norm(link.textContent) || rowText.slice(0, 80),
      });
    }
  }

  // In-scope filter: Submitted/In Review status AND Laundromat System type.
  const scoped = out.filter(r =>
    /submitted|review/i.test(r.section) && /laundromat system/i.test(r.type));

  return JSON.stringify(scoped.length ? scoped : out, null, 0);
})();
