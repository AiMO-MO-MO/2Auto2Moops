// reader_match.js — run via Chrome connector javascript_tool on ANY moops.mitechisys.com tab.
//
// ONE call for the WHOLE run: fetch the Reader Lookup dataset once and match every target model
// (collected from all in-scope SORs by sor_readers_extract.js) against every reg_ex locally.
// This is the reader-kit analogue of the single Admin /customers scrape — never drive the
// /reader_lookup search box per model.
//
// BEFORE running: fill MODELS with the UNASSIGNED models from the SOR kit tables
//   (the rows where assigned === false). Pass the full coded model (e.g. SCT080VY0FXB80BA00).
//
// Output per model: { matched:bool, rows:[{id,mfr,desc,reg_ex,question_id,card_kit,card_part_id,
//   hybrid_part_id,secondary}] }. matched=false ⇒ no regex hit ⇒ decode + propose a new regex
// (escalate to Oleg). matched=true with hybrid_part_id=null on a HYBRID order ⇒ the row has no
// hybrid mapping (see SKILL.md "reader-kit classification").

(async () => {
  // ----- fill from the unassigned SOR kit rows -----
  const MODELS = [
    // "SCT080VY0FXB80BA00", "ST075NVY0RXS6NC000",
  ];
  // -------------------------------------------------
  const idx = await fetch('/reader_lookup/index').then(r => r.json());
  const summ = (row) => ({
    id: row.id, mfr: row.manufacturer, desc: (row.description || '').slice(0, 70),
    reg_ex: row.reg_ex, question_id: row.question_id || null, sample: row.sample_model || '',
    kits: (row.reader_lookup_parts || []).map(p => ({
      card_part_id: p.part_id, card_kit: p.part && p.part.part_number,
      hybrid_part_id: p.part_hybrid_id, secondary: !!p.is_secondary_kit,
    })),
  });
  const results = {};
  for (const m of MODELS) {
    const hits = idx.filter(row => { try { return new RegExp(row.reg_ex, 'i').test(m); } catch (e) { return false; } });
    results[m] = { matched: hits.length > 0, rows: hits.map(summ) };
  }
  return JSON.stringify({ n_rows: idx.length, results }, null, 0);
})();
