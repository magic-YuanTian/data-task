// Shared UI components.
import { html, useState, useMemo, useEffect, useRef, fmtNum, isNumType, humanize } from './lib.js';
import { marked } from '../vendor/marked.js';

const ICONS = {
  home: 'M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z',
  book: 'M4 4.5A2.5 2.5 0 0 1 6.5 2H20v17H6.5A2.5 2.5 0 0 0 4 21.5zM4 21.5A2.5 2.5 0 0 1 6.5 19H20',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  db: 'M12 3c4.97 0 9 1.34 9 3s-4.03 3-9 3-9-1.34-9-3 4.03-3 9-3zM3 6v6c0 1.66 4.03 3 9 3s9-1.34 9-3V6M3 12v6c0 1.66 4.03 3 9 3s9-1.34 9-3v-6',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h6',
  code: 'm16 18 6-6-6-6M8 6l-6 6 6 6',
  chart: 'M3 3v18h18M7 15l4-4 3 3 6-6',
  check: 'M20 6 9 17l-5-5',
  flag: 'M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zM4 22v-7',
  target: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12zM12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  play: 'm6 4 14 8-14 8z',
  left: 'm15 18-6-6 6-6', right: 'm9 18 6-6-6-6',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
  external: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
  map: 'M1 6v16l7-4 8 4 7-4V2l-7 4-8-4zM8 2v16M16 6v16',
  copy: 'M20 9h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2zM5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1',
  layers: 'm12 2 10 5-10 5L2 7zM2 17l10 5 10-5M2 12l10 5 10-5',
};

export const Icon = ({ name, size }) => html`<svg class="icon" viewBox="0 0 24 24" width=${size || 16} height=${size || 16}
  fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d=${ICONS[name] || ''} /></svg>`;

export const LevelTag = ({ level, name }) => html`<span class=${'tag lv ' + level} title=${name || ''}>${level}${name ? ' ' + name : ''}</span>`;

export const TypeTag = ({ tt, name, short }) => {
  const lv = tt.split('.')[0];
  return html`<span class=${'tag lv ' + lv} title=${name}>${tt}${!short && name ? ' · ' + name : ''}</span>`;
};

const CTRL = {
  null: ['Null', 'Nothing to find: the correct answer is that there is no effect or event.'],
  decoy: ['Decoy', 'An obvious explanation is wrong; the answer requires ruling it out.'],
  ambiguous: ['Ambiguous', 'The question has several readings; the agent should ask or state its assumption.'],
  infeasible: ['Infeasible', 'The request cannot be met with the data or constraints; the agent should say so.'],
  params_given: ['Params given', 'Twin of another item with the parameters supplied, to separate setup from solving.'],
};
export const controlInfo = (c) => CTRL[c] || [c, ''];
export const ControlTag = ({ control }) => control ?
  html`<span class=${'tag ctrl ' + control} title=${controlInfo(control)[1]}>${controlInfo(control)[0]}</span>` : null;

export const STATUS_LABEL = { unreviewed: 'Unreviewed', approved: 'Approved', needs_fix: 'Needs fix', rejected: 'Rejected' };
export const StatusDot = ({ status }) => html`<span class="status"><span class=${'dot ' + (status || 'unreviewed')}></span>${STATUS_LABEL[status || 'unreviewed']}</span>`;

export const CheckTag = ({ passed, total }) => {
  if (!total) return html`<span class="faint small">–</span>`;
  const cls = passed === total ? 'ok' : 'bad';
  return html`<span class=${'tag ' + cls} title=${`${passed} of ${total} identifiability checks pass`}>${passed === total ? '✓' : '✕'} ${passed}/${total}</span>`;
};

export const Loading = ({ label }) => html`<div class="loading"><span class="spinner"></span>${label || 'Loading…'}</div>`;
export const ErrorBox = ({ error }) => error ? html`<div class="error">${error.message || String(error)}</div>` : null;
export const Empty = ({ children }) => html`<div class="empty">${children}</div>`;

export function Card({ title, sub, actions, children, flush, class: cls, id }) {
  return html`<section class=${'card ' + (cls || '')} id=${id}>
    ${(title || actions) && html`<div class="card-head">${title && html`<h3>${title}</h3>`}${sub && html`<span class="sub">${sub}</span>`}
      ${actions && html`<div class="actions">${actions}</div>`}</div>`}
    <div class=${'card-body' + (flush ? ' flush' : '')}>${children}</div></section>`;
}

export function Tabs({ tabs, active, onChange }) {
  return html`<div class="tabs" role="tablist">${tabs.filter(Boolean).map((t) => html`
    <button role="tab" class=${t.id === active ? 'on' : ''} onClick=${() => onChange(t.id)}>${t.label}
      ${t.count !== undefined && t.count !== null && html`<span class="count">${t.count}</span>`}</button>`)}</div>`;
}

export function Seg({ options, value, onChange }) {
  return html`<div class="seg">${options.map((o) => html`<button class=${o.value === value ? 'on' : ''}
    onClick=${() => onChange(o.value)} title=${o.title || ''}>${o.label}</button>`)}</div>`;
}

marked.setOptions({ gfm: true, breaks: false });
export const Markdown = ({ text, class: cls }) =>
  html`<div class=${'md ' + (cls || '')} dangerouslySetInnerHTML=${{ __html: marked.parse(text || '') }}></div>`;

