import { html, useState, useMemo, useEffect, useApp, useFetch, api, href, navigate, fmtNum, fmtCompact, settings, isNumType } from '../lib.js';
import { Loading, ErrorBox, Icon, Card, Tabs, Markdown, DataTable } from '../ui.js';
import { Figure, buildPlot, buildMap, toObjects } from '../charts.js';
import { SqlPage } from './sqlpage.js';

// tiny inline distribution
function Spark({ points, w = 120, h = 26 }) {
  if (!points?.length) return null;
  const max = Math.max(...points.map((p) => p[1])) || 1;
  const bw = w / points.length;
  return html`<svg class="spark" width=${w} height=${h} viewBox=${`0 0 ${w} ${h}`}>
    ${points.map((p, i) => { const bh = Math.max(1, (p[1] / max) * (h - 2)); return html`<rect x=${i * bw + 0.5} y=${h - bh} width=${Math.max(1, bw - 1)} height=${bh} fill="var(--l5)" />`; })}</svg>`;
}

function Distribution({ p }) {
  if (!p) return null;
  if (p.hist) return html`<div title=${`${fmtNum(p.hist.lo)} to ${fmtNum(p.hist.hi)} (1st to 99th percentile)`}><${Spark} points=${p.hist.bins} />
    <div class="faint" style="font-size:10.5px;display:flex;justify-content:space-between;width:120px"><span>${fmtNum(p.hist.lo)}</span><span>${fmtNum(p.hist.hi)}</span></div></div>`;
  if (p.series) return html`<div title=${`count by ${p.series.unit}`}><${Spark} points=${p.series.points} />
    <div class="faint" style="font-size:10.5px;display:flex;justify-content:space-between;width:120px"><span>${String(p.series.points[0]?.[0] || '').slice(0, 7)}</span><span>${String(p.series.points.at(-1)?.[0] || '').slice(0, 7)}</span></div></div>`;
  if (p.top) {
    const tot = p.top.total || 1;
    return html`<div class="topvals">${p.top.values.slice(0, 4).map(([v, n]) => html`<div><span class="bar" style=${`width:${Math.max(2, (n / tot) * 60)}px`}></span>
      <span class="val" title=${v}>${v === null ? 'NULL' : v}</span><span class="cnt">${((n / tot) * 100).toFixed(n / tot < 0.01 ? 2 : 0)}%</span></div>`)}
      ${p.distinct > 4 && html`<div class="faint">… ${fmtCompact(p.distinct)} distinct${p.top.sampled ? ', sampled' : ''}</div>`}</div>`;
  }
  if (p.long_text) return html`<span class="faint small">long text</span>`;
  return null;
}

