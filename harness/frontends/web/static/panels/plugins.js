/**
 * The Plugins & Integrations pane — the Studio's answer to "what is actually installed here?".
 *
 * The kernel discovers every plugin under `harness/plugins/...` and the client used to show none of them, so a
 * user could not tell a bridge from a hook from a policy engine without reading the tree. `plugins.list` is the
 * single source for this pane: name, tier, capability family, whether the user switched it off, how many settings
 * it brings, which settings tab they are on, and whether it declares panels or record renderers. Nothing is
 * hardcoded here — not the plugin list, not the families, not which ones have settings — so a plugin shows up in
 * this pane the moment the kernel discovers it, and disappears when it is disabled at the next start.
 *
 * The two switches are `data-command` attributes, dispatched by `CommandBinder` (WS-05b) — the pane registers
 * no handler for them. They are written as literals on separate branches rather than interpolated from an
 * expression because `harness catalog gen` reads the binding out of *this* source and `harness catalog --check`
 * fails the build when a `data-command` names a command the dispatcher does not have. Enabling or disabling
 * writes `kernel.plugins.disabled` for the next start; the header says so instead of pretending the toggle is
 * live, because nothing hot-unloads a plugin mid-session.
 */

const UNKNOWN_FAMILY = 'Other';

export default {
  async render(ctx) {
    const host = ctx.host;
    if (!host) return;
    bindOnce(ctx);

    let rows = [];
    try {
      rows = (await ctx.api.command('plugins.list', {})) || [];
    } catch (err) {
      host.innerHTML = `<div class="plugin-empty">The kernel could not list its plugins: ${ctx.escape(
        String(err?.message || err)
      )}</div>`;
      return;
    }

    const loaded = rows.filter((row) => row.loaded).length;
    const withSettings = rows.filter((row) => row.settings_count > 0).length;
    const withUi = rows.filter((row) => (row.panels || []).length || (row.record_renderers || []).length).length;
    const head = `
      <div class="plugin-summary-bar">
        <span class="plugin-count"><strong>${loaded}</strong> of ${rows.length} loaded</span>
        <span class="plugin-count">${withSettings} with settings</span>
        <span class="plugin-count">${withUi} contributing UI</span>
        <span class="plugin-restart-note">
          Enable / disable writes <code>kernel.plugins.disabled</code> — it takes effect the next time the
          daemon starts.
        </span>
      </div>`;

    if (!rows.length) {
      host.innerHTML = `${head}<div class="plugin-empty">The kernel discovered no plugins at all.</div>`;
      return;
    }

    const groups = new Map();
    for (const row of [...rows].sort((a, b) => a.name.localeCompare(b.name))) {
      const family = row.family || UNKNOWN_FAMILY;
      if (!groups.has(family)) groups.set(family, []);
      groups.get(family).push(row);
    }

    const sections = [...groups.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([family, items]) => {
        const on = items.filter((row) => row.loaded).length;
        return `
          <section class="plugin-family">
            <h3 class="plugin-family-title">
              ${ctx.escape(family)}
              <span class="plugin-family-meta">${on}/${items.length} loaded</span>
            </h3>
            <div class="plugin-grid">${items.map((row) => card(ctx, row)).join('')}</div>
          </section>`;
      })
      .join('');

    host.innerHTML = head + sections;
  },
};

function card(ctx, row) {
  const name = ctx.escape(row.name);
  const args = ctx.escapeAttr(JSON.stringify({ name: row.name }));
  const facts = [];
  facts.push(fact('tier', row.tier || '—'));
  facts.push(fact('provides', (row.provides || []).join(', ') || '—'));
  if (row.settings_count > 0) {
    const where = row.settings_group ? ` on ${ctx.escape(row.settings_group)}` : '';
    facts.push(fact('settings', `${row.settings_count} setting${row.settings_count === 1 ? '' : 's'}${where}`));
  }
  const panels = (row.panels || []).length;
  const renderers = (row.record_renderers || []).length;
  if (panels || renderers || row.client_module) {
    const parts = [];
    if (panels) parts.push(`${panels} panel${panels === 1 ? '' : 's'}`);
    if (renderers) parts.push(`${renderers} renderer${renderers === 1 ? '' : 's'}`);
    if (row.client_module) parts.push('client module');
    facts.push(fact('ui', parts.join(' · ')));
  }
  // Two facts, two places. The badge is what is *running*; the switch is what the user *asked for*, so the
  // button flips as soon as the write lands even though `loaded` cannot move until the daemon restarts.
  const { slug, label } = stateOf(row);
  const toggle = row.disabled
    ? `<button type="button" class="plugin-toggle" data-command="plugins.enable" data-args="${args}">Enable</button>`
    : `<button type="button" class="plugin-toggle" data-command="plugins.disable" data-args="${args}">Disable</button>`;
  const configure = row.settings_group
    ? `<button type="button" class="plugin-configure" data-configure="${ctx.escapeAttr(row.settings_group)}">Configure</button>`
    : '';

  return `
    <article class="plugin-card" data-plugin="${name}" data-state="${slug}">
      <header class="plugin-card-head">
        <h4 class="plugin-name font-mono">${name}</h4>
        <span class="plugin-state" data-state="${slug}">${label}</span>
      </header>
      <p class="plugin-blurb">${ctx.escape(row.summary || 'No summary in this manifest.')}</p>
      <dl class="plugin-facts">${facts.join('')}</dl>
      <footer class="plugin-actions">${toggle}${configure}</footer>
    </article>`;
}

/** `disabled` is the user's intent, `loaded` is this session's reality; a card must not hide the gap. */
function stateOf(row) {
  if (row.disabled) return { slug: row.loaded ? 'off-next' : 'off', label: row.loaded ? 'off next start' : 'off' };
  // Not loaded while the user has it enabled: could be a fresh Enable waiting for a restart, or a plugin whose
  // entry point or `setup()` failed at boot. The row does not guess which — a load failure is only on the event
  // bus, never in `plugins.list` — so it says the true thing it knows and leaves the cause to the log.
  if (!row.loaded) return { slug: 'unloaded', label: 'not loaded' };
  return { slug: 'loaded', label: 'loaded' };
}

function fact(label, value) {
  return `<div class="plugin-fact"><dt>${label}</dt><dd>${value}</dd></div>`;
}

/**
 * The two things this pane cannot express as a `data-command`: opening the settings modal on one plugin's tab,
 * and repainting after a toggle (whose result the binder announces on `harness:command-done`).
 *
 * The listeners sit on the host, which survives every re-render, so they are installed once — `dataset.bound`
 * is the guard. Nothing here knows a settings tab name: `data-configure` carries the group the *kernel*
 * assigned to that plugin's schema, and `ctx.select` hands it to the app.
 */
function bindOnce(ctx) {
  const host = ctx.host;
  if (host.dataset.bound === '1') return;
  host.dataset.bound = '1';
  host.addEventListener('click', (event) => {
    const button = event.target.closest?.('[data-configure]');
    if (!button) return;
    ctx.select(button.getAttribute('data-configure'));
  });
  host.addEventListener('harness:command-done', () => {
    // The setting is written; the rows' `loaded` state will not move until the daemon restarts, but the
    // pending `disabled` flag and the button's label must change now or the click looks like it did nothing.
    ctx.app.panels?.refresh('plugins');
  });
}
