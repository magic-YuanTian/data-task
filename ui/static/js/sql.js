// SQL editor (CodeMirror 5) and a runner with result table and chart.
import { html, useState, useEffect, useRef, api, fmtNum, toCSV, download, settings, useApp } from './lib.js';
import { DataTable, ErrorBox, Icon, Tabs } from './ui.js';
import { ResultChart } from './charts.js';

const CM = window.CodeMirror;

export function SqlEditor({ value, onChange, onRun, tables, minLines = 6, handle }) {
  const ref = useRef();
  const cm = useRef();
  const runRef = useRef(onRun);
  runRef.current = onRun;
  useEffect(() => {
    const ed = CM(ref.current, {
      value: value || '', mode: 'text/x-pgsql', lineNumbers: true, matchBrackets: true, indentUnit: 2, tabSize: 2,
      lineWrapping: false, viewportMargin: Infinity,
      extraKeys: {
        'Ctrl-Enter': () => runRef.current?.(ed.getValue()), 'Cmd-Enter': () => runRef.current?.(ed.getValue()),
        'Shift-Enter': () => runRef.current?.(ed.getValue()), 'Ctrl-Space': 'autocomplete', Tab: (c) => c.replaceSelection('  '),
      },
      hintOptions: { tables: tables || {}, completeSingle: false },
    });
    ed.setSize(null, 'auto');
    ed.on('change', () => onChange?.(ed.getValue()));
    if (minLines) ed.getWrapperElement().style.minHeight = `${minLines * 19.5 + 8}px`;
    cm.current = ed;
    if (handle) handle.current = { set: (v, focus = false) => { ed.setValue(v); if (focus) { ed.focus(); ed.setCursor(ed.lineCount(), 0); } }, get: () => ed.getValue(), focus: () => ed.focus(), insert: (t) => { ed.replaceSelection(t); ed.focus(); } };
    return () => ref.current && ref.current.replaceChildren();
  }, []);
  useEffect(() => { if (cm.current) cm.current.setOption('hintOptions', { tables: tables || {}, completeSingle: false }); }, [tables]);
  return html`<div ref=${ref}></div>`;
}

function remember(world, sql) {
  const h = settings.get('history.' + world, []).filter((x) => x.sql !== sql);
  h.unshift({ sql, ts: Date.now() });
  settings.set('history.' + world, h.slice(0, 40));
}

export function SqlRunner({ world, seed, initialSql, tables, autoRun, compact, handle: outer, onResult, chartSpec }) {
  const app = useApp();
  const own = useRef();
  const handle = outer || own;
  const [sql, setSql] = useState(initialSql || '');
  const [state, setState] = useState({});
  const [tab, setTab] = useState(chartSpec ? 'chart' : 'table');
  const run = async (text) => {
    const q = (text ?? handle.current?.get() ?? sql).trim();
    if (!q) return;
    setState({ running: true, result: state.result });
    remember(world, q);
    try {
      const result = await api('/api/sql', { method: 'POST', body: { world, sql: q, limit: 5000 } });
      setState({ result });
      onResult?.(result);
    } catch (error) {
      setState({ error });
    }
  };
  useEffect(() => { if (handle.current) handle.current.run = run; });
  useEffect(() => { if (autoRun && initialSql) run(initialSql); }, []);
  useEffect(() => { if (initialSql !== undefined && handle.current && handle.current.get() !== initialSql) { handle.current.set(initialSql); setState({}); } }, [initialSql]);
  const { result, error, running } = state;
  const hints = tables ? Object.fromEntries(tables.map((t) => [t.name, t.columns.map((c) => c.name)])) : {};
  return html`<div class="stack" style="--gap:12px">
    <div class="editor">
      <${SqlEditor} value=${sql} onChange=${setSql} onRun=${run} tables=${hints} minLines=${compact ? 4 : 7} handle=${handle} />
      <div class="editor-bar">
        <button class="btn primary sm" onClick=${() => run()} disabled=${running}><${Icon} name="play" />Run</button>
        <span style="flex:1"></span>
        ${running && html`<span class="result-meta"><span class="spinner"></span>Running…</span>`}
        ${result && !running && html`<span class="result-meta"><span>${fmtNum(result.rows.length)}${result.truncated ? '+' : ''} rows</span><span>${fmtNum(result.ms)} ms</span></span>`}
      </div>
    </div>
    <${ErrorBox} error=${error} />
    ${result && html`<div class="result">
      <div class="row" style="justify-content:space-between;padding-right:8px;border-bottom:1px solid var(--border)">
        <${Tabs} tabs=${[{ id: 'table', label: 'Table' }, { id: 'chart', label: 'Chart' }]} active=${tab} onChange=${setTab} />
        <div class="row">
          ${result.truncated && html`<span class="faint small">First ${fmtNum(result.rows.length)} rows</span>`}
          <button class="btn sm ghost" onClick=${() => download(`${world}_query.csv`, toCSV(result.columns, result.rows))}><${Icon} name="download" />CSV</button>
        </div>
      </div>
      ${result.columns.length === 0 ? html`<div class="empty">The statement ran and returned no table.</div>`
        : tab === 'table' ? html`<${DataTable} columns=${result.columns} types=${result.types} rows=${result.rows} pageSize=${200} />`
          : html`<${ResultChart} result=${result} initial=${chartSpec} />`}
    </div>`}
  </div>`;
}
