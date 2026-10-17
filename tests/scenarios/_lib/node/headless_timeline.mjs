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
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import { pathToFileURL } from 'node:url';

const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const STATIC = process.env.STATIC || path.resolve('harness/frontends/web/static');
const PROMPTS = process.env.PROMPTS ? JSON.parse(process.env.PROMPTS) : [process.env.PROMPT || 'hello'];
const WAIT_MS = parseInt(process.env.WAIT_MS || '6000', 10);
const NEW_SESSION = process.env.NEW_SESSION === '1';
const RELOAD = process.env.RELOAD === '1'; // simulate a page refresh on an existing session: no clicks, no prompts
const APPROVE = process.env.APPROVE === '1';
const QUESTION_ANSWERS = process.env.QUESTION_ANSWERS ? JSON.parse(process.env.QUESTION_ANSWERS) : [];
const MODEL = process.env.MODEL || '';
const MODEL_PROVIDER = process.env.MODEL_PROVIDER || '';
const SETTINGS_PROBE = process.env.SETTINGS_PROBE === '1';
const PLUGINS_PROBE = process.env.PLUGINS_PROBE === '1';
const PLUGINS_TOGGLE = process.env.PLUGINS_TOGGLE === '1';
const PLUGINS_DISABLE_CARD = process.env.PLUGINS_DISABLE_CARD || ''; // click one card's switch, then restart on it // opt-in: click a real switch, then click it back

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
const WINDOW_GLOBALS = ['HTMLElement', 'Element', 'Node', 'Event', 'CustomEvent', 'KeyboardEvent', 'MouseEvent', 'FormData', 'localStorage', 'sessionStorage', 'getComputedStyle', 'URLSearchParams', 'DOMParser'];
// Node ships its own `Event`/`CustomEvent`/`FormData`, and jsdom's `dispatchEvent` rejects a foreign instance
// ("parameter 1 is not of type 'Event'") — so the window's copies win for anything event- or DOM-shaped. A
// global left to Node's version makes the page's real code paths untestable rather than broken.
const FROM_WINDOW = new Set(['Event', 'CustomEvent', 'KeyboardEvent', 'MouseEvent', 'FormData', 'Element', 'Node', 'HTMLElement']);
for (const k of WINDOW_GLOBALS) {
  if (window[k] === undefined) continue;
  if (FROM_WINDOW.has(k) || g[k] === undefined) g[k] = window[k];
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

// `loadPluginModules()` normally relies on browser ESM importing the kernel-advertised HTTP URL. Node cannot
// import `http:` modules, so this DOM lane fetches those exact bytes and rewrites their shipped `/static/` imports
// to local file URLs. The app receives this hook only in the isolated test process; browsers keep native import().
const pluginModuleDir = await mkdtemp(path.join(os.tmpdir(), 'harness-plugin-ui-'));
g.__harnessPluginModuleLoader = async (entry) => {
  const response = await g.fetch(entry.url);
  if (!response.ok) throw new Error(`plugin module fetch failed: ${entry.url} -> ${response.status}`);
  const staticRoot = path.resolve(STATIC);
  const source = await response.text();
  const rewritten = source.replace(/(['"])\/static\/([^'"]+)\1/g, (_match, quote, relative) => {
    const target = path.resolve(staticRoot, relative);
    if (target !== staticRoot && !target.startsWith(`${staticRoot}${path.sep}`)) {
      throw new Error(`plugin import escaped the shipped static tree: ${relative}`);
    }
    return `${quote}${pathToFileURL(target).href}${quote}`;
  });
  const filename = path.join(pluginModuleDir, `${String(entry.plugin || 'plugin').replace(/[^a-z0-9_-]/gi, '_')}.mjs`);
  await writeFile(filename, rewritten, 'utf8');
  return import(pathToFileURL(filename).href);
};
window.__harnessPluginModuleLoader = g.__harnessPluginModuleLoader;

// EventSource polyfill with browser semantics: named frames -> addEventListener(type); unnamed -> onmessage.
const sseSeen = [];
let lastTerminal = 0; // count of turn.completed/error/cancelled frames observed on the wire
let completedOk = 0;  // count of turn.completed only
let approvalsSeen = [];
let questionsSeen = [];
let questionAnswersSubmitted = 0;
let questionWaitingSeen = false;
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
            if (env.type === 'question.asked') questionsSeen.push(env.data || {});
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
const securityBadge = $('#permission-mode-badge');
report.security_badge = securityBadge ? {
  text: securityBadge.textContent.replace(/\s+/g, ' ').trim(),
  state: securityBadge.dataset.state || null,
  title: securityBadge.title || '',
} : null;
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
    const waiting = $('#question-waiting');
    if (waiting && !waiting.classList.contains('hidden')) questionWaitingSeen = true;
    if (QUESTION_ANSWERS.length > questionAnswersSubmitted && questionsSeen[questionAnswersSubmitted]) {
      const questionEvent = questionsSeen[questionAnswersSubmitted];
      const card = [...window.document.querySelectorAll('.harness-card-question[data-interaction-id]')]
        .find((item) => item.dataset.interactionId === questionEvent.interaction_id);
      const form = card?.querySelector('form[data-question-form]');
      if (form) {
        const answerMap = QUESTION_ANSWERS[questionAnswersSubmitted] || {};
        for (const question of questionEvent.questions || []) {
          const answer = answerMap[question.id] || {};
          const optionId = typeof answer === 'string' ? answer : (answer.option_id || '');
          const radio = [...form.querySelectorAll('input[type="radio"][data-question-id]')]
            .find((input) => input.dataset.questionId === question.id && input.value === optionId);
          if (radio) radio.checked = true;
          const custom = [...form.querySelectorAll('input[data-question-custom-id]')]
            .find((input) => input.dataset.questionCustomId === question.id);
          if (custom && typeof answer === 'object') custom.value = answer.custom_text || '';
        }
        form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
        questionAnswersSubmitted++;
      }
    }
    if (lastTerminal > before) { await sleep(300); break; }
  }
  turns.push({ prompt, completed: completedOk > okBefore, terminal: lastTerminal > before, waited_ms: Date.now() - start, approved });
}