// ---------- JSON tree ----------
function JNode({ k, v, depth, open }) {
  const [isOpen, setOpen] = useState(open);
  const key = k !== undefined ? html`<span class="k">${k}: </span>` : null;
  if (v === null || v === undefined) return html`<li>${key}<span class="z">null</span></li>`;
  if (typeof v === 'string') return html`<li>${key}<span class="s">"${v}"</span></li>`;
  if (typeof v === 'number') return html`<li>${key}<span class="n">${fmtNum(v)}</span></li>`;
  if (typeof v === 'boolean') return html`<li>${key}<span class="b">${String(v)}</span></li>`;
  const arr = Array.isArray(v);
  const entries = arr ? v.map((x, i) => [i, x]) : Object.entries(v);
  const brace = arr ? ['[', ']'] : ['{', '}'];
  if (!entries.length) return html`<li>${key}<span class="z">${brace.join('')}</span></li>`;
  const shown = entries.slice(0, 200);
  return html`<li>${key}<span class="toggle" onClick=${() => setOpen(!isOpen)}>${isOpen ? '▾' : '▸'} ${brace[0]}${!isOpen ? `… ${entries.length} ${arr ? 'items' : 'keys'} ${brace[1]}` : ''}</span>
    ${isOpen && html`<ul>${shown.map(([kk, vv]) => html`<${JNode} k=${kk} v=${vv} depth=${depth + 1} open=${depth < 0} />`)}
      ${entries.length > 200 && html`<li class="z">… ${entries.length - 200} more</li>`}</ul><span class="toggle">${brace[1]}</span>`}</li>`;
}
export const JsonView = ({ value, open = true }) => html`<div class="json"><ul style="padding-left:0">
  <${JNode} v=${value} depth=${0} open=${open} /></ul></div>`;

// ---------- data table ----------
function cellText(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === 'object') return JSON.stringify(v);
  if (typeof v === 'number') return fmtNum(v);
  return String(v);
}

export function DataTable({ columns, types = [], rows, pageSize = 100, maxHeight, rowNumbers = true, compact, onRowClick }) {
  const [sort, setSort] = useState(null);
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [rows]);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const { i, dir } = sort;
    return [...rows].sort((a, b) => {
      const x = a[i], y = b[i];
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * dir;
    });
  }, [rows, sort]);
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const view = sorted.slice(page * pageSize, (page + 1) * pageSize);
  const num = columns.map((c, i) => isNumType(types[i]) || (!types[i] && rows.length && rows.every((r) => r[i] === null || typeof r[i] === 'number')));
  const clickSort = (i) => setSort((s) => (!s || s.i !== i ? { i, dir: 1 } : s.dir === 1 ? { i, dir: -1 } : null));
  return html`<div>
    <div class="tbl-wrap" style=${maxHeight ? `max-height:${maxHeight}px` : ''}>
      <table class=${'tbl data-grid' + (compact ? ' compact' : '')}>
        <thead><tr>${rowNumbers && html`<th class="rownum"></th>`}${columns.map((c, i) => html`
          <th class=${'sortable' + (num[i] ? ' r' : '')} onClick=${() => clickSort(i)} title=${types[i] || ''}>
            ${c}${sort && sort.i === i ? (sort.dir === 1 ? ' ↑' : ' ↓') : ''}</th>`)}</tr></thead>
        <tbody>${view.map((r, j) => html`<tr class=${onRowClick ? 'click' : ''} onClick=${onRowClick ? () => onRowClick(r) : null}>
          ${rowNumbers && html`<td class="rownum">${page * pageSize + j + 1}</td>`}
          ${r.map((v, i) => { const t = cellText(v); return html`<td class=${num[i] ? 'r' : ''} title=${t && t.length > 40 ? t.slice(0, 1000) : ''}>
            ${t === null ? html`<span class="null">NULL</span>` : t}</td>`; })}</tr>`)}</tbody>
      </table>
    </div>
    ${pages > 1 && html`<div class="pager">
      <button class="btn sm" disabled=${page === 0} onClick=${() => setPage(page - 1)}>Previous</button>
      <span>Rows ${page * pageSize + 1}–${Math.min(sorted.length, (page + 1) * pageSize)} of ${fmtNum(sorted.length)}</span>
      <button class="btn sm" disabled=${page >= pages - 1} onClick=${() => setPage(page + 1)}>Next</button></div>`}
  </div>`;
}

// Records (list of objects) to a table.
export function RecordsTable({ records, maxRows = 12, maxHeight = 360 }) {
  const [all, setAll] = useState(false);
  const cols = useMemo(() => {
    const s = [];
    for (const r of records) for (const k of Object.keys(r)) if (!s.includes(k)) s.push(k);
    return s;
  }, [records]);
  const rows = (all ? records : records.slice(0, maxRows)).map((r) => cols.map((c) => r[c] ?? null));
  return html`<div class="card" style="box-shadow:none">
    <${DataTable} columns=${cols} rows=${rows} pageSize=${200} maxHeight=${maxHeight} rowNumbers=${records.length > 5} compact />
    ${records.length > maxRows && html`<div class="pager"><span>${all ? records.length : maxRows} of ${records.length} rows</span>
      <button class="btn sm ghost" onClick=${() => setAll(!all)}>${all ? 'Show fewer' : 'Show all'}</button></div>`}</div>`;
}

export function Toast({ text, onDone }) {
  useEffect(() => { if (!text) return; const t = setTimeout(onDone, 2200); return () => clearTimeout(t); }, [text]);
  return text ? html`<div class="toast">${text}</div>` : null;
}

export function CopyButton({ text, label = 'Copy' }) {
  const [done, setDone] = useState(false);
  return html`<button class="btn sm ghost" onClick=${() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1200); }}>
    <${Icon} name="copy" />${done ? 'Copied' : label}</button>`;
}

export { humanize };
