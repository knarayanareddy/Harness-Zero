// Headless DOM lane for scenario tests.
//
// Loads index.html exactly as the daemon serves it (token injected server side),
// imports the same ES modules the page loads, drives the composer with the
// scenario prompts, waits for the kernel's turn.* events to reach the client,
// optionally approves approval requests through the real buttons, and prints a
// JSON report (timeline text, SSE events seen by the client, submit bodies,
// fetch log, console tail). jsdom is not a browser (no layout/CSS), but DOM
// wiring, fetch, SSE framing and event handling are the real code paths.
//
// Env: BASE, STATIC, PROMPTS (JSON array) | PROMPT, WAIT_MS (per turn cap),
//      NEW_SESSION=1 (click New Session first), APPROVE=1 (click approve when asked), MODEL
import { JSDOM, VirtualConsole } from 'jsdom';
import path from 'node:path';

const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const STATIC = process.env.STATIC || path.resolve('harness/frontends/web/static');
const PROMPTS = process.env.PROMPTS ? JSON.parse(process.env.PROMPTS) : [process.env.PROMPT || 'hello'];
const WAIT_MS = parseInt(process.env.WAIT_MS || '6000', 10);
const NEW_SESSION = process.env.NEW_SESSION === '1';
const RELOAD = process.env.RELOAD === '1'; // simulate a page refresh on an existing session: no clicks, no prompts
const APPROVE = process.env.APPROVE === '1';
const MODEL = process.env.MODEL || '';
const MODEL_PROVIDER = process.env.MODEL_PROVIDER || '';
const SETTINGS_PROBE = process.env.SETTINGS_PROBE === '1';

const consoleLog = [];
const vc = new VirtualConsole();
for (const lvl of ['log', 'warn', 'error', 'info', 'debug']) {
  vc.on(lvl, (...a) => consoleLog.push(`[${lvl}] ${a.map(String).join(' ')}`.slice(0, 300)));
}
vc.on('jsdomError', (e) => consoleLog.push(`[jsdomError] ${String(e.message || e).slice(0, 300)}`));

const res = await fetch(`${BASE}/`);
const html = await res.text();
const cookie = (res.headers.get('set-cookie') || '').split(';')[0];

const dom = new JSDOM(html, { url: `${BASE}/`, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc });
const { window } = dom;
const g = globalThis;
g.window = window; g.document = window.document;
try { Object.defineProperty(g, 'navigator', { value: window.navigator, configurable: true }); } catch (e) {}
for (const k of ['HTMLElement', 'Element', 'Node', 'Event', 'CustomEvent', 'KeyboardEvent', 'MouseEvent', 'FormData', 'localStorage', 'sessionStorage', 'getComputedStyle', 'URLSearchParams', 'DOMParser']) {
  if (window[k] !== undefined && g[k] === undefined) g[k] = window[k];
}
g.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
g.scrollTo = () => {}; window.scrollTo = () => {};
window.HTMLElement.prototype.scrollIntoView = function () {};
window.marked = { parse: (s) => String(s) };
window.DOMPurify = { sanitize: (s) => String(s) };
window.Prism = { highlightAll: () => {}, highlightElement: () => {} };
g.marked = window.marked; g.DOMPurify = window.DOMPurify; g.Prism = window.Prism;

const nodeFetch = g.fetch;
const fetchLog = [];
const submitBodies = [];
g.fetch = async (url, opts = {}) => {
  const abs = String(url).startsWith('http') ? String(url) : `${BASE}${url}`;
  opts.headers = Object.assign({}, opts.headers || {});
  if (cookie) opts.headers['Cookie'] = cookie;
  if (abs.includes('submit_turn')) submitBodies.push(safeJson(opts.body));
  const r = await nodeFetch(abs, opts);
  fetchLog.push(`${opts.method || 'GET'} ${new URL(abs).pathname} -> ${r.status}`);
  return r;
};
window.fetch = g.fetch;
function safeJson(s) { try { return JSON.parse(String(s)); } catch { return String(s).slice(0, 200); } }