function TableDetail({ world, seed, table }) {
  const app = useApp();
  const [tab, setTab] = useState('columns');
  const prof = useFetch(`/api/world/${world}/profile/${table.name}?seed=${seed}`);
  const [preview, setPreview] = useState(null);
  const loadPreview = () => api('/api/sql', { method: 'POST', body: { world, seed, sql: `SELECT * FROM "${table.name}" LIMIT 100` } })
    .then(setPreview).catch((e) => setPreview({ error: e }));
  const byName = Object.fromEntries((prof.data?.columns || []).map((c) => [c.name, c]));
  const undocumented = table.columns.filter((c) => !c.description).length;
  const openSql = () => { settings.set('sql.pending', `SELECT *\nFROM ${table.name}\nLIMIT 100;`); navigate(['data', world], { tab: 'query' }); };
  return html`<div class="card">
    <div class="card-head"><h3 class="mono" style="font-size:14px">${table.name}</h3><span class="sub">${fmtNum(table.rows)} rows · ${table.columns.length} columns</span>
      <div class="actions"><button class="btn sm" onClick=${openSql}><${Icon} name="code" />Query</button></div></div>
    ${table.description && html`<div class="card-body" style="border-bottom:1px solid var(--border)"><span class="muted">${table.description}</span></div>`}
    <${Tabs} tabs=${[{ id: 'columns', label: 'Columns', count: table.columns.length }, { id: 'preview', label: 'Preview' }]} active=${tab}
      onChange=${(t) => { setTab(t); if (t === 'preview' && !preview) loadPreview(); }} />
    ${tab === 'columns' ? html`<div class="tbl-wrap">
      ${prof.loading && html`<div class="loading small"><span class="spinner"></span>Profiling columns (the first time can take a minute on large tables)…</div>`}
      <${ErrorBox} error=${prof.error} />
      <table class="tbl">
        <thead><tr><th>Column</th><th>Type</th><th>Description</th><th class="r">Null</th><th class="r" title="approximate count of distinct values">Distinct ≈</th><th>Range</th><th>Distribution</th></tr></thead>
        <tbody>${table.columns.map((c) => { const p = byName[c.name]; return html`<tr>
          <td class="mono" style="white-space:nowrap">${c.name}</td><td class="mono faint" style="font-size:11.5px">${c.type}</td>
          <td style="min-width:220px;max-width:420px">${c.description ? html`<span class="small">${c.description}</span>` : html`<span class="tag warn" title="The data dictionary does not describe this column">not documented</span>`}</td>
          <td class="r small">${p ? html`${p.null_pct > 0 ? html`<span class=${p.null_pct > 20 ? 'tag warn' : ''}>${fmtNum(p.null_pct, { digits: p.null_pct < 1 ? 2 : 1 })}%</span>` : html`<span class="faint">0</span>`}` : ''}</td>
          <td class="r small">${p ? fmtCompact(p.distinct) : ''}</td>
          <td class="small mono" style="white-space:nowrap;font-size:11.5px">${p && p.min !== null && !p.long_text ? html`${String(p.min).slice(0, 19)}<br /><span class="faint">${String(p.max).slice(0, 19)}</span>` : ''}</td>
          <td><${Distribution} p=${p} /></td></tr>`; })}</tbody>
      </table></div>
      ${undocumented > 0 && html`<div class="pager">${undocumented} column${undocumented > 1 ? 's are' : ' is'} not described in the data dictionary. Some gaps are deliberate: questions at Q1 ask what such fields mean.</div>`}`
      : !preview ? html`<${Loading} />` : preview.error ? html`<div class="card-body"><${ErrorBox} error=${preview.error} /></div>`
        : html`<${DataTable} columns=${preview.columns} types=${preview.types} rows=${preview.rows} pageSize=${100} maxHeight=${560} />`}
  </div>`;
}

export function ViewCard({ world, seed, view, height }) {
  const { data, error, loading } = useFetch(`/api/world/${world}/view/${view.id}?seed=${seed}`);
  const objs = useMemo(() => (data ? toObjects(data.columns, data.types, data.rows) : []), [data]);
  const openSql = () => { settings.set('sql.pending', view.sql.trim()); navigate(['data', world], { tab: 'query' }); };
  return html`<div class="card view-card">
    <div class="card-head"><h3>${view.title}</h3>${view.unit && html`<span class="sub">${view.unit}</span>`}
      <div class="actions">${view.sql && html`<button class="btn sm ghost" onClick=${openSql} title="Open the query"><${Icon} name="code" /></button>`}</div></div>
    <div class="card-body">
      ${loading && html`<${Loading} label="Computing…" />`}<${ErrorBox} error=${error} />
      ${data && html`<${Figure} build=${(w) => buildPlot({ height: height || 230, ...view.chart }, objs, w)} deps=${[objs]} />`}
      ${view.caption && html`<p class="caption">${view.caption}</p>`}
    </div></div>`;
}

