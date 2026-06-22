// sor_extract.js — run via Chrome connector javascript_tool on
//   https://moops.mitechisys.com/order-requests/<id>
//
// Returns ONLY the dedupe signals for ONE SOR as compact JSON.
// This REPLACES get_page_text on the SOR detail page — do not dump the whole page.
// Batch all in-scope SORs in ONE browser_batch (navigate + javascript_tool per SOR).
//
// Output: { sor, desc, locName, locAddr, cName, cEmail, cPhone, existingEC, phone10, emailKey }
//
// Strategy: the detail page is a list of labeled fields. grab(label) finds the leaf element whose
// text exactly equals the label, then returns the next non-empty leaf's text (the value).

(async () => {
  // Wait for Angular render.
  for (let i = 0; i < 40; i++) {
    if (!document.body.textContent.includes('{{')) break;
    await new Promise(r => setTimeout(r, 250));
  }

  const norm = s => (s || '').replace(/\s+/g, ' ').trim();

  // All leaf elements (no element children) in document order, with their trimmed text.
  const leaves = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  while (walker.nextNode()) {
    const el = walker.currentNode;
    if (el.children.length === 0) {
      const t = norm(el.textContent);
      if (t) leaves.push(t);
    }
  }

  // Find a label (case-insensitive, label may end with ':') and return the next leaf's text.
  const grab = (label) => {
    const want = label.toLowerCase().replace(/:$/, '');
    for (let i = 0; i < leaves.length; i++) {
      const t = leaves[i].toLowerCase().replace(/:$/, '');
      if (t === want) {
        for (let j = i + 1; j < leaves.length; j++) {
          const v = leaves[j];
          // skip if the next leaf is itself another label
          if (v && v.toLowerCase().replace(/:$/, '') !== want) return v;
        }
      }
    }
    return '';
  };

  const sorMatch = location.pathname.match(/order-requests\/(\d+)/);
  const cEmail = grab('New Contact Email') || grab('Contact Email') || grab('Email');
  const cPhone = grab('New Contact Phone') || grab('Contact Phone') || grab('Phone');

  const data = {
    sor: sorMatch ? sorMatch[1] : '',
    desc: grab('Description'),
    locName: grab('Location Name'),
    locAddr: grab('Location Address') || grab('Shipping Address'),
    cName: grab('New Contact Name') || grab('Contact Name'),
    cEmail,
    cPhone,
    existingEC: grab('Existing End Customer') || grab('End Customer'),
    phone10: (cPhone.match(/\d/g) || []).join('').slice(-10),
    emailKey: cEmail.toLowerCase().split(/[\s,;]+/)[0] || '',
  };

  return JSON.stringify(data, null, 0);
})();
