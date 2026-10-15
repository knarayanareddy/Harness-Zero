// Headless DOM smoke test for the shipped Harness-Zero web UI (review tool).
//
// Loads index.html exactly as the daemon serves it, imports the same ES modules
// the page loads, drives the composer, and inspects the resulting DOM. It is not
// a browser (no layout/CSS), but DOM wiring, fetch, SSE parsing and event
// handling are the real code paths — including the SSE semantics browsers use:
// `onmessage` fires only for unnamed events; `event: <type>` frames go to
// addEventListener(<type>) listeners.
//
// Usage (from any scratch directory, outside the repo):
//   npm init -y && npm i jsdom@24
//   harness ui -w /tmp/ws -p 8765 -s 8766 -h 127.0.0.1 --no-open &
//   BASE=http://127.0.0.1:8765 STATIC=/path/to/harness/frontends/web/static \
//     PROMPT='hello' WAIT_MS=8000 SETTINGS=1 node ui_smoke.mjs
// Optional: NEW_SESSION=1 clicks 'New Session' first (a fresh daemon has no active
// session, so sending without it fails). CLICK='<css selector>' clicks an element after the turn and reports
// the timeline tail (e.g. CLICK='[data-notice="Account deletion confirmed."]').
import { JSDOM, VirtualConsole } from 'jsdom';
import path from 'node:path';

const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const STATIC = process.env.STATIC || path.resolve('harness/frontends/web/static');
const PROMPT = process.env.PROMPT || 'What does app.py do?';
const WAIT_MS = parseInt(process.env.WAIT_MS || '9000', 10);
const DO_SETTINGS = process.env.SETTINGS === '1';
const CLICK = process.env.CLICK || '';
const NEW_SESSION = process.env.NEW_SESSION === '1'; // click 'New Session' before sending (fresh daemon has no active session)

const consoleLog = [];
const vc = new VirtualConsole();
for (const lvl of ['log', 'warn', 'error', 'info', 'debug']) {
  vc.on(lvl, (...a) => consoleLog.push(`[${lvl}] ${a.map(String).join(' ')}`.slice(0, 300)));
}
vc.on('jsdomError', (e) => consoleLog.push(`[jsdomError] ${String(e.message || e).slice(0, 300)}`));

// 1. Fetch the page exactly as the browser would (token injected server side)
const res = await fetch(`${BASE}/`);
const html = await res.text();
const setCookie = res.headers.get('set-cookie') || '';
const cookie = setCookie.split(';')[0];

const dom = new JSDOM(html, { url: `${BASE}/`, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc });
const { window } = dom;

// 2. Globals the modules expect
const g = globalThis;
g.window = window; g.document = window.document; try { Object.defineProperty(g, 'navigator', { value: window.navigator, configurable: true }); } catch (e) {}
for (const k of ['HTMLElement', 'Element', 'Node', 'Event', 'CustomEvent', 'KeyboardEvent', 'MouseEvent', 'FormData', 'localStorage', 'sessionStorage', 'requestAnimationFrame', 'getComputedStyle', 'URLSearchParams', 'DOMParser']) {
  if (window[k] !== undefined && g[k] === undefined) g[k] = window[k];
}
g.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
g.scrollTo = () => {};
window.scrollTo = () => {};
window.HTMLElement.prototype.scrollIntoView = function () {};
window.marked = { parse: (s) => String(s) };
window.DOMPurify = { sanitize: (s) => String(s) };
window.Prism = { highlightAll: () => {} };
g.marked = window.marked; g.DOMPurify = window.DOMPurify; g.Prism = window.Prism;

// Node fetch with relative URLs + cookie jar
const nodeFetch = g.fetch;
const fetchLog = [];
g.fetch = async (url, opts = {}) => {
  const abs = String(url).startsWith('http') ? String(url) : `${BASE}${url}`;
  opts.headers = Object.assign({}, opts.headers || {});
  if (cookie) opts.headers['Cookie'] = cookie;
  if (String(abs).includes('submit_turn')) (globalThis.__submit ||= []).push(String(opts.body).slice(0,200));
  const r = await nodeFetch(abs, opts);
  fetchLog.push(`${opts.method || 'GET'} ${new URL(abs).pathname} -> ${r.status}`);
  return r;
};
window.fetch = g.fetch;

