// Readable views of gold answers, evidence, sub-steps and checks. Gold records vary by item, so values are
// rendered by shape: facts as numbered lists, record lists as tables, numeric mappings as small charts.
import { html, useState, fmtNum, humanize } from './lib.js';
import { RecordsTable, JsonView, TypeTag, Icon, CopyButton, DataTable } from './ui.js';
import { SeriesChart } from './charts.js';

const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);
const isScalar = (v) => v === null || ['string', 'number', 'boolean'].includes(typeof v);
const size = (v) => JSON.stringify(v ?? null).length;
// short values shown inline in a key/value grid
const isInline = (v) => isScalar(v) || (Array.isArray(v) && v.length <= 8 && v.every((x) => typeof x === 'number' || (typeof x === 'string' && x.length < 40)));

function Scalar({ v }) {
  if (v === null || v === undefined) return html`<span class="faint">null</span>`;
  if (typeof v === 'boolean') return html`<span class=${'tag ' + (v ? 'ok' : 'bad')}>${v ? 'Yes' : 'No'}</span>`;
  if (typeof v === 'number') return html`<span class="num">${fmtNum(v)}</span>`;
  return html`<span>${v}</span>`;
}

function Fold({ label, children, open = false }) {
  return html`<details class="fold" open=${open}><summary>${label}</summary><div class="fold-body">${children}</div></details>`;
}

function describe(v) {
  if (Array.isArray(v)) return `${v.length} ${v.length === 1 ? 'entry' : 'entries'}`;
  if (isObj(v)) return `${Object.keys(v).length} keys`;
  return '';
}

// Smart value renderer.
export function Value({ v, k, onRunSql, depth = 0 }) {
  if (isScalar(v)) {
    if (k === 'sql' && typeof v === 'string') return html`<${SqlBlock} sql=${v} onRun=${onRunSql} />`;
    return html`<${Scalar} v=${v} />`;
  }
  const big = size(v) > 2400;
  let body;
  if (Array.isArray(v)) {
    if (!v.length) body = html`<span class="faint">none</span>`;
    else if (v.every((x) => typeof x === 'string')) {
      body = v.length <= 8 && v.every((x) => x.length < 60) ? html`<span>${v.join(', ')}</span>`
        : html`<ul style="margin:0;padding-left:18px">${v.slice(0, 200).map((x) => html`<li>${x}</li>`)}</ul>`;
      if (v.length > 30) return html`<${Fold} label=${describe(v)}>${body}</${Fold}>`;
    } else if (v.every((x) => typeof x === 'number' || x === null)) {
      body = html`<span class="num">${v.slice(0, 40).map(fmtNum).join(', ')}${v.length > 40 ? ` … (${v.length})` : ''}</span>`;
    } else if (v.every((x) => isObj(x))) {
      const flat = v.map((x) => Object.fromEntries(Object.entries(x).map(([a, b]) => [a, isScalar(b) ? b : JSON.stringify(b)])));
      body = html`<${RecordsTable} records=${flat} maxRows=${big ? 8 : 15} />`;
      if (big && depth > 0) return html`<${Fold} label=${describe(v)}>${body}</${Fold}>`;
    } else body = html`<${JsonView} value=${v} />`;
    return body;
  }
  const entries = Object.entries(v);
  if (!entries.length) return html`<span class="faint">none</span>`;
  const allNum = entries.every(([, x]) => typeof x === 'number');
  const statLike = entries.some(([a]) => STAT_KEYS.test(a));
  if (allNum && !statLike && entries.length >= 3 && entries.length <= 400) {
    const table = html`<${DataTable} columns=${['key', 'value']} rows=${entries} pageSize=${50} rowNumbers=${false} compact maxHeight=${260} />`;
    return html`<div class="series"><${SeriesChart} obj=${v} />
      <${Fold} label=${`values (${entries.length})`}>${table}</${Fold}></div>`;
  }
  const allRec = entries.length > 1 && entries.every(([, x]) => isObj(x) && Object.values(x).every(isScalar));
  if (allRec && entries.length <= 200) {
    const cols = [];
    for (const [, x] of entries) for (const c of Object.keys(x)) if (!cols.includes(c)) cols.push(c);
    const rows = entries.map(([a, x]) => [a, ...cols.map((c) => x[c] ?? null)]);
    return html`<div class="card" style="box-shadow:none"><${DataTable} columns=${['', ...cols]} rows=${rows} pageSize=${50} rowNumbers=${false} compact maxHeight=${300} /></div>`;
  }
  if (entries.length > 40) return html`<${Fold} label=${describe(v)}><${JsonView} value=${v} open=${false} /></${Fold}>`;
  return html`<div class="kv">${entries.map(([a, x]) => html`<div class="k">${humanize(a)}</div>
    <div class="v"><${Value} v=${x} k=${a} onRunSql=${onRunSql} depth=${depth + 1} /></div>`)}</div>`;
}

