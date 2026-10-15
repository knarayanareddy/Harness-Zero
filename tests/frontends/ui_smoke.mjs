// Headless DOM smoke test for the shipped Harness-Zero web UI.
// Exercises real DOM wiring, fetch, SSE parsing, and turn streaming.

import { JSDOM, VirtualConsole } from 'jsdom';
import path from 'node:path';
import assert from 'node:assert';

const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const PROMPT = process.env.PROMPT || 'What does app.py do?';
const WAIT_MS = parseInt(process.env.WAIT_MS || '7000', 10);

const consoleLog = [];
const vc = new VirtualConsole();
for (const lvl of ['log', 'warn', 'error', 'info', 'debug']) {
  vc.on(lvl, (...a) => consoleLog.push(`[${lvl}] ${a.map(String).join(' ')}`.slice(0, 300)));
}
vc.on('jsdomError', (e) => consoleLog.push(`[jsdomError] ${String(e.message || e).slice(0, 300)}`));

// 1. Fetch the page exactly as the browser would (token injected server side)
const res = await fetch(`${BASE}/`);
if (!res.ok) {
  console.error(`Failed to fetch ${BASE}/: ${res.status}`);
  process.exit(1);
}
const html = await res.text();
const setCookie = res.headers.get('set-cookie') || '';
const cookie = setCookie.split(';')[0];

const dom = new JSDOM(html, { url: `${BASE}/`, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc });
const { window } = dom;

// 2. Globals the modules expect
const g = globalThis;
g.window = window; g.document = window.document;
try { Object.defineProperty(g, 'navigator', { value: window.navigator, configurable: true }); } catch (e) {}
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
  const r = await nodeFetch(abs, opts);
  fetchLog.push(`${opts.method || 'GET'} ${new URL(abs).pathname} -> ${r.status}`);
  return r;
};
window.fetch = g.fetch;

// EventSource polyfill over fetch (text/event-stream)
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
          const raw = data.join('\n');
          (globalThis.__sse ||= []).push(type);
          const ev = { type, data: raw, lastEventId: id };
          if (type === 'message') this.onmessage?.(ev);
          for (const fn of (this._listeners[type] || [])) fn(ev);
        }
      }
    } catch (e) {
      if (!this._closed) this.onerror?.(e);
    }
  }
}
g.EventSource = ES; window.EventSource = ES;

// 3. Dynamically import app.js and fire DOMContentLoaded
const staticDir = process.env.STATIC || path.resolve('harness/frontends/web/static');
await import(`file://${path.join(staticDir, 'app.js')}`);
window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(2500);

const $ = (sel) => window.document.querySelector(sel);
const txt = (sel) => ($(sel)?.textContent || '').trim();

console.log('[UI Smoke] Checking initial boot state...');
assert(window.HarnessApp, 'window.HarnessApp must be initialized');
assert(txt('#status-text') === 'Connected', 'Status indicator must be Connected');

// 4. Submit prompt via composer
const input = $('#composer-input');
const form = $('#composer-form');
assert(input && form, 'Composer input and form must exist');

input.value = PROMPT;
form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));

console.log(`[UI Smoke] Waiting for turn execution (${WAIT_MS}ms)...`);
await sleep(WAIT_MS);

const timeline = $('#timeline');
assert(timeline, '#timeline must exist');
const timelineText = timeline.textContent.replace(/\s+/g, ' ').trim();
console.log('[UI Smoke] Timeline text:', timelineText);

assert(timeline.children.length >= 2, 'Timeline must contain user turn and assistant response');
assert(timelineText.includes(PROMPT), 'Timeline must contain user prompt');

// Check tool card rendering
const toolCards = window.document.querySelectorAll('.tool-card, .tool-block, [data-call-id]');
console.log(`[UI Smoke] Tool cards rendered: ${toolCards.length}`);
assert(toolCards.length >= 1, 'Timeline must render at least one tool execution card');

// 5. Open Settings and verify schema-driven panel
console.log('[UI Smoke] Opening settings modal...');
window.HarnessApp.openSettings();
await sleep(1500);

const settingsContent = $('#settings-content');
assert(settingsContent, '#settings-content must exist');
const schemaTabs = [...window.document.querySelectorAll('#settings-content [data-group]')].map((b) => b.textContent.trim());
console.log('[UI Smoke] Schema group tabs:', schemaTabs);
assert(schemaTabs.includes('Models & Providers'), 'Settings must include Models & Providers tab');
assert(schemaTabs.includes('Secrets'), 'Settings must include Secrets tab');

const deadPages = window.document.querySelectorAll('#settings-content .settings-page');
assert(deadPages.length === 0, 'No static settings mockup pages may exist');

const deadSidebar = window.document.querySelectorAll('[data-stab]');
assert(deadSidebar.length === 0, 'No dead static sidebar tabs may exist');

// Check console for fatal errors
const fatalErrors = consoleLog.filter((c) => c.startsWith('[error]') || c.startsWith('[jsdomError]'));
assert(fatalErrors.length === 0, `Fatal console errors found: ${JSON.stringify(fatalErrors)}`);

window.HarnessApp?.events?.disconnect();
console.log('✅ [UI Smoke Test Passed Successfully]');
process.exit(0);
