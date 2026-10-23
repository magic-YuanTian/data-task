// Application shell: top bar, navigation, routing.
import { html, render, useState, useEffect, AppContext, api, useRoute, href, navigate, settings } from './lib.js';
import { Loading, ErrorBox, Toast } from './ui.js';
import { DataPage } from './pages/data.js';
import { SqlPage } from './pages/sqlpage.js';
import { AboutPage } from './pages/about.js';
import { TasksPage, TaskPage } from './pages/tasks.js';

const WORLD_PAGES = ['tasks', 'task', 'data', 'sql'];

// Data environment menu in the top bar.
function EnvPicker({ worlds, value, onChange }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (!e.target.closest('.env-picker')) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  const cur = worlds.find((x) => x.world === value) || worlds[0];
  return html`<div class="env-picker">
    <button class=${'env-btn' + (open ? ' open' : '')} onClick=${() => setOpen(!open)}>
      <span class="env-title">${cur?.title}</span><span class="env-domain">${cur?.domain}</span>
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6" /></svg>
    </button>
    ${open && html`<div class="env-menu">${worlds.map((x) => html`<button class=${'env-opt' + (x.world === value ? ' on' : '')}
        onClick=${() => { setOpen(false); if (x.world !== value) onChange(x.world); }}>
      <span><span class="env-title">${x.title}</span><span class="env-domain">${x.domain}</span></span>
      ${x.world === value && html`<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M20 6 9 17l-5-5" /></svg>`}
    </button>`)}</div>`}
  </div>`;
}

function Shell({ meta }) {
  const route = useRoute();
  const [world, setWorldState] = useState(() => {
    const w = settings.get('world');
    return meta.worlds.some((x) => x.world === w) ? w : meta.worlds[0]?.world;
  });
  const [toast, setToast] = useState('');
  const [page, ...rest] = route.path;
  const routeWorld = WORLD_PAGES.includes(page) ? rest[0] : route.query.world;
  useEffect(() => {
    if (routeWorld && routeWorld !== world && meta.worlds.some((x) => x.world === routeWorld)) {
      setWorldState(routeWorld); settings.set('world', routeWorld);
    }
  }, [routeWorld]);
  const app = {
    meta, toast: setToast,
    get current() { return routeWorld && meta.worlds.some((x) => x.world === routeWorld) ? routeWorld : world; },
    world(w) { return meta.worlds.find((x) => x.world === w); },
    seed: () => 1,
    setWorld(w) {
      settings.set('world', w); setWorldState(w);
      if (page === 'task') navigate(['tasks', w], {});
      else if (WORLD_PAGES.includes(page)) navigate([page, w], {});
    },
  };
  const ACTIVE = { about: [undefined, 'about'], tasks: ['tasks', 'task'], data: ['data', 'sql'] };
  const tabItem = (id, label, to) => html`<a class=${(ACTIVE[id] || [id]).includes(page) ? 'on' : ''} href=${to}>${label}</a>`;
  let body;
  const w = routeWorld && app.world(routeWorld) ? routeWorld : world;
  switch (page) {
    case undefined: case 'about': body = html`<${AboutPage} key=${w} />`; break;
    case 'tasks': body = html`<${TasksPage} world=${w} query=${route.query} key=${w} />`; break;
    case 'task': body = html`<${TaskPage} world=${w} id=${rest[1]} key=${w + rest[1]} />`; break;
    case 'data': body = html`<${DataPage} world=${w} query=${route.query} key=${w} />`; break;
    case 'sql': body = html`<${SqlPage} world=${w} query=${route.query} key=${w} />`; break;
    default: body = html`<div class="page"><h1>Not found</h1><p><a href="#/">Back to Taxonomy</a></p></div>`;
  }
  return html`<${AppContext.Provider} value=${app}>
    <div class="app no-side">
      <header class="topbar">
        <a class="brand" href="#/"><span class="brand-mark"><i></i><i></i><i></i><i></i></span><b>DataTaskBench</b></a>
        <${EnvPicker} worlds=${meta.worlds} value=${w} onChange=${(x) => app.setWorld(x)} />
        <nav class="top-tabs">
          ${tabItem('data', 'Data', href(['data', w]))}
          ${tabItem('tasks', 'Tasks', href(['tasks', w]))}
        </nav>
        <span class="spacer"></span>
        <nav class="top-tabs about-nav">${tabItem('about', 'Taxonomy', '#/')}</nav>
      </header>
      <main class="main" id="main">${body}</main>
    </div>
    <${Toast} text=${toast} onDone=${() => setToast('')} />
  </${AppContext.Provider}>`;
}

// Shown in a frame on a page the server allows (--embed-origin): tell that page the route, so its address bar links here.
function reportRoute(origins) {
  if (window.parent === window || !document.referrer) return;
  const parent = new URL(document.referrer).origin;
  if (!origins.includes(parent)) return;
  const post = () => window.parent.postMessage({ type: 'datataskbench:route', hash: location.hash }, parent);
  window.addEventListener('hashchange', post);
  post();
}

function Root() {
  const [state, set] = useState({});
  useEffect(() => {
    api('/api/meta').then((meta) => { reportRoute(meta.embed_origins || []); set({ meta }); }).catch((error) => set({ error }));
  }, []);
  if (state.error) return html`<div class="page"><${ErrorBox} error=${state.error} /></div>`;
  if (!state.meta) return html`<${Loading} label="Loading worlds…" />`;
  return html`<${Shell} meta=${state.meta} />`;
}

// scroll to top on page change
let lastPath = '';
window.addEventListener('hashchange', () => {
  const p = location.hash.split('?')[0];
  if (p !== lastPath) { document.getElementById('main')?.scrollTo(0, 0); lastPath = p; }
});

render(html`<${Root} />`, document.getElementById('app'));