function MapCard({ world, seed, view }) {
  const layer = view.layers?.[0];
  const geo = useFetch(layer ? `/api/world/${world}/geo/${layer}?seed=${seed}` : null);
  const pts = useFetch(`/api/world/${world}/view/${view.id}?seed=${seed}`);
  const points = useMemo(() => (pts.data ? toObjects(pts.data.columns, pts.data.types, pts.data.rows) : null), [pts.data]);
  const [colorBy, setColorBy] = useState(view.chart?.color || null);
  const ready = points && (!layer || geo.data);
  const colorOpts = view.chart?.color_options || (view.chart?.color ? [view.chart.color] : []);
  return html`<div class="card view-card">
    <div class="card-head"><h3>${view.title}</h3>
      <div class="actions">${colorOpts.length > 1 && html`<label class="inline">Color <select value=${colorBy} onChange=${(e) => setColorBy(e.target.value)}>
        ${colorOpts.map((c) => html`<option value=${c}>${c}</option>`)}</select></label>`}</div></div>
    <div class="card-body">
      ${!ready && html`<${Loading} label="Loading map…" />`}<${ErrorBox} error=${geo.error || pts.error} />
      ${ready && html`<${Figure} build=${(w) => buildMap({ geo: geo.data, points, ...view.chart, color: colorBy, height: view.chart?.height || 560 }, w)} deps=${[points, geo.data, colorBy]} />`}
      ${view.caption && html`<p class="caption">${view.caption}</p>`}
    </div></div>`;
}

function Catalog({ world, text, metrics }) {
  return html`<div class="grid" style="grid-template-columns:minmax(0,1.2fr) minmax(0,1fr)">
    <${Card} title="Data dictionary" sub="catalog/data_dictionary.md">${text === null ? html`<${Loading} />` : html`<${Markdown} text=${text} />`}</${Card}>
    <${Card} title="Certified metrics" sub="catalog/semantic_layer.yaml" flush>
      ${Object.keys(metrics || {}).length === 0 ? html`<div class="empty">No metrics.</div>` : html`<table class="tbl">
        <tbody>${Object.entries(metrics).map(([k, m]) => html`<tr><td>
          <div class="mono" style="font-weight:600">${k}</div><div class="small muted" style="margin:2px 0 6px">${m.description}</div>
          <details class="fold"><summary>SQL</summary><div class="fold-body"><pre>${(m.sql || '').trim()}</pre>
            <button class="btn sm" style="margin-top:6px" onClick=${() => { settings.set('sql.pending', m.sql.trim()); navigate(['data', world], { tab: 'query' }); }}><${Icon} name="code" />Open in SQL</button></div></details>
        </td></tr>`)}</tbody></table>`}
    </${Card}>
  </div>`;
}

// Where every table comes from, the publishers' documents and any documents in the workspace.
function Sources({ world, seed, wd }) {
  const src = Object.entries(wd.sources?.sources || {});
  const docs = Object.values(wd.sources?.documents || {});
  return html`<div class="stack">
    ${src.length > 0 && html`<${Card} title="Where every table comes from" flush>
      <table class="data"><thead><tr><th>Table</th><th>Source</th><th>Also used by</th><th>Changes</th></tr></thead><tbody>
      ${src.map(([t, s]) => html`<tr><td class="mono">${t}</td>
        <td>${s.url ? html`<a href=${s.url} target="_blank" rel="noopener">${s.source}</a>` : s.source}</td>
        <td>${s.benchmark || html`<span class="faint">–</span>`}</td><td>${s.modified || html`<span class="faint">none</span>`}</td></tr>`)}
      </tbody></table></${Card}>`}
    ${docs.map((d) => html`<${Card} title=${d.document} actions=${html`<a class="btn sm ghost" href=${d.url} target="_blank" rel="noopener"><${Icon} name="external" />Open</a>`}>
      <ul class="quotes">${d.text.map((x) => html`<li>${x}</li>`)}</ul></${Card}>`)}
    ${wd.docs?.length > 0 && html`<${Card} title="Documents in the workspace" sub="written for this environment">
      <ul class="plain">${wd.docs.map((d) => html`<li><a href=${`/files/${world}/${seed}/docs/${encodeURIComponent(d.file || d)}`} target="_blank">${d.file || d}</a></li>`)}</ul></${Card}>`}
    ${!src.length && !docs.length && !wd.docs?.length && html`<div class="faint">No source documents recorded for this environment.</div>`}
  </div>`;
}

