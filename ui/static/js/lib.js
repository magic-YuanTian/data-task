// Shared helpers: rendering, fetching, routing, formatting, local settings.
export { html, render, h, Component, createContext, useState, useEffect, useLayoutEffect, useRef, useMemo,
  useCallback, useContext, useReducer } from '../vendor/preact-htm.js';
import { html, createContext, useState, useEffect, useContext, useRef } from '../vendor/preact-htm.js';

export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

// ---------- fetching ----------
const cache = new Map();

// The single-file copy made by ui/export_static.py carries the recorded responses instead of a server.
const OFFLINE = window.DTB_OFFLINE;
export const sqlKey = (world, sql) => world + '\n' + sql.trim().replace(/;\s*$/, '').replace(/\s+/g, ' ');

function offline(path, method, body) {
  let hit = method === 'POST' ? OFFLINE.sql[sqlKey(body.world, body.sql || '')] : OFFLINE.get[path.split('?')[0]];
  if (typeof hit === 'string') hit = OFFLINE.sql[hit];      // the same query written differently
  if (hit !== undefined) return Promise.resolve(hit);
  const msg = method === 'POST' ? OFFLINE.sql_note : `not in this copy: ${path}`;
  return Promise.reject(Object.assign(new Error(msg), { status: 404 }));
}

export async function api(path, { method = 'GET', body, fresh = false } = {}) {
  if (OFFLINE) return offline(path, method, body);
  const key = method + ' ' + path;
  if (method === 'GET' && !fresh && cache.has(key)) return cache.get(key);
  const p = fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  }).then(async (r) => {
    const data = await r.json().catch(() => ({ error: r.statusText }));
    if (!r.ok) throw Object.assign(new Error(data.error || r.statusText), { status: r.status });
    return data;
  });
  if (method === 'GET') {
    cache.set(key, p);
    p.catch(() => cache.delete(key));
  }
  return p;
}

export function invalidate(prefix) {
  for (const k of [...cache.keys()]) if (k.includes(prefix)) cache.delete(k);
}

// useFetch(url) -> {data, error, loading, reload}; url null skips.
export function useFetch(url, deps = []) {
  const [state, set] = useState({ data: null, error: null, loading: !!url });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!url) { set({ data: null, error: null, loading: false }); return; }
    let live = true;
    set((s) => ({ ...s, loading: true, error: null }));
    api(url, { fresh: tick > 0 })
      .then((data) => live && set({ data, error: null, loading: false }))
      .catch((error) => live && set({ data: null, error, loading: false }));
    return () => { live = false; };
  }, [url, tick, ...deps]);
  return { ...state, reload: () => setTick((t) => t + 1) };
}

// ---------- routing (hash based) ----------
export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [p, qs] = raw.split('?');
  const path = p.split('/').filter(Boolean).map(decodeURIComponent);
  const query = Object.fromEntries(new URLSearchParams(qs || ''));
  return { path, query };
}

export function href(path, query = {}) {
  const q = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== ''));
  const s = q.toString();
  return '#/' + path.map(encodeURIComponent).join('/') + (s ? '?' + s : '');
}

export function navigate(path, query = {}, { replace = false } = {}) {
  const h = href(path, query);
  if (replace) history.replaceState(null, '', h);
  else location.hash = h;
  if (replace) window.dispatchEvent(new HashChangeEvent('hashchange'));
}

export function useRoute() {
  const [r, set] = useState(parseHash());
  useEffect(() => {
    const on = () => set(parseHash());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return r;
}

// ---------- local settings ----------
export const settings = {
  get(k, d = null) {
    try { const v = localStorage.getItem('dl.' + k); return v === null ? d : JSON.parse(v); } catch { return d; }
  },
  set(k, v) { try { localStorage.setItem('dl.' + k, JSON.stringify(v)); } catch { /* ignore */ } },
};

export function useSetting(k, d) {
  const [v, set] = useState(() => settings.get(k, d));
  return [v, (x) => { settings.set(k, x); set(x); }];
}

// ---------- formatting ----------
const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

export function fmtNum(v, { digits } = {}) {
  if (v === null || v === undefined || v === '') return '';
  if (typeof v !== 'number') return String(v);
  if (Number.isInteger(v)) return Math.abs(v) >= 10000 ? nf0.format(v) : String(v);
  const a = Math.abs(v);
  if (a !== 0 && a < 0.0001) return v.toExponential(2);
  const d = digits ?? (a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : a >= 0.01 ? 3 : 4);
  let out = new Intl.NumberFormat('en-US', { maximumFractionDigits: d, minimumFractionDigits: 0 }).format(v);
  // do not let rounding turn a fraction into a whole number (0.99998 is not 1)
  if (digits === undefined && a < 1000 && !/[.]/.test(out)) out = new Intl.NumberFormat('en-US', { maximumSignificantDigits: 6 }).format(v);
  return out;
}

export function fmtBytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1024 ** 2) return (n / 1024).toFixed(0) + ' KB';
  if (n < 1024 ** 3) return (n / 1024 ** 2).toFixed(1) + ' MB';
  return (n / 1024 ** 3).toFixed(1) + ' GB';
}

export function fmtCompact(n) {
  if (n === null || n === undefined) return '';
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e4) return (n / 1e3).toFixed(0) + 'K';
  return nf0.format(n);
}

export function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
  if (diff < 86400) return Math.floor(diff / 3600) + ' h ago';
  return d.toLocaleDateString('en-CA') + ' ' + d.toTimeString().slice(0, 5);
}

export const humanize = (k) => String(k).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

export const isNumType = (t) => /^(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|U\w*INT|FLOAT|DOUBLE|REAL|DECIMAL)/.test(t || '');
export const isTimeType = (t) => /^(DATE|TIMESTAMP)/.test(t || '');

export function toCSV(columns, rows) {
  const esc = (v) => {
    if (v === null || v === undefined) return '';
    const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  return [columns.map(esc).join(','), ...rows.map((r) => r.map(esc).join(','))].join('\n');
}

export function download(name, text, type = 'text/csv') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function useDebounced(value, ms = 200) {
  const [v, set] = useState(value);
  useEffect(() => { const t = setTimeout(() => set(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

export function useKey(handler, deps = []) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const on = (e) => {
      const t = e.target;
      const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' ||
        t.isContentEditable || t.closest?.('.CodeMirror'));
      ref.current(e, typing);
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, deps);
}

export function seedOf(app, world) {
  const w = app.meta.worlds.find((x) => x.world === world);
  const s = settings.get('seed.' + world);
  return w && w.seeds.includes(s) ? s : w?.default_seed;
}

export function worldOf(app, world) {
  return app.meta.worlds.find((x) => x.world === world);
}

export const nothing = html``;