// Minimal EventSource polyfill over fetch (text/event-stream)
class ES {
  constructor(url) {
    this.url = String(url).startsWith('http') ? String(url) : `${BASE}${url}`;
    this.readyState = 0; this.onopen = null; this.onmessage = null; this.onerror = null;
    this._listeners = {}; this._closed = false; this._run();
  }
  addEventListener(t, f) { (this._listeners[t] ||= []).push(f); }
  close() { this._closed = true; this.readyState = 2; this._ctrl?.abort(); }
  async _run() {
    this._ctrl = new AbortController();
    try {
      const r = await nodeFetch(this.url, { headers: { Accept: 'text/event-stream', Cookie: cookie }, signal: this._ctrl.signal });
      fetchLog.push(`GET ${new URL(this.url).pathname.replace(/\?.*/, '')} (SSE) -> ${r.status}`);
      if (r.status !== 200) { this.onerror?.({ status: r.status }); return; }
      this.readyState = 1; this.onopen?.({});
      const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (!this._closed) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
          let data = [], id = null, type = 'message';
          for (const line of chunk.split('\n')) {
            if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
            else if (line.startsWith('id:')) id = line.slice(3).trim();
            else if (line.startsWith('event:')) type = line.slice(6).trim();
          }
          if (!data.length) continue;
          const ev = { data: data.join('\n'), lastEventId: id, type };
          try { const env = JSON.parse(ev.data); (globalThis.__sse ||= []).push(`${env.type}@${(env.session_id||'').slice(-4)}`); } catch {}
          if (type === 'message') this.onmessage?.(ev);
          (this._listeners[type] || []).forEach((f) => f(ev));
        }
      }
    } catch (e) { if (!this._closed) this.onerror?.(e); }
  }
}
g.EventSource = ES; window.EventSource = ES;

// 3. Import the module graph exactly as the page does (<script type="module" src="/static/app.js">)
const moduleSrcs = [...window.document.querySelectorAll('script[type="module"][src]')].map((s) => s.getAttribute('src'));
for (const src of moduleSrcs) {
  const file = path.join(STATIC, src.replace(/^\/static\//, ''));
  await import(file);
}
// The page's DOMContentLoaded already fired before import → fire it again so app.js bootstraps
window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(2500);

const $ = (sel) => window.document.querySelector(sel);
const txt = (sel) => ($(sel)?.textContent || '').replace(/\s+/g, ' ').trim();
const report = {};
report.app_booted = !!window.HarnessApp;
report.status_text = txt('#status-text') || txt('#connection-status') || null;
report.model_options = [...(window.document.querySelectorAll('#model-select option') || [])].map((o) => o.value);
report.strategy_options = [...(window.document.querySelectorAll('#strategy-select option') || [])].map((o) => o.value);
report.session_list_items = window.document.querySelectorAll('#session-list .session-item, #session-list li, #session-list [data-session-id]').length;

// 4. Optionally create a session, then type a prompt and submit the composer form
if (NEW_SESSION) { $('#btn-new-session')?.click(); await sleep(1200); }
report.active_session_before_send = window.HarnessApp?.activeSessionId || null;
const input = $('#composer-input'); const form = $('#composer-form');
if (input && form) {
  input.value = PROMPT;
  form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
}
await sleep(WAIT_MS);

const timeline = $('#timeline');
report.timeline_children = timeline ? timeline.children.length : null;
report.timeline_text = timeline ? timeline.textContent.replace(/\s+/g, ' ').trim().slice(0, 600) : null;
report.approval_cards = window.document.querySelectorAll('[data-approval-id], .approval-card, #approval-banner:not(.hidden)').length;
report.tool_cards = window.document.querySelectorAll('.tool-card, .tool-block, [data-call-id]').length;
report.economics = txt('#economics-indicator') || null;
report.context_badge = txt('#context-tokens-badge') || null;

if (CLICK) {
  const el = $(CLICK);
  if (el) { el.click(); await sleep(1500); }
  report.clicked = CLICK + (el ? ' (found)' : ' (NOT FOUND)');
  report.timeline_text_after_click = ($('#timeline')?.textContent || '').replace(/\s+/g, ' ').trim().slice(-300);
}

if (DO_SETTINGS) {
  // Open settings via the app API, then inspect what survives
  window.HarnessApp.openSettings?.();
  await sleep(2500);
  report.settings = {
    static_pages_remaining: window.document.querySelectorAll('#settings-content .settings-page').length,
    sidebar_tabs: window.document.querySelectorAll('[data-stab]').length,
    schema_group_tabs: [...window.document.querySelectorAll('#settings-content [data-group]')].map((b) => b.textContent.trim()),
    schema_fields: window.document.querySelectorAll('#settings-content [data-setting-path], #settings-content [data-path]').length,
    content_text_head: txt('#settings-content').slice(0, 300),
  };
  // Click a sidebar tab (Account) and see whether anything changes
  const acct = $('[data-stab="account"]');
  const before = txt('#settings-content').slice(0, 120);
  acct?.click(); await sleep(500);
  report.settings.account_tab_click_changed_content = txt('#settings-content').slice(0, 120) !== before;
  report.settings.spage_account_present = !!$('#spage-account');
}

report.sse_events = (globalThis.__sse||[]).slice(0,40);
report.app_session = window.HarnessApp?.sessionId || window.HarnessApp?.currentSessionId || window.HarnessApp?.state?.sessionId || null;
report.app_keys = Object.keys(window.HarnessApp||{}).slice(0,40);
report.submit_bodies = globalThis.__submit||[];
report.fetch_log = fetchLog;
report.console_tail = consoleLog.slice(-12);
console.log(JSON.stringify(report, null, 2));
window.HarnessApp?.events?.disconnect?.();
process.exit(0);
