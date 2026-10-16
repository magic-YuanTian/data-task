// Tasks: the list (filter by level, dependency graph) and one task with its ground-truth reasoning path.
import { html, useState, useMemo, useRef, useApp, useFetch, api, href, navigate } from '../lib.js';
import { Loading, ErrorBox, Icon, LevelTag, TypeTag, ControlTag, Markdown, DataTable } from '../ui.js';
import { GoldView, ScoringView, Value } from '../gold.js';
import { SqlEditor } from '../sql.js';

const SOURCE = {
  db: ['Query', 'db'], document: ['Source document', 'doc'], 'sub-task': ['Earlier task', 'sub'], benchmark: ['Spider 2.0', 'bench'],
  'hold-out': ['Hold-out records', 'gold'], calculation: ['Calculation', 'calc'], 'reference method': ['Reference method', 'calc'],
};
const short = (id) => id.split('.').slice(1).join('.').replace(/_/g, ' ');

function useTasks(world, seed) {
  const r = useFetch(`/api/tasks/${world}?seed=${seed}`);
  const byId = useMemo(() => Object.fromEntries((r.data || []).map((t) => [t.id, t])), [r.data]);
  return { ...r, byId };
}

// ---------------------------------------------------------------- dependency graph
function Graph({ tasks, levels, world, focus, level }) {
  const [hover, setHover] = useState(null);
  const W = 1180, colW = W / levels.length, nodeW = colW - 34, nodeH = 30, gap = 10, top = 34;
  // order each column by the mean row of the tasks it builds on (fewer crossings); tasks without one go last
  const row = {};
  const cols = levels.map((lv) => {
    const c = tasks.filter((t) => t.level === lv);
    const key = (t) => { const r = t.depends_on.filter((d) => d in row).map((d) => row[d]); return r.length ? r.reduce((x, y) => x + y) / r.length : 1e9; };
    const sorted = c.map((t, i) => [key(t), i, t]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((x) => x[2]);
    sorted.forEach((t, j) => { row[t.id] = j; });
    return sorted;
  });
  const H = top + Math.max(...cols.map((c) => c.length)) * (nodeH + gap) + 8;
  const pos = {};
  cols.forEach((c, i) => c.forEach((t, j) => { pos[t.id] = { x: i * colW + 17, y: top + j * (nodeH + gap) }; }));
  const byId = Object.fromEntries(tasks.map((t) => [t.id, t]));
  const lit = new Set();
  const key = hover || focus;
  if (key) {
    const up = (id) => { if (lit.has(id)) return; lit.add(id); (byId[id]?.depends_on || []).forEach(up); };
    const down = (id) => { lit.add(id); (byId[id]?.used_by || []).forEach(down); };
    up(key); down(key);
  }
  const on = (t) => (key ? lit.has(t.id) : !level || t.level === level);
  return html`<div class="dep-graph"><svg viewBox=${`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Task dependencies">
    ${levels.map((lv, i) => html`<text x=${i * colW + 17} y="18" class="g-col">${lv}</text>`)}
    ${tasks.flatMap((t) => t.depends_on.filter((d) => pos[d]).map((d) => {
      const a = pos[d], b = pos[t.id];
      const x1 = a.x + nodeW, y1 = a.y + nodeH / 2, x2 = b.x, y2 = b.y + nodeH / 2, mx = (x1 + x2) / 2;
      const hot = key ? lit.has(d) && lit.has(t.id) : on(t) || on(byId[d]);
      return html`<path d=${`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} class=${'g-edge' + (hot ? ' hot' : '')} />`;
    }))}
    ${tasks.map((t) => { const p = pos[t.id]; return html`<a href=${href(['task', world, t.id])}
        onMouseEnter=${() => setHover(t.id)} onMouseLeave=${() => setHover(null)}>
      <g class=${'g-node ' + t.level + (on(t) ? '' : ' dim') + (t.id === focus ? ' focus' : '')}>
        <rect x=${p.x} y=${p.y} width=${nodeW} height=${nodeH} rx="5" />
        <text x=${p.x + 9} y=${p.y + 19}>${short(t.id).slice(0, 24)}</text></g></a>`; })}
  </svg></div>`;
}

// ---------------------------------------------------------------- list
export function TasksPage({ world, query }) {
  const app = useApp();
  const { data, error } = useTasks(world, app.seed(world));
  if (error) return html`<div class="page"><${ErrorBox} error=${error} /></div>`;
  if (!data) return html`<${Loading} />`;
  const levels = app.meta.level_order;
  const level = query.level || '';
  const shown = data.filter((t) => (!level || t.level === level) && (!query.type || t.task_type === query.type));
  const set = (lv) => navigate(['tasks', world], lv ? { level: lv } : {}, { replace: true });
  return html`<div class="page wide tasks-page">
    <div class="page-head"><div><h1>Tasks</h1></div></div>
    <div class="card" style="margin-bottom:18px"><div class="card-body"><${Graph} tasks=${data} levels=${levels} world=${world} level=${level} /></div></div>
    <div class="row" style="margin-bottom:12px">
      <div class="seg">
        <button class=${!level ? 'on' : ''} onClick=${() => set('')}>All levels <span class="faint">${data.length}</span></button>
        ${levels.map((lv) => html`<button class=${level === lv ? 'on' : ''} onClick=${() => set(lv)}
          title=${app.meta.levels[lv]}>${lv} ${app.meta.levels[lv]} <span class="faint">${data.filter((t) => t.level === lv).length}</span></button>`)}
      </div>
      ${query.type && html`<span class="tag">${query.type} ${app.meta.task_types[query.type]} <a href=${href(['tasks', world], level ? { level } : {})}>×</a></span>`}
    </div>
    <div class="sc-list">${shown.map((t) => html`<a class="task-row" href=${href(['task', world, t.id])}>
      <${LevelTag} level=${t.level} />
      <span class="t"><span class="tt">${t.task_type} ${t.task_type_name}${t.control ? ' · control' : ''}</span>
        <span class="q">${t.question}</span></span>
      <span class="deps">${t.depends_on.length ? html`<span title=${t.depends_on.join(', ')}>builds on ${t.depends_on.length}</span>` : ''}</span>
      <span>${t.planted && html`<span class="prov planted">synthetic</span>`}</span></a>`)}</div>
  </div>`;
}

// ---------------------------------------------------------------- one task
function Result({ r }) {
  if (r && Array.isArray(r.columns) && Array.isArray(r.rows)) {
    return html`<div class="sc-result"><${DataTable} columns=${r.columns} rows=${r.rows} compact pageSize=${12} rowNumbers=${false} />
      ${r.note && html`<div class="faint small" style="margin-top:4px">${r.note}</div>`}</div>`;
  }
  return html`<div class="sc-result kvbox"><${Value} v=${r} depth=${1} /></div>`;
}

// The step's query, editable in place; the run button replaces the recorded result with a live one.
function StepQuery({ s, world, seed }) {
  const app = useApp();
  const handle = useRef();
  const [state, setState] = useState({});
  const run = async () => {
    const sql = (handle.current?.get() || s.sql).trim();
    setState({ running: true, result: state.result });
    try {
      const result = await api('/api/sql', { method: 'POST', body: { world, sql, limit: 5000 } });
      setState({ result });
    } catch (error) { setState({ error }); }
  };
  return html`<div class="step-query">
    <div class="sq-editor">
      <${SqlEditor} value=${s.sql} onRun=${run} minLines=${1} handle=${handle} />
      <button class="sq-run" onClick=${run} disabled=${state.running} title="Run">
        ${state.running ? html`<span class="spinner"></span>` : html`<${Icon} name="play" />`}</button>
    </div>
    <${ErrorBox} error=${state.error} />
    ${state.result ? html`<${Result} r=${state.result} />` : s.result !== undefined && html`<${Result} r=${s.result} />`}
  </div>`;
}

function Step({ s, i, world, seed, byId }) {
  const [label, cls] = SOURCE[s.source] || [s.source, 'calc'];
  const dep = s.uses && byId[s.uses];
  return html`<li class="sc-step">
    <div class="sc-num">${i + 1}</div>
    <div class="sc-body">
      <div class="sc-title">${s.title} <span class=${'src ' + cls}>${label}</span></div>
      ${dep && html`<a class="uses" href=${href(['task', world, dep.id])}>
        <${LevelTag} level=${dep.level} /><span><b>${short(dep.id)}</b> · ${dep.target_response.slice(0, 180)}${dep.target_response.length > 180 ? '…' : ''}</span></a>`}
      ${s.doc && html`<blockquote class="quote">${s.doc.text}
        <div class="cite"><a href=${s.doc.url} target="_blank" rel="noopener">${s.doc.document}</a></div></blockquote>`}
      ${s.sql ? html`<${StepQuery} s=${s} world=${world} seed=${seed} />` : s.result !== undefined && html`<${Result} r=${s.result} />`}
      ${s.note && html`<div class="sc-note">${s.note}</div>`}
    </div></li>`;
}

export function TaskPage({ world, id }) {
  const app = useApp();
  const seed = app.seed(world);
  const { data, error, byId } = useTasks(world, seed);
  const list = data || [];
  const idx = list.findIndex((x) => x.id === id);
  const go = (k) => list[k] && navigate(['task', world, list[k].id]);
  if (error) return html`<div class="page"><${ErrorBox} error=${error} /></div>`;
  if (!data) return html`<${Loading} />`;
  const t = byId[id];
  if (!t) return html`<div class="page">Unknown task. <a href=${href(['tasks', world])}>All tasks</a></div>`;
  return html`<div class="page task-page" key=${t.id}>
    <div class="crumbs"><a href=${href(['tasks', world])}>Tasks</a> / <a href=${href(['tasks', world], { level: t.level })}>${t.level} ${t.level_name}</a> / <span class="mono">${t.id}</span></div>
    <div class="row" style="margin-bottom:10px">
      <${LevelTag} level=${t.level} name=${t.level_name} /><${TypeTag} tt=${t.task_type} name=${t.task_type_name} /><${ControlTag} control=${t.control} />
      ${t.planted && html`<span class="prov planted" title="Uses a planted event">synthetic</span>`}
      <span class="spacer"></span>
      <button class="btn sm ghost" disabled=${idx <= 0} onClick=${() => go(idx - 1)} title="Previous"><${Icon} name="left" /></button>
      <span class="faint small num">${idx + 1} / ${list.length}</span>
      <button class="btn sm ghost" disabled=${idx >= list.length - 1} onClick=${() => go(idx + 1)} title="Next"><${Icon} name="right" /></button>
    </div>
    <div class="task-q">
      <div class="section-label">Task</div>
      <h1>${t.question}</h1>
      <div class="faint small">Answer format <code>${t.answer_format?.type}</code></div>
    </div>

    <div class="deps-box">
      <div><div class="section-label">Builds on</div>${t.depends_on.length ? t.depends_on.map((d) => html`<a class="dep" href=${href(['task', world, d])}>
        <${LevelTag} level=${byId[d]?.level} /> ${short(d)}</a>`) : html`<span class="faint small">no earlier task</span>`}</div>
      <div><div class="section-label">Used by</div>${t.used_by.length ? t.used_by.map((d) => html`<a class="dep" href=${href(['task', world, d])}>
        <${LevelTag} level=${byId[d]?.level} /> ${short(d)}</a>`) : html`<span class="faint small">no later task</span>`}</div>
      <div><div class="section-label">Data sources</div>${t.provenance.map((p) => html`<div class="prov-line">
        ${p.table ? html`<code>${p.table}</code> ${p.url ? html`<a href=${p.url} target="_blank" rel="noopener">${p.source}</a>` : p.source}${p.benchmark ? html` <span class="faint">· also in ${p.benchmark}</span>` : ''}`
          : p.document ? html`<a href=${p.url} target="_blank" rel="noopener">${p.document}</a>` : html`Spider 2.0 <code>${p.instance}</code>`}</div>`)}</div>
    </div>

    <h2 class="sc-h">Ground-truth reasoning path</h2>
    <ol class="sc-steps">${t.steps.map((s, i) => html`<${Step} s=${s} i=${i} world=${world} seed=${seed} byId=${byId} />`)}</ol>

    <h2 class="sc-h">Answer</h2>
    <div class="target">
      <${Markdown} text=${t.target_response} />
      <details class="answer-fold"><summary>Gold fields and scoring</summary>
        <div class="stack" style="padding-top:12px">
          <${GoldView} gold=${t.gold} scoring=${t.scoring} answerFormat=${t.answer_format} />
          <div><div class="section-label">Scoring</div><${ScoringView} scoring=${t.scoring} /></div>
        </div></details>
    </div>
  </div>`;
}
