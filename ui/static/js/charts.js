// Charts with Observable Plot: a resizable figure, a small chart grammar, and guesses for query results.
import { html, useRef, useState, useEffect, useLayoutEffect, useMemo, isNumType, isTimeType, fmtNum } from './lib.js';

const Plot = window.Plot;
const d3 = window.d3;
export const PALETTE = ['#4c78a8', '#f58518', '#54a24b', '#e45756', '#72b7b2', '#b279a2', '#eeca3b', '#9d755d', '#ff9da6', '#bab0ac'];
export const ACCENT = '#3d6fcf';
const STYLE = { fontSize: '11.5px', fontFamily: 'inherit', background: 'transparent', overflow: 'visible' };

export function Figure({ build, deps = [], minHeight }) {
  const ref = useRef();
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const ro = new ResizeObserver(([e]) => {
      const nw = Math.floor(e.contentRect.width);
      setW((old) => (Math.abs(old - nw) > 4 ? nw : old));
    });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    if (!w || !ref.current) return;
    let el;
    try { el = build(w); } catch (e) {
      el = document.createElement('div'); el.className = 'error'; el.textContent = 'Chart error: ' + e.message;
    }
    ref.current.replaceChildren(...(el ? [el] : []));
  }, [w, ...deps]);
  return html`<div class="figure" ref=${ref} style=${minHeight ? `min-height:${minHeight}px` : ''}></div>`;
}

// Parse SQL result values into JS values that Plot understands.
export function toObjects(columns, types, rows) {
  const conv = columns.map((c, i) => {
    if (isTimeType(types[i])) return (v) => (v === null ? null : new Date(String(v).length <= 10 ? v : String(v).replace(' ', 'T') + 'Z'));
    return (v) => v;
  });
  return rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, conv[i](r[i])])));
}

const bigFormat = (v) => (Math.abs(v) >= 10000 ? d3.format('~s')(v) : d3.format('~')(v));

function labelWidth(data, key) {
  const n = d3.max(data, (d) => String(d[key] ?? '').length) || 4;
  return Math.min(260, Math.max(40, n * 6.4 + 12));
}