export function SqlBlock({ sql, onRun }) {
  return html`<div class="card" style="box-shadow:none">
    <div class="card-head" style="min-height:34px;padding:4px 8px 4px 12px"><span class="faint small">SQL</span>
      <div class="actions"><${CopyButton} text=${sql} />${onRun && html`<button class="btn sm" onClick=${() => onRun(sql)}><${Icon} name="play" />Run</button>`}</div></div>
    <pre style="border-radius:0 0 6px 6px;max-height:320px">${sql.trim()}</pre></div>`;
}

const LIST_KEYS = ['key_facts', 'rubric'];
// mappings of test statistics are shown as values, not as a chart
const STAT_KEYS = /^(p|p_value|tau|kendall_tau|sen_slope|slope|r2|rho|spearman|z|se|n|ci|mean|median|std|sd|statistic|ks_statistic|total|rate_total|mix_total|level|diff|ratio|peak|coef)$/;
const BULKY = /^(detector|instance|candidates|scan|daily_counts|true_probability|optimal_assignment|assignment|realized_\w+|oracle_\w+|positives|review_ids|merchant_ids|dispute_ids|complaint_ids|histogram|unchanged_rewordings|result)$/;

function FactList({ facts, required }) {
  const req = new Set(required || []);
  return html`<ol class="facts">${facts.map((f, i) => html`<li class=${req.has(i) ? 'req' : ''}>${typeof f === 'string' ? f : f.point || JSON.stringify(f)}
    ${req.has(i) && html`<span class="tag info">required</span>`}
    ${f && f.weight !== undefined && html`<span class="tag">weight ${f.weight}</span>`}</li>`)}</ol>`;
}

function tolText(f) {
  if (!f) return null;
  const parts = [];
  if (f.tol !== undefined) parts.push(`± ${fmtNum(f.tol)}`);
  if (f.rel_tol !== undefined) parts.push(`± ${fmtNum(f.rel_tol * 100)}%`);
  if (f.judge) parts.push(`judged by ${f.judge.toUpperCase()}`);
  if (f.exact) parts.push('exact');
  for (const [k, v] of Object.entries(f)) {
    if (['tol', 'rel_tol', 'judge', 'exact'].includes(k)) continue;
    if (Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === 'number')) parts.push(`${k} ${fmtNum(v[0])} to ${fmtNum(v[1])}`);
    else parts.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);
  }
  return parts.join(', ');
}

