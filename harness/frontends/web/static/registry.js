/**
 * The client's mount points (WS-05): who renders a record kind, which panels exist, and what a
 * `data-command` control does.
 *
 * Everything here is a *lookup the kernel answered*, not a list this file keeps. The app used to hard-code
 * both halves — a `switch` in `renderRecord` and a branch per studio tab — which meant a feature could add a
 * record kind or a panel and the client would silently show nothing. Now:
 *
 *   - a record kind is painted by a module under `renderers/`, or excused in `NO_RENDERER` with a reason;
 *   - a panel is declared in a manifest `[ui]` block (`harness/frontends/web/ui.toml` for these built-ins)
 *     and backed by a command the kernel says is dispatchable;
 *   - a control with `data-command` is dispatched by `CommandBinder`, so a binding to a command that does not
 *     exist is a static-parse failure, not a dead button.
 *
 * `tests/frontends/test_ui_contract.py` reads these exports' source, and `harness catalog --check --section ui`
 * fails on a mismatch, so the three rules cannot quietly drift apart.
 */

/**
 * Record kinds the timeline deliberately does not paint, each with the reason.
 *
 * The generic-node path below fires for anything *not* listed here, so a new record kind shows up as a line in
 * the timeline instead of vanishing — and the contract test fails until a renderer or an excuse with a reason
 * exists. Keep the reasons true: `turn.completed` is not "not implemented", it is a marker the turn block
 * already positions.
 */
export const NO_RENDERER = {
  'turn.completed':
    'a turn marker; the assistant record inside the turn is what the timeline shows, so painting the marker too would duplicate the turn',
  'turn.started':
    'a turn marker; `turn.started` is what opens the turn block live, and the records inside it are the history',
  'tool.call':
    'the call fact lives inside `assistant.message` (its tool_calls), which is what paints the card',
  'settings.changed':
    'a settings write is a notice at most; the settings panel re-reads the schema, so a card would be noise',
  'checkpoint.created':
    'bookkeeping for the checkpoint plugin; WS-07 renders checkpoints as a diff view, not as timeline rows',
  'job.started':
    'background jobs are WS-10 rows with no turn to attach to; the jobs panel will show them',
  'job.completed':
    'background jobs are WS-10 rows with no turn to attach to; the jobs panel will show them',
  'goal.created':
    'goal state belongs to the goal strip (WS-10), not to the conversation',
  'goal.completed':
    'goal state belongs to the goal strip (WS-10), not to the conversation',
};

/** Flattens a record's content blocks into text, the way every painter needs it. */
export function textContentOf(blocks) {
  return (blocks || []).filter((b) => b && b.type === 'text').map((b) => b.text).join('');
}

export class RendererRegistry {
  constructor({ excuses = NO_RENDERER } = {}) {
    this.renderers = new Map();
    this.excuses = excuses;
  }

  /** Registers one module per record kind (`renderers/user.message.js` → `user.message`). */
  register(kind, render) {
    if (this.renderers.has(kind)) throw new Error(`two renderers claim the record kind ${kind}`);
    if (Object.prototype.hasOwnProperty.call(this.excuses, kind)) {
      throw new Error(`${kind} is both excused and rendered — delete the excuse`);
    }
    this.renderers.set(kind, render);
  }

  has(kind) {
    return this.renderers.has(kind);
  }

  paintedKinds() {
    return [...this.renderers.keys()];
  }

  /** Kinds the kernel keeps in the log that this client neither paints nor excuses: a coverage hole. */
  gaps(kinds) {
    return (kinds || []).filter((kind) => !this.renderers.has(kind) && !Object.prototype.hasOwnProperty.call(this.excuses, kind));
  }

  /** One record in, DOM out. `app` is the owner of the shared painters (appendUserTurn, appendCard, …). */
  render(app, rec) {
    if (!rec || !rec.kind) return;
    const render = this.renderers.get(rec.kind);
    if (render) {
      render(app, rec);
      return;
    }
    if (Object.prototype.hasOwnProperty.call(this.excuses, rec.kind)) return;
    // The visible fallback is the point: an unpainted kind must not be silently swallowed by a client that
    // is older than the kernel it is talking to.
    app.appendNotice(`Unrendered record: ${rec.kind}`, 'warn');
    console.warn('[RendererRegistry] no renderer for record kind', rec.kind);
  }
}

/**
 * Panels: declared by a manifest, mounted by the shell.
 *
 * A panel is `{id, title, command, renderer, plugin}` from `ui.contributions`, plus — for the ones the client
 * ships code for — a module registered here. A declared panel *without* a module is not a bug: the generic
 * renderer renders its command's result, which is how a plugin gets a pane with no client-side JS.
 */