// spec: {mark, x, y, color, size, height, xLabel, yLabel, limit, zero}
export function buildPlot(spec, data, width) {
  const { mark = 'line', x, y, color, size } = spec;
  const xLabel = 'xLabel' in spec ? spec.xLabel : x;
  const yLabel = 'yLabel' in spec ? spec.yLabel : y;
  const height = spec.height || 260;
  const common = { width, height, style: STYLE, marginTop: 24, marginRight: 14 };
  const colorScale = color ? { color: { range: PALETTE, legend: true, type: 'categorical' } } : {};
  const fill = color || ACCENT;
  const yTick = { tickFormat: bigFormat, grid: true, label: yLabel };
  const multi = color && new Set(data.map((d) => d[color])).size > 1;
  if (mark === 'barh') {
    const n = spec.limit || 30;
    const top = [...data].sort((a, b) => (b[y] ?? 0) - (a[y] ?? 0)).slice(0, n);
    return Plot.plot({
      ...common, height: Math.max(120, top.length * 20 + 40), marginLeft: labelWidth(top, x), ...colorScale,
      x: { grid: true, tickFormat: bigFormat, label: yLabel }, y: { label: null, domain: top.map((d) => d[x]) },
      marks: [Plot.barX(top, { y: x, x: y, fill, tip: true }), Plot.ruleX([0])],
    });
  }
  if (mark === 'hist') {
    return Plot.plot({
      ...common, marginLeft: 50, y: { grid: true, tickFormat: bigFormat, label: 'count' }, x: { label: xLabel },
      marks: [Plot.rectY(data, Plot.binX({ y: 'count' }, { x, fill: ACCENT, thresholds: 40 })), Plot.ruleY([0])],
    });
  }
  const xIsCat = data.length && typeof data[0][x] === 'string';
  const base = { ...common, marginLeft: 54, ...colorScale, y: yTick, x: { label: xLabel } };
  if (mark === 'bar' && data[0]?.[x] instanceof Date) {
    const ts = data.map((d) => +d[x]).sort((a, b) => a - b);
    const step = ts.length > 1 ? Math.min(...ts.slice(1).map((t, i) => t - ts[i]).filter((v) => v > 0)) / 864e5 : 1;
    const interval = step >= 300 ? d3.utcYear : step >= 27 ? d3.utcMonth : step >= 6 ? d3.utcWeek : d3.utcDay;
    return Plot.plot({ ...base, marks: [Plot.rectY(data, { x, y, interval, fill, inset: 0.5, tip: true }), Plot.ruleY([0])] });
  }
  if (mark === 'bar') {
    return Plot.plot({
      ...base, marginBottom: xIsCat ? 60 : 30,
      x: { ...base.x, type: xIsCat || !(data[0]?.[x] instanceof Date) ? 'band' : undefined, tickRotate: xIsCat && data.length > 8 ? -35 : 0 },
      marks: [Plot.barY(data, { x, y, fill, tip: true, sort: xIsCat ? null : undefined }), Plot.ruleY([0])],
    });
  }
  if (mark === 'area') {
    return Plot.plot({ ...base, marks: [Plot.areaY(data, { x, y, fill, fillOpacity: multi ? 0.85 : 0.25, tip: true, order: spec.order }),
      ...(multi ? [] : [Plot.lineY(data, { x, y, stroke: ACCENT })]), Plot.ruleY([0])] });
  }
  if (mark === 'dot') {
    return Plot.plot({
      ...base, x: { ...base.x, grid: true, tickFormat: bigFormat }, ...(size ? { r: { range: [1.5, 9] } } : {}),
      marks: [Plot.dot(data, { x, y, fill: color || ACCENT, r: size || 2.2, fillOpacity: 0.55, tip: true }),
        ...(spec.fit ? [Plot.linearRegressionY(data, { x, y, stroke: '#888' })] : [])],
    });
  }
  // line
  const dense = data.length > 400;
  const win = spec.window;
  return Plot.plot({
    ...base, x: { ...base.x, type: xIsCat ? 'point' : undefined },
    marks: [
      ...(spec.zero !== false ? [Plot.ruleY([0])] : []),
      Plot.lineY(data, { x, y, stroke: color || ACCENT, strokeWidth: dense ? 1 : 1.6, z: color, strokeOpacity: win ? 0.3 : 1 }),
      ...(win ? [Plot.lineY(data, Plot.windowY({ k: win, anchor: 'middle' }, { x, y, stroke: color || ACCENT, strokeWidth: 1.6, z: color }))] : []),
      ...(!dense && data.length < 80 ? [Plot.dot(data, { x, y, fill: color || ACCENT, r: 2 })] : []),
      Plot.tip(data, Plot.pointerX({ x, y, stroke: color, title: (d) => `${fmtX(d[x])}\n${y}: ${fmtNum(d[y])}${color ? '\n' + d[color] : ''}` })),
    ],
  });
}

const fmtX = (v) => (v instanceof Date ? v.toISOString().slice(0, v.getUTCHours() || v.getUTCMinutes() ? 16 : 10).replace('T', ' ') : fmtNum(v));

export function buildMap({ geo, points, lon = 'lon', lat = 'lat', r, color, title, height = 520 }, width) {
  const domain = points?.length ? { type: 'MultiPoint', coordinates: points.filter((p) => p[lon] && p[lat]).map((p) => [p[lon], p[lat]]) } : geo;
  return Plot.plot({
    width, height, style: STYLE, projection: { type: 'mercator', domain, inset: 12 },
    ...(color ? { color: { range: PALETTE, legend: true, type: 'categorical' } } : {}),
    ...(r ? { r: { range: [0.8, 7] } } : {}),
    marks: [
      ...(geo ? [Plot.geo(geo, { fill: 'currentColor', fillOpacity: 0.04, stroke: 'currentColor', strokeOpacity: 0.22, strokeWidth: 0.5 })] : []),
      Plot.dot(points || [], { x: lon, y: lat, r: r || 1.6, fill: color || ACCENT, fillOpacity: 0.7, stroke: 'white', strokeWidth: 0.3,
        tip: true, title: title ? (d) => d[title] + (r ? `\n${r}: ${fmtNum(d[r])}` : '') : undefined }),
    ],
  });
}