report.turns = turns;
report.questions_answered = questionAnswersSubmitted;
report.question_waiting_seen = questionWaitingSeen;
report.active_session_after = window.HarnessApp?.activeSessionId || null;
const timeline = $('#timeline');
report.timeline_children = timeline ? timeline.children.length : null;
report.timeline_text = timeline ? timeline.textContent.replace(/\s+/g, ' ').trim() : null;
report.notices = [...window.document.querySelectorAll('#timeline .notice, #timeline [data-level], #timeline .timeline-notice')].map((n) => n.textContent.replace(/\s+/g, ' ').trim()).slice(0, 10);
const presented = window.document.querySelector('#studio-content-area .deliverable-viewer');
const presentedFrame = presented?.querySelector('iframe');
report.deliverable_viewer = presented ? {
  kind: presented.dataset.kind || null,
  path: presented.dataset.path || null,
  url: presentedFrame ? new URL(presentedFrame.src).pathname : null,
  sandboxed: !!presentedFrame && presentedFrame.getAttribute('sandbox') === '',
} : null;
report.artifact_tab_active = window.document.querySelector('.studio-tabs .studio-tab.active')?.getAttribute('data-tab') === 'artifact';
report.status_text = txt('#status-text') || null;
if (SETTINGS_PROBE && window.HarnessApp?.openSettings) {
  // The settings panel is schema-driven (`settings.schema` → group tabs); the old ui_smoke lane checked this.
  window.HarnessApp.openSettings();
  await sleep(1500);
  report.settings_groups = [...window.document.querySelectorAll('#settings-content [data-group]')].map((b) => b.textContent.replace(/\s+/g, ' ').trim());
  const securityTab = [...window.document.querySelectorAll('#settings-content [data-group]')]
    .find((b) => b.textContent.replace(/\s+/g, ' ').trim() === 'Security & Sandboxing');
  securityTab?.click();
  await sleep(200);
  const presetControl = window.document.getElementById('input_kernel.security.preset');
  const extraReadableControl = window.document.getElementById('input_kernel.sandbox.extra_readable');
  report.security_settings_controls = {
    preset: presetControl ? { tag: presetControl.tagName.toLowerCase(), type: presetControl.getAttribute('data-setting-type') } : null,
    extra_readable: extraReadableControl ? { tag: extraReadableControl.tagName.toLowerCase(), type: extraReadableControl.getAttribute('data-setting-type') } : null,
  };
  report.settings_modal_hidden = $('#settings-modal')?.classList.contains('hidden') ?? null; // stylesheets are not fetched in this lane; the cascade is covered by tests/frontends/test_ui_coherence.py
  window.HarnessApp.closeSettings?.();
}
if (PLUGINS_PROBE && window.HarnessApp) {
  // The Plugins pane is the proof that `[ui].panels` is more than a declaration: the shell carries no
  // `data-tab="plugins"` and no host in `index.html`, so if the tab and the cards appear here, they were
  // mounted from `ui.contributions`. The static contract (tests/frontends/test_ui_contract.py) can only check
  // that the client *could* paint the panel; this is where it checks that it does.
  const doc = window.document;
  const click = async (el, ms = 700) => {
    if (!el) return;
    el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
    await sleep(ms);
  };
  report.tab_ids = [...doc.querySelectorAll('.studio-tabs [data-tab]')].map((el) => el.getAttribute('data-tab'));
  // Visit every tab the kernel declared and keep the text each one painted. A panel that mounts but renders
  // nothing (a module that failed to import, a command that 404s) is otherwise indistinguishable from a
  // working one in this report — and `dom_panel_text` is how a scenario proves a *plugin's* module ran.
  report.panel_texts = {};
  for (const id of report.tab_ids) {
    await click(doc.querySelector(`.studio-tabs [data-tab="${id}"]`), 500);
    const pane = doc.getElementById(`panel-${id}`);
    // Generous cap: these strings exist for a scenario's `dom_panel_text` check, not for a golden (the dom
    // report is never written into `golden/`), and the Plugins pane legitimately holds tens of cards — a short
    // slice made a real pane look like it was missing its own row.
    report.panel_texts[id] = pane ? pane.textContent.replace(/\s+/g, ' ').trim().slice(0, 20000) : null;
  }
  await click(doc.querySelector('.studio-tabs [data-tab="diffs"]'), 500);
  const diffButton = doc.querySelector('#panel-diffs .diff-file-button:not(:disabled)');
  if (diffButton) {
    await click(diffButton, 500);
    report.diff_preview = doc.querySelector('#panel-diffs .diff-file-content:not(.hidden)')?.textContent || '';
  }
  await click(doc.querySelector('.studio-tabs [data-tab="plugins"]'), 1200);
  const host = doc.getElementById('panel-plugins');
  report.plugins_host = !!host;
  report.plugin_cards = host ? host.querySelectorAll('.plugin-card').length : 0;
  report.plugin_families = host
    ? [...host.querySelectorAll('.plugin-family-title')].map((el) => el.textContent.replace(/\s+/g, ' ').trim())
    : [];
  report.plugin_toggles = host
    ? [...host.querySelectorAll('[data-command]')].slice(0, 3).map((el) => el.getAttribute('data-command'))
    : [];
  const configure = host ? host.querySelector('[data-configure]') : null;
  report.plugin_configure_target = configure ? configure.getAttribute('data-configure') : null;
  await click(configure, 1200);
  report.settings_open_after_configure = !doc.getElementById('settings-modal')?.classList.contains('hidden');
  report.settings_active_group = doc
    .querySelector('#settings-content [data-group].settings-tab-active')
    ?.textContent?.replace(/\s+/g, ' ')
    .trim() ?? null;
  window.HarnessApp.closeSettings?.();

  if (PLUGINS_TOGGLE) {
    // The `[data-command]` binding is this pane's only behaviour, so click one and check that the kernel's
    // answer comes back through the re-render: `Disable` -> `Enable` means the write landed and `plugins.list`
    // reported it. The loop clicks it back, leaving the daemon's settings as they were found.
    const name = host.querySelector('.plugin-card [data-command="plugins.disable"]')
      ?.closest('.plugin-card')
      ?.getAttribute('data-plugin');
    const stateOf = () => {
      const card = host.querySelector(`.plugin-card[data-plugin="${name}"]`);
      return {
        state: card?.getAttribute('data-state'),
        button: card?.querySelector('.plugin-toggle')?.textContent?.trim(),
        command: card?.querySelector('.plugin-toggle')?.getAttribute('data-command'),
      };
    };
    const clicked = [];
    for (let i = 0; i < 2 && name; i += 1) {
      const before = stateOf();
      await click(host.querySelector(`.plugin-card[data-plugin="${name}"] .plugin-toggle`), 1400);
      clicked.push({ before, after: stateOf() });
    }
    report.plugin_toggle = { name, clicked };
  }

  if (PLUGINS_DISABLE_CARD) {
    // The pane's switch is a `data-command` binding (no handler of its own): clicking it must reach the kernel,
    // repaint the card, and persist — `dom_restart_tabs_exclude` is then the proof that the restart applied it.
    const card = doc.querySelector(`.plugin-card[data-plugin="${PLUGINS_DISABLE_CARD}"]`);
    const button = card?.querySelector('[data-command]');
    const seen = { found: !!card, command: button?.getAttribute('data-command') || null, before: button?.textContent?.trim() || null };
    await click(button, 1400);
    const after = doc.querySelector(`.plugin-card[data-plugin="${PLUGINS_DISABLE_CARD}"]`);
    report.plugin_disable = {
      ...seen,
      after: after?.querySelector('[data-command]')?.textContent?.trim() || null,
      state: after?.getAttribute('data-state') || null,
    };
  }

  // Every URL `ui.contributions` advertises is fetched from inside the page. The loader above executes the
  // fetched module bytes and resolves their core imports to the shipped client files; this second request keeps
  // the advertised URL itself covered too (the WS-05b failure was a bad URL that 404'd).
  const modules = (window.HarnessApp?.ui?.client_modules) || [];
  report.client_module_urls = modules.map((entry) => entry.url);
  report.plugin_module_status = {};
  for (const entry of modules) {
    try {
      const res = await fetch(entry.url);
      report.plugin_module_status[entry.url] = res.status;
    } catch (err) {
      report.plugin_module_status[entry.url] = String(err?.message || err);
    }
  }
}
report.console_errors = consoleLog.filter((c) => c.startsWith('[error]') || c.startsWith('[jsdomError]')).slice(0, 10);
report.sse_events = sseSeen;
report.submit_bodies = submitBodies;
report.fetch_log = fetchLog;
report.console_tail = consoleLog.slice(-15);
await rm(pluginModuleDir, { recursive: true, force: true });
console.log(JSON.stringify(report));
window.HarnessApp?.events?.disconnect?.();
process.exit(0);