export class PanelRegistry {
  constructor({ app, panels = [] } = {}) {
    this.app = app;
    this.panels = new Map();
    for (const panel of panels) this.panels.set(panel.id, { ...panel, module: null });
    this.active = null;
  }

  register(id, module) {
    const panel = this.panels.get(id);
    if (!panel) {
      console.warn(`[PanelRegistry] "${id}" has a client module but no declaration — declare it in [ui].panels`);
      return;
    }
    panel.module = module;
  }

  declaredIds() {
    return [...this.panels.keys()];
  }

  hostFor(id) {
    const panel = this.panels.get(id);
    const hostId = panel?.module?.host || `panel-${id}`;
    return document.getElementById(hostId);
  }

  /** Exactly one panel visible, and its content refreshed. */
  async activate(id, { silent = false } = {}) {
    const panel = this.panels.get(id);
    if (!panel) return false;
    for (const known of this.panels.values()) {
      const host = this.hostFor(known.id);
      if (host) host.classList.toggle('hidden', known.id !== id);
    }
    document.querySelectorAll('.studio-tab').forEach((tab) => {
      tab.classList.toggle('active', tab.getAttribute('data-tab') === id);
    });
    this.active = id;
    if (!silent) await renderSafely(this, panel);
    return true;
  }

  /** Re-renders one panel without changing which one is visible (the app's refresh path). */
  async refresh(id) {
    const panel = this.panels.get(id);
    if (!panel?.module?.render) return false;
    await renderSafely(this, panel);
    return true;
  }

  /**
   * Mounts what the kernel declared: a tab and a host per panel, adding only what the shell lacks.
   *
   * The built-in three are already in `index.html`, so this is a no-op for them; a plugin panel gets its tab
   * and its `<div id="panel-<id>">` here, which is the whole reason `[ui].panels` can be *added* to without a
   * pull request against the shell. A panel that names a `command` gets the generic renderer for its
   * `renderer` — no client module required — and a refresh button bound through `data-command`.
   */
  mount() {
    const strip = document.querySelector('.studio-tabs');
    const viewport = document.querySelector('.studio-viewport');
    const present = new Set([...document.querySelectorAll('[data-tab]')].map((el) => el.getAttribute('data-tab')));
    for (const panel of this.panels.values()) {
      if (!present.has(panel.id) && strip) {
        const tab = document.createElement('div');
        tab.className = 'studio-tab';
        tab.setAttribute('data-tab', panel.id);
        tab.textContent = panel.title || panel.id;
        strip.appendChild(tab);
      }
      if (!panel.module?.host && viewport && !document.getElementById(`panel-${panel.id}`)) {
        const host = document.createElement('div');
        host.id = `panel-${panel.id}`;
        host.className = 'panel-view hidden';
        viewport.appendChild(host);
      }
      if (!panel.module) this.mountGeneric(panel);
    }
  }

  /** A declared panel with no module: fetch its command, render the result with the named built-in renderer. */
  mountGeneric(panel) {
    const render = GENERIC_RENDERERS[panel.renderer] || GENERIC_RENDERERS.list;
    panel.module = {
      host: `panel-${panel.id}`,
      async render(ctx) {
        if (!ctx.host) return;
        // Only send what the command accepts: the kernel lists the argument names per panel (`accepts`), so a
        // panel over a session-less command works and one over a session-scoped command still scopes itself.
        const scoped = (panel.accepts || []).includes('session_id') && ctx.app.activeSessionId;
        const result = await ctx.api.command(panel.command, scoped ? { session_id: ctx.app.activeSessionId } : {});
        const rows = Array.isArray(result) ? result : (result?.rows ?? result?.records ?? result?.items ?? []);
        const columns = Array.isArray(result?.columns) ? result.columns : inferColumns(rows);
        render(ctx.host, rows, columns);
        const refresh = ctx.host.querySelector('[data-refresh]');
        if (refresh && !refresh.dataset.command) refresh.dataset.command = panel.command;
      },
    };
  }

  /** What a panel module may use: the API, the host element, and the app for shared state. */
  contextFor(panel) {
    return {
      app: this.app,
      api: this.app.api,
      panel,
      host: this.hostFor(panel.id),
      escape: (value) => escapeText(value),
      escapeAttr: (value) => escapeAttr(value),
      select: (value) => this.app.handlePanelSelect?.(panel.id, value),
    };
  }

  /** The panel currently visible, if any (the settings modal and the tabs share this registry). */
  activePanel() {
    return this.panels.get(this.active) || null;
  }
}