// EventSource polyfill with browser semantics: named frames -> addEventListener(type); unnamed -> onmessage.
const sseSeen = [];
let lastTerminal = 0; // count of turn.completed/error/cancelled frames observed on the wire
let completedOk = 0;  // count of turn.completed only
let approvalsSeen = [];
class ES {
  constructor(url) {
    this.url = String(url).startsWith('http') ? String(url) : `${BASE}${url}`;
    this.readyState = 0; this.onopen = null; this.onmessage = null; this.onerror = null;
    this._listeners = {}; this._closed = false; this._run();
  }
  addEventListener(t, f) { (this._listeners[t] ||= []).push(f); }
  removeEventListener(t, f) { this._listeners[t] = (this._listeners[t] || []).filter((x) => x !== f); }
  close() { this._closed = true; this.readyState = 2; this._ctrl?.abort(); }
  async _run() {
    this._ctrl = new AbortController();
    try {
      const r = await nodeFetch(this.url, { headers: { Accept: 'text/event-stream', Cookie: cookie }, signal: this._ctrl.signal });
      fetchLog.push(`GET ${new URL(this.url).pathname} (SSE) -> ${r.status}`);
      if (r.status !== 200) { this.onerror?.({ status: r.status }); return; }
      this.readyState = 1; this.onopen?.({});
      const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (!this._closed) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const data = []; let id = null; let type = 'message';
          for (const line of chunk.split('\n')) {
            if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
            else if (line.startsWith('id:')) id = line.slice(3).trim();
            else if (line.startsWith('event:')) type = line.slice(6).trim();
          }
          if (!data.length) continue;
          const ev = { data: data.join('\n'), lastEventId: id, type };
          try {
            const env = JSON.parse(ev.data);
            sseSeen.push(env.type);
            if (['turn.completed', 'turn.error', 'turn.cancelled'].includes(env.type)) lastTerminal++;
            if (env.type === 'turn.completed') completedOk++;
            if (env.type === 'approval.required') approvalsSeen.push(env.data?.approval_id);
          } catch {}
          if (type === 'message') this.onmessage?.(ev);
          (this._listeners[type] || []).forEach((f) => f(ev));
        }
      }
    } catch (e) { if (!this._closed) this.onerror?.(e); }
  }
}
g.EventSource = ES; window.EventSource = ES;