export function GoldView({ gold, scoring, answerFormat, onRunSql }) {
  if (!isObj(gold)) return html`<${Value} v=${gold} />`;
  const fields = (scoring && scoring.fields) || {};
  const schemaKeys = Object.keys(answerFormat?.schema || {});
  const keys = Object.keys(gold).filter((k) => k !== 'required');
  const primary = [], secondary = [], bulky = [];
  for (const k of keys) {
    if (LIST_KEYS.includes(k) || schemaKeys.includes(k) || fields[k]) primary.push(k);
    else if (BULKY.test(k) || size(gold[k]) > 6000) bulky.push(k);
    else secondary.push(k);
  }
  if (!primary.length && secondary.length) primary.push(...secondary.splice(0, secondary.length));
  const block = (k) => {
    const v = gold[k];
    if (k === 'key_facts') return html`<div><div class="section-label">Key facts</div><${FactList} facts=${v} required=${gold.required} /></div>`;
    if (k === 'rubric') return html`<div><div class="section-label">Rubric</div><${FactList} facts=${v} /></div>`;
    const tol = tolText(fields[k]);
    const wide = !isInline(v);
    return html`<div class=${wide ? 'gold-wide' : 'kv'}>
      ${wide ? html`<div class="section-label">${humanize(k)}${tol ? html` <span class="faint" style="text-transform:none;letter-spacing:0">(${tol})</span>` : ''}</div>
        <${Value} v=${v} k=${k} onRunSql=${onRunSql} depth=${1} />`
        : html`<div class="k">${humanize(k)}</div><div class="v"><${Value} v=${v} k=${k} />${tol && html`<span class="tol">${tol}</span>`}</div>`}</div>`;
  };
  // group consecutive scalar fields into one key/value grid
  const renderGroup = (ks) => {
    const out = [];
    let run = [];
    const flush = () => {
      if (run.length) out.push(html`<div class="kv">${run.map((k) => html`<div class="k">${humanize(k)}</div>
        <div class="v"><${Value} v=${gold[k]} k=${k} />${tolText(fields[k]) && html`<span class="tol">${tolText(fields[k])}</span>`}</div>`)}</div>`);
      run = [];
    };
    for (const k of ks) {
      if (isInline(gold[k]) && k !== 'sql') run.push(k);
      else { flush(); out.push(block(k)); }
    }
    flush();
    return out;
  };
  return html`<div class="stack">
    ${renderGroup(primary)}
    ${secondary.length > 0 && html`<div><div class="section-label" style="margin-top:4px">Also recorded</div><div class="stack">${renderGroup(secondary)}</div></div>`}
    ${bulky.length > 0 && html`<div class="stack" style="margin-top:8px">${bulky.map((k) => html`<${Fold} label=${`${humanize(k)} · ${describe(gold[k])}`}>
      <${Value} v=${gold[k]} k=${k} onRunSql=${onRunSql} depth=${0} /></${Fold}>`)}</div>`}
  </div>`;
}

export function ScoringView({ scoring }) {
  if (!scoring) return null;
  const { metric, judge, notes, fields, ...rest } = scoring;
  return html`<div class="kv">
    <div class="k">Metric</div><div class="v"><code>${metric}</code></div>
    ${judge && html`<div class="k">Judge</div><div class="v">${judge === 'llm' ? 'LLM judge' : judge}</div>`}
    ${fields && html`<div class="k">Fields</div><div class="v">${Array.isArray(fields)
      ? fields.map((k) => html`<code style="margin-right:6px">${k}</code>`)
      : Object.entries(fields).map(([k, f]) => html`<div><code>${k}</code> <span class="faint small">${tolText(f)}</span></div>`)}</div>`}
    ${Object.entries(rest).map(([k, v]) => html`<div class="k">${humanize(k)}</div><div class="v"><${Value} v=${v} /></div>`)}
    ${notes && html`<div class="k">Notes</div><div class="v">${notes}</div>`}
  </div>`;
}

export function SubSteps({ steps, taskTypes }) {
  return html`<div>${steps.map((s, i) => html`<div class="substep">
    <div class="row" style="margin-bottom:6px"><span class="faint small">Step ${i + 1}</span>
      ${s.task_type && html`<${TypeTag} tt=${s.task_type} name=${taskTypes[s.task_type]} />`}</div>
    <div style="margin-bottom:6px">${s.question}</div>
    ${s.gold !== undefined && html`<div class="small"><${Value} v=${s.gold} depth=${1} /></div>`}</div>`)}</div>`;
}

export function ChecksView({ checks, bySeed }) {
  if (!checks?.length) return html`<div class="faint small">No identifiability check is attached to this item.</div>`;
  const seeds = bySeed ? Object.entries(bySeed) : [];
  return html`<div>
    ${checks.map((c) => html`<div class="check-row" style="align-items:flex-start">
      <span class=${'tag ' + (c.passed ? 'ok' : 'bad')}>${c.passed ? 'pass' : 'FAIL'}</span>
      <div style="flex:1;min-width:0"><div>${c.check}</div>
        <div class="small muted" style="margin-top:4px"><${Value} v=${c.detail} depth=${2} /></div></div></div>`)}
    ${seeds.length > 0 && html`<div class="small muted" style="margin-top:10px">Other seeds:
      ${seeds.map(([s, cs]) => html` <span class=${'tag ' + (cs.every((c) => c.passed) ? 'ok' : 'bad')}>seed ${s} ${cs.filter((c) => c.passed).length}/${cs.length}</span>`)}</div>`}
  </div>`;
}

export function EvidenceView({ evidence, onRunSql }) {
  if (!evidence || !Object.keys(evidence).length) return html`<div class="faint small">No evidence recorded.</div>`;
  return html`<${Value} v=${evidence} onRunSql=${onRunSql} depth=${0} />`;
}