// Guess a sensible chart for a query result.
export function guessSpec(columns, types, rows) {
  if (!columns.length || !rows.length) return null;
  const kind = columns.map((c, i) => (isTimeType(types[i]) ? 'time' : isNumType(types[i]) ? 'num' : 'cat'));
  const idx = (k) => kind.map((x, i) => (x === k ? i : -1)).filter((i) => i >= 0);
  const [times, nums, cats] = [idx('time'), idx('num'), idx('cat')];
  const distinct = (i) => new Set(rows.map((r) => r[i])).size;
  const cat = cats.find((i) => distinct(i) <= 12 && distinct(i) > 1);
  if (times.length && nums.length) return { mark: 'line', x: columns[times[0]], y: columns[nums[0]], color: cat !== undefined ? columns[cat] : null };
  if (cats.length && nums.length) {
    if (cat !== undefined && cats.length > 1) return { mark: 'barh', x: columns[cats.find((i) => i !== cat) ?? cats[0]], y: columns[nums[0]] };
    return { mark: rows.length <= 14 && distinct(cats[0]) === rows.length ? 'bar' : 'barh', x: columns[cats[0]], y: columns[nums[0]] };
  }
  if (nums.length >= 2) {
    const xi = nums[0];
    const vals = rows.map((r) => r[xi]);
    const ordinal = vals.every((v, i) => v === null || Number.isInteger(v) && (i === 0 || v >= vals[i - 1])) && distinct(xi) <= 400;
    return { mark: ordinal ? 'line' : 'dot', x: columns[xi], y: columns[nums[1]], color: cat !== undefined ? columns[cat] : null, zero: ordinal };
  }
  if (nums.length === 1) return { mark: 'hist', x: columns[nums[0]] };
  return null;
}

export function ResultChart({ result, initial }) {
  const { columns, types, rows } = result;
  const guess = useMemo(() => initial || guessSpec(columns, types, rows), [result]);
  const [spec, setSpec] = useState(guess);
  useEffect(() => setSpec(guess), [guess]);
  const data = useMemo(() => toObjects(columns, types, rows), [result]);
  if (!spec) return html`<div class="empty">No chart for this result. A chart needs a numeric column.</div>`;
  const opt = (v) => html`<option value=${v}>${v}</option>`;
  const set = (k) => (e) => setSpec({ ...spec, [k]: e.target.value || null });
  const needsY = spec.mark !== 'hist';
  return html`<div>
    <div class="chart-controls">
      <label>Chart <select value=${spec.mark} onChange=${set('mark')}>
        ${[['line', 'Line'], ['bar', 'Bars'], ['barh', 'Ranked bars'], ['area', 'Area'], ['dot', 'Scatter'], ['hist', 'Histogram']].map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select></label>
      <label>${spec.mark === 'barh' ? 'Category' : 'X'} <select value=${spec.x} onChange=${set('x')}>${columns.map(opt)}</select></label>
      ${needsY && html`<label>${spec.mark === 'barh' ? 'Value' : 'Y'} <select value=${spec.y} onChange=${set('y')}>${columns.map(opt)}</select></label>`}
      ${needsY && html`<label>Color <select value=${spec.color || ''} onChange=${set('color')}><option value="">none</option>${columns.map(opt)}</select></label>`}
      ${spec.mark === 'dot' && html`<label>Size <select value=${spec.size || ''} onChange=${set('size')}><option value="">none</option>${columns.map(opt)}</select></label>`}
    </div>
    <div class="chart-box"><${Figure} build=${(w) => buildPlot({ ...spec, height: 300 }, data, w)} deps=${[spec, data]} /></div>
  </div>`;
}

// Small chart for a {key: number} mapping found in a gold answer.
export function SeriesChart({ obj, height = 150 }) {
  const entries = Object.entries(obj);
  const timeLike = entries.every(([k]) => /^\d{4}(-\d{2})?(-\d{2})?$/.test(k));
  const data = entries.map(([k, v]) => ({ key: timeLike && k.length > 4 ? new Date(k.length === 7 ? k + '-01' : k) : k, value: v }));
  const spec = timeLike && entries.length > 12 ? { mark: 'line', x: 'key', y: 'value', height, xLabel: null, yLabel: null }
    : entries.length > 10 && !timeLike ? { mark: 'barh', x: 'key', y: 'value', yLabel: null, limit: 40 }
      : { mark: 'bar', x: 'key', y: 'value', height, xLabel: null, yLabel: null };
  return html`<${Figure} build=${(w) => buildPlot(spec, data, Math.min(w, 640))} deps=${[obj]} />`;
}
