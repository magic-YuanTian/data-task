import { html, useState, useRef, useEffect, useApp, useFetch, settings, fmtCompact, fmtTime } from '../lib.js';
import { Loading, ErrorBox, Icon } from '../ui.js';
import { SqlRunner } from '../sql.js';

function Schema({ tables, onInsert }) {
  const [open, setOpen] = useState({});
  const [filter, setFilter] = useState('');
  const f = filter.toLowerCase();
  const shown = tables.filter((t) => !f || t.name.includes(f) || t.columns.some((c) => c.name.includes(f)));
  return html`<div>
    <div style="padding:10px 10px 6px"><input type="search" style="width:100%" placeholder="Filter tables and columns" value=${filter} onInput=${(e) => setFilter(e.target.value)} /></div>
    ${shown.map((t) => html`<div>
      <div class="t" onClick=${() => setOpen({ ...open, [t.name]: !open[t.name] })} onDblClick=${() => onInsert(t.name)} title=${t.description || 'Double-click to insert'}>
        <span class="faint" style="width:10px">${open[t.name] || f ? '▾' : '▸'}</span><span class="mono">${t.name}</span><span class="n">${fmtCompact(t.rows)}</span></div>
      ${(open[t.name] || f) && t.columns.filter((c) => !f || t.name.includes(f) || c.name.includes(f)).map((c) => html`
        <div class="c" onClick=${() => onInsert(c.name)} title=${c.description || 'not documented'}><span>${c.name}</span><span class="ty">${c.type.toLowerCase()}</span></div>`)}
    </div>`)}
  </div>`;
}

function examples(world, metrics) {
  const ex = Object.entries(metrics || {}).map(([k, m]) => ({ label: `Metric: ${k}`, sql: m.sql.trim() }));
  ex.unshift({ label: 'Tables and row counts', sql: 'SELECT table_name, estimated_size AS approx_rows, column_count\nFROM duckdb_tables()\nORDER BY table_name;' });
  return ex;
}

export function SqlPage({ world, query }) {
  const app = useApp();
  const seed = app.seed(world);
  const { data: wd, error } = useFetch(`/api/world/${world}?seed=${seed}`);
  const handle = useRef();
  const [initial] = useState(() => {
    const p = settings.get('sql.pending');
    if (p) { settings.set('sql.pending', null); return p; }
    return settings.get('history.' + world, [])[0]?.sql || '';
  });
  const [hist, setHist] = useState(() => settings.get('history.' + world, []));
  if (error) return html`<div class="page"><${ErrorBox} error=${error} /></div>`;
  if (!wd) return html`<${Loading} />`;
  const ex = examples(world, wd.metrics);
  const start = initial || `SELECT table_name, estimated_size AS approx_rows\nFROM duckdb_tables()\nORDER BY approx_rows DESC;`;
  return html`<div class="sql-layout">
    <aside class="schema">
      <div class="section-label" style="padding:12px 12px 0">Schema · ${wd.tables.length} tables</div>
      <${Schema} tables=${wd.tables} onInsert=${(t) => handle.current?.insert(t)} />
    </aside>
    <div class="sql-main" style="overflow:auto">
      <div class="page wide" style="padding-top:18px">
        <div class="page-head" style="margin-bottom:12px">
          <div class="actions">
            <select onChange=${(e) => { const x = ex[+e.target.value]; if (x) handle.current?.set(x.sql, true); e.target.value = ''; }}>
              <option value="">Examples…</option>${ex.map((x, i) => html`<option value=${i}>${x.label}</option>`)}</select>
            <select onFocus=${() => setHist(settings.get('history.' + world, []))} onChange=${(e) => { const x = hist[+e.target.value]; if (x) handle.current?.set(x.sql, true); e.target.value = ''; }}>
              <option value="">History (${hist.length})…</option>${hist.map((x, i) => html`<option value=${i}>${fmtTime(x.ts)} · ${x.sql.replace(/\s+/g, ' ').slice(0, 70)}</option>`)}</select>
          </div>
        </div>
        <${SqlRunner} world=${world} seed=${seed} initialSql=${start} tables=${wd.tables} handle=${handle} />
      </div>
    </div>
  </div>`;
}