const moduleSrcs = [...window.document.querySelectorAll('script[type="module"][src]')].map((s) => s.getAttribute('src'));
for (const src of moduleSrcs) {
  await import(path.join(STATIC, src.replace(/^\/static\//, '')));
}
window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const $ = (sel) => window.document.querySelector(sel);
const txt = (sel) => ($(sel)?.textContent || '').replace(/\s+/g, ' ').trim();
await sleep(1500);

const report = { app_booted: !!window.HarnessApp };
const timelineText = () => ($('#timeline')?.textContent || '').replace(/\s+/g, ' ').trim();
if (RELOAD) {
  // What the user sees right after pressing F5 on a daemon that has an active session.
  report.active_session_on_load = window.HarnessApp?.activeSessionId || null;
  report.timeline_text_on_load = timelineText();
  report.timeline_children_on_load = $('#timeline')?.children.length ?? null;
  const items = [...window.document.querySelectorAll('#session-list .session-item')];
  report.session_items = items.length;
  // The only click a user can make to get the history back: the session entry in the sidebar.
  (items.find((i) => i.classList.contains('active')) || items[0])?.click();
  await sleep(1000);
  report.timeline_text_after_click = timelineText();
  // Renderer fidelity, independent of wiring: what reloadTranscript() itself produces.
  if (window.HarnessApp?.reloadTranscript) { await window.HarnessApp.reloadTranscript(); await sleep(300); }
  report.timeline_text_after_reload_transcript = timelineText();
  report.empty_user_bubbles = [...window.document.querySelectorAll('#timeline .turn-user')].filter((n) => !n.textContent.trim()).length;
}
report.model_options = [...window.document.querySelectorAll('#model-select option')].map((o) => o.value);
if (NEW_SESSION) { $('#btn-new-session')?.click(); await sleep(800); }
report.active_session_before_send = window.HarnessApp?.activeSessionId || null;
if (MODEL && $('#model-select')) {
  const sel = $('#model-select');
  const matches = [...sel.options].filter((o) => o.value === MODEL);
  if (matches.length > 1 && MODEL_PROVIDER) {
    // Several providers can list the same model id (a fixture recorded from `mock` is served by `mock-v1`,
    // which the built-in mock also offers), and the chip's choice decides which provider the client names in
    // its submit — i.e. which provider answers and what the events golden says. Scope it explicitly instead of
    // letting option order decide. `option.selected` rather than `sel.value`, which would take the first match.
    const right = matches.find((o) => o.dataset.provider === MODEL_PROVIDER);
    if (right) right.selected = true;
  } else if (matches.length) {
    sel.value = MODEL;
  }
}
report.model_selected = $('#model-select')?.value || null;
// …and which provider that selection belongs to, because that is what the client puts in its submit body:
// a scenario that records 0 streams (the request resolved past the recorder) shows up here.
report.model_selected_provider = $('#model-select')?.selectedOptions?.[0]?.dataset?.provider || null;

const turns = [];
for (const prompt of PROMPTS) {
  const before = lastTerminal; const okBefore = completedOk;
  const input = $('#composer-input'); const form = $('#composer-form');
  if (!input || !form) { turns.push({ prompt, error: 'composer not found' }); break; }
  input.value = prompt;
  form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  const start = Date.now();
  let approved = 0;
  while (Date.now() - start < WAIT_MS) {
    await sleep(100);
    if (APPROVE && approvalsSeen.length > approved) {
      // Prefer the inline card button, fall back to the banner.
      const btn = window.document.querySelector('button[data-action="approve-approval"]') || $('#btn-approval-approve');
      if (btn) { btn.click(); approved++; }
    }
    if (lastTerminal > before) { await sleep(300); break; }
  }
  turns.push({ prompt, completed: completedOk > okBefore, terminal: lastTerminal > before, waited_ms: Date.now() - start, approved });
}

report.turns = turns;
report.active_session_after = window.HarnessApp?.activeSessionId || null;
const timeline = $('#timeline');
report.timeline_children = timeline ? timeline.children.length : null;
report.timeline_text = timeline ? timeline.textContent.replace(/\s+/g, ' ').trim() : null;
report.notices = [...window.document.querySelectorAll('#timeline .notice, #timeline [data-level], #timeline .timeline-notice')].map((n) => n.textContent.replace(/\s+/g, ' ').trim()).slice(0, 10);
report.status_text = txt('#status-text') || null;
if (SETTINGS_PROBE && window.HarnessApp?.openSettings) {
  // The settings panel is schema-driven (`settings.schema` → group tabs); the old ui_smoke lane checked this.
  window.HarnessApp.openSettings();
  await sleep(1500);
  report.settings_groups = [...window.document.querySelectorAll('#settings-content [data-group]')].map((b) => b.textContent.replace(/\s+/g, ' ').trim());
  report.settings_modal_hidden = $('#settings-modal')?.classList.contains('hidden') ?? null; // stylesheets are not fetched in this lane; the cascade is covered by tests/frontends/test_ui_coherence.py
  window.HarnessApp.closeSettings?.();
}
report.console_errors = consoleLog.filter((c) => c.startsWith('[error]') || c.startsWith('[jsdomError]')).slice(0, 10);
report.sse_events = sseSeen;
report.submit_bodies = submitBodies;
report.fetch_log = fetchLog;
report.console_tail = consoleLog.slice(-15);
console.log(JSON.stringify(report));
window.HarnessApp?.events?.disconnect?.();
process.exit(0);