export function DataPage({ world, query }) {
  const app = useApp();
  const seed = app.seed(world);
  const { data: wd, error } = useFetch(`/api/world/${world}?seed=${seed}`);
  const tab = query.tab || 'charts';
  const setTab = (t) => navigate(['data', world], { ...query, tab: t }, { replace: true });
  if (error) return html`<div class="page"><${ErrorBox} error=${error} /></div>`;
  if (!wd) return html`<${Loading} />`;
  const tables = wd.tables;
  const table = tables.find((t) => t.name === query.table) || tables.slice().sort((a, b) => b.rows - a.rows)[0];
  const views = (wd.views || []).filter((v) => v.type !== 'map');
  const maps = (wd.views || []).filter((v) => v.type === 'map');
  const metricViews = Object.entries(wd.metrics || {}).map(([k, m]) => ({ id: 'metric:' + k, title: k, caption: m.description, sql: m.sql,
    chart: { mark: 'line', x: 'month', y: 'value', yLabel: null, xLabel: null } }));
  return html`<div class="page wide">
    <div class="page-head">
      <div class="env-overview">
        <div class="row" style="gap:10px;align-items:baseline"><h1>${wd.title}</h1><span class="faint">${wd.domain}</span></div>
        <p class="lead">${wd.summary}</p>
        <div class="env-stats">
          <div><b>${wd.n_tables}</b> tables</div><div><b>${fmtCompact(wd.n_rows)}</b> rows</div>
          ${wd.period && html`<div><b>${wd.period}</b></div>`}<div><b>${wd.n_tasks}</b> tasks</div>
        </div>
      </div>
    </div>
    <div class="card" style="margin-bottom:16px"><${Tabs} active=${tab} onChange=${setTab} tabs=${[
      { id: 'charts', label: 'Charts', count: views.length + metricViews.length },
      maps.length && { id: 'map', label: 'Map' },
      { id: 'tables', label: 'Tables', count: tables.length },
      { id: 'documents', label: 'Sources and documents' },
      { id: 'catalog', label: 'Data dictionary' },
      { id: 'query', label: 'Query' }]} /></div>
    ${tab === 'charts' && html`<div>
      ${views.length > 0 && html`<div class="section-label">Overview charts</div><div class="grid c2" style="margin-bottom:22px">
        ${views.map((v) => html`<${ViewCard} world=${world} seed=${seed} view=${v} />`)}</div>`}
      <div class="section-label">Certified metrics, by month</div>
      <div class="grid c3">${metricViews.map((v) => html`<${ViewCard} world=${world} seed=${seed} view=${v} height=${170} />`)}</div>
    </div>`}
    ${tab === 'map' && html`<div class="stack">${maps.map((v) => html`<${MapCard} world=${world} seed=${seed} view=${v} />`)}</div>`}
    ${tab === 'tables' && html`<div class="split">
      <div class="list">${tables.map((t) => html`<a class=${t.name === table.name ? 'on' : ''} href=${href(['data', world], { tab: 'tables', table: t.name })}>
        <span class="mono" style="font-size:12.5px">${t.name}</span><span class="n">${fmtCompact(t.rows)}</span></a>`)}</div>
      <${TableDetail} key=${table.name} world=${world} seed=${seed} table=${table} />
    </div>`}
    ${tab === 'catalog' && html`<${Catalog} world=${world} text=${wd.dictionary_md} metrics=${wd.metrics} />`}
    ${tab === 'documents' && html`<${Sources} world=${world} seed=${seed} wd=${wd} />`}
    ${tab === 'query' && html`<div class="embedded-sql"><${SqlPage} world=${world} query=${query} /></div>`}
  </div>`;
}