/** The four renderers a `[ui].panels[].renderer` may name, for a panel with no module of its own. */
export const GENERIC_RENDERERS = {
  table: (container, rows, columns) => {
    const head = (columns || []).map((c) => `<th class="panel-th">${escapeText(c)}</th>`).join('');
    const body = (rows || [])
      .map((row) => `<tr class="panel-row">${(columns || Object.keys(row)).map((c) => `<td class="panel-td">${escapeText(row[c])}</td>`).join('')}</tr>`)
      .join('');
    container.innerHTML = `<table class="panel-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  },
  list: (container, rows, columns) => {
    const items = (rows || [])
      .map((row) => `<div class="panel-item">${escapeText((columns || Object.keys(row)).map((c) => row[c]).join(' · '))}</div>`)
      .join('');
    container.innerHTML = items || `<div class="panel-empty text-xs">Nothing to show yet.</div>`;
  },
  markdown: (container, rows) => {
    const body = (rows || []).map((row) => row?.text ?? row?.content ?? '').filter(Boolean).join('\n\n');
    container.innerHTML = `<div class="panel-markdown">${escapeText(body)}</div>`;
  },
  tree: (container, rows) => {
    const items = (rows || []).map((row) => `<li class="panel-node">${escapeText(row?.name ?? row?.id ?? '')}</li>`).join('');
    container.innerHTML = `<ul class="panel-tree">${items}</ul>`;
  },
};

async function renderSafely(registry, panel) {
  const render = panel.module?.render;
  if (!render) return;
  try {
    await render(registry.contextFor(panel));
  } catch (err) {
    console.warn(`[PanelRegistry] ${panel.id} failed to render:`, err);
  }
}

function inferColumns(rows) {
  const first = (rows || []).find((row) => row && typeof row === 'object');
  return first ? Object.keys(first).slice(0, 6) : [];
}

/** Escape a value going into an HTML *attribute* (`escapeText` only defuses text nodes). */
export function escapeAttr(value) {
  // Plugin-supplied strings reach `data-args` here, and CSP has no unsafe-inline to fall back on: a bare `"`
  // would end the attribute and turn a data value into markup. Values are usually `JSON.stringify` output, so
  // quotes are the real risk — but nothing from a command response is trusted.
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeText(value) {
  // The client sanitises its own strings before they reach `innerHTML`, and CSP has no unsafe-inline to fall
  // back on; keep the escaping here so a generic renderer never trusts a plugin command's payload.
  const div = document.createElement('div');
  div.textContent = value == null ? '' : String(value);
  return div.innerHTML;
}

/**
 * `[data-command]` controls, dispatched by one delegated listener.
 *
 * A control that names a command needs no handler in `app.js`: `data-command="sessions.create"` plus optional
 * `data-args='{"json":"here"}'`. Only static bindings belong here — a control that needs a runtime id (an
 * approval's, a session's) or that must validate first (the composer) keeps its own handler in the app, and
 * the catalogue's `data_command_targets` is what keeps the two lists honest.
 */
export class CommandBinder {
  constructor({ api, app }) {
    this.api = api;
    this.app = app;
  }

  bind(root = document) {
    if (this._bound) return;
    this._bound = true;
    root.addEventListener('click', async (event) => {
      const control = event.target?.closest?.('[data-command]');
      if (!control) return;
      event.preventDefault();
      const name = control.getAttribute('data-command');
      let args = {};
      const raw = control.getAttribute('data-args');
      if (raw) {
        try {
          args = JSON.parse(raw);
        } catch (err) {
          console.warn(`[CommandBinder] ${name}: bad data-args JSON`, err);
          this.app?.appendNotice?.(`Malformed binding for ${name}`, 'error');
          return;
        }
      }
      if (args.session_id === '@active') args.session_id = this.app?.activeSessionId || undefined;
      control.disabled = true;
      let result;
      try {
        result = await this.api.command(name, args);
      } catch (err) {
        console.warn(`[CommandBinder] ${name} failed:`, err);
        this.app?.appendNotice?.(`${name} failed: ${err.message}`, 'error');
        control.disabled = false;
        return;
      }
      control.disabled = false;
      // Announce the success *outside* the command's try: a listener that throws (or a control a re-render has
      // already detached, so the event has nowhere to bubble) must never be reported to the user as a failed
      // command — the write went through, and saying otherwise is how a working toggle looks broken.
      try {
        const target = control.isConnected ? control : document;
        target.dispatchEvent(new CustomEvent('harness:command-done', { bubbles: true, detail: { command: name, result } }));
      } catch (err) {
        console.warn(`[CommandBinder] could not announce ${name}:`, err);
      }
    });
  }
}
