/**
 * Schema-driven Settings Panel for Harness-Zero Studio (ES Module).
 * Renders tab groups according to x-ui.group and widgets based on JSON Schema (§11, WEB-05).
 * Secrets display has() status and allow masked writes only.
 * Operates without inline event handlers (CSP script-src 'self' compliant).
 */

import { escapeHtml } from './cards.js';

export class SettingsPanel {
  constructor(api, containerElement) {
    this.api = api;
    this.container = containerElement;
    this.schema = null;
    this.values = {};
    this.activeGroup = null; // no tab of the client's own: `settings.schema`'s `groups` picks the first one
    this.statusMessage = null;
    this.loadError = null;

    if (this.container) {
      this._bindDelegatedEvents();
    }
  }

  _bindDelegatedEvents() {
    this.container.addEventListener('click', (e) => {
      const tabBtn = e.target.closest('[data-group]');
      if (tabBtn) {
        const group = tabBtn.getAttribute('data-group');
        if (group) this.setGroup(group);
        return;
      }

      const saveSecretBtn = e.target.closest('[data-action="save-secret"]');
      if (saveSecretBtn) {
        const key = saveSecretBtn.getAttribute('data-secret-key');
        if (key) this.saveSecret(key);
      }
    });

    this.container.addEventListener('change', (e) => {
      const input = e.target.closest('[data-setting-path]');
      if (!input) return;
      const path = input.getAttribute('data-setting-path');
      const type = input.getAttribute('data-setting-type');
      let val;
      if (type === 'boolean') {
        val = input.checked;
      } else if (type === 'number') {
        val = parseFloat(input.value);
        if (isNaN(val)) return;
      } else {
        val = input.value;
      }
      this.saveSetting(path, val);
    });
  }

  async load() {
    try {
      try {
        const remoteSchema = await this.api.command('settings.schema');
        if (remoteSchema && remoteSchema.properties) {
          this.schema = remoteSchema;
          this.loadError = null;
        }
      } catch (err) {
        console.warn('[SettingsPanel] Remote schema fetch failed:', err);
      }

      if (!this.schema || !this.schema.properties) {
        // No client-side copy of the settings surface: the kernel's `settings.schema` command is the
        // only source (WS-02). If it is unreachable the panel says so instead of rendering a stale form.
        this.schema = { groups: [], properties: {} };
        this.loadError = 'Settings schema unavailable — the daemon did not answer `settings.schema`.';
        this.render();
        return;
      }

      // Which tab is open is the kernel's answer too: a remembered or requested group that no longer exists (no
      // provider plugin loaded, a plugin disabled, a renamed tab) would otherwise render an empty pane over 40
      // real settings. `groups[0]` is the schema's own first choice, ordered by `x-ui.order`.
      if (!this.schema.groups.includes(this.activeGroup)) {
        this.activeGroup = this.schema.groups[0] || null;
      }

      // Fetch active values
      for (const key of Object.keys(this.schema.properties)) {
        try {
          if (key.startsWith('secrets.')) {
            const has = await this.api.getSettings(`has_secret.${key.replace('secrets.', '')}`);
            this.values[key] = Boolean(has);
          } else {
            const val = await this.api.getSettings(key, this.schema.properties[key].default);
            this.values[key] = val;
          }
        } catch {
          this.values[key] = this.schema.properties[key].default;
        }
      }

      this.render();
    } catch (err) {
      console.error('[SettingsPanel] Failed to load settings:', err);
      if (this.container) {
        this.container.innerHTML = `<div class="p-4 text-red-400">Failed to load settings: ${escapeHtml(err.message)}</div>`;
      }
    }
  }

  setGroup(groupName) {
    // A request for a tab the schema does not offer is ignored rather than honoured with an empty pane (the
    // onboarding banner asks for `Models & Providers`, which a daemon with no provider plugin has not got).
    if (this.schema?.groups?.length && !this.schema.groups.includes(groupName)) return;
    this.activeGroup = groupName;
    this.render();
  }

  showNotice(msg, type = 'info') {
    this.statusMessage = { msg, type };
    this.render();
    setTimeout(() => {
      this.statusMessage = null;
      this.render();
    }, 4000);
  }

  render() {
    if (!this.container) return;

    if (this.loadError) {
      this.container.innerHTML = `<div class="p-4 text-xs border rounded bg-red-950/80 text-red-200 border-red-800">${escapeHtml(this.loadError)}</div>`;
      return;
    }

    const groups = this.schema.groups || [];
    let tabsHtml = '';
    for (const g of groups) {
      const activeCls = g === this.activeGroup ? 'settings-tab-active bg-blue-600 text-white' : 'settings-tab-btn text-gray-400 hover:text-white hover:bg-gray-800';
      tabsHtml += `
        <button type="button" data-group="${escapeHtml(g)}" class="px-3 py-1.5 rounded text-xs font-medium transition ${activeCls}">
          ${escapeHtml(g)}
        </button>
      `;
    }

    let statusHtml = '';
    if (this.statusMessage) {
      const colorCls = this.statusMessage.type === 'error' ? 'bg-red-950/80 text-red-200 border-red-800' : 'bg-emerald-950/80 text-emerald-200 border-emerald-800';
      statusHtml = `
        <div class="p-2 mb-3 rounded text-xs border ${colorCls} flex items-center justify-between">
          <span>${escapeHtml(this.statusMessage.msg)}</span>
        </div>
      `;
    }

    // One block per owner inside the tab (WS-05 follow-up). `settings.schema` says which plugin a control belongs
    // to (`x-ui.owner`), so a tab groups like *behaviour* — hooks under `Hooks`, bridges under
    // `Bridges & Integrations` — while a heading names the integration you are actually configuring. Kernel and
    // secret properties own no plugin and render unheaded, exactly as before; the client invents no tab name of
    // its own, because `tests/frontends/test_ui_contract.py` fails the moment it does.
    let fieldsHtml = '';
    let block = '';
    let blockOwner = null;
    const flushBlock = () => {
      if (!block) return;
      fieldsHtml += blockOwner
        ? `<div class="settings-owner">
            <div class="settings-owner-head">
              <span class="settings-owner-title">${escapeHtml(blockOwner.title)}</span>
              ${blockOwner.summary ? `<span class="settings-owner-summary">${escapeHtml(blockOwner.summary)}</span>` : ''}
            </div>
            <div class="settings-owner-fields">${block}</div>
          </div>`
        : block;
      block = '';
    };
    for (const [key, prop] of Object.entries(this.schema.properties || {})) {
      const ui = prop['x-ui'] || {};
      if (ui.group !== this.activeGroup) continue;

      const owner = ui.owner ? { id: ui.owner, title: ui.owner_title || ui.owner, summary: ui.owner_summary || '' } : null;
      const ownerChanged = (owner?.id || null) !== (blockOwner?.id || null);
      if (ownerChanged) {
        flushBlock();
        blockOwner = owner;
      }

      const title = escapeHtml(prop.title || key);
      const widget = ui.widget || (prop.type === 'boolean' ? 'boolean' : 'text');
      const val = this.values[key];

      if (widget === 'secret') {
        const isSet = Boolean(val);
        const badge = isSet
          ? '<span class="text-xs px-2 py-0.5 rounded bg-emerald-900/60 text-emerald-300 font-mono">Configured ✓</span>'
          : '<span class="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400 font-mono">Not Set</span>';

        block += `
          <div class="setting-item flex flex-col gap-1 py-3 border-b border-gray-800">
            <div class="flex items-center justify-between">
              <label class="text-sm font-medium text-gray-200">${title}</label>
              ${badge}
            </div>
            <div class="flex gap-2 mt-1">
              <input type="password" id="input_${escapeHtml(key)}" placeholder="Enter new secret value to update" class="flex-1 px-3 py-1.5 rounded bg-gray-900 border border-gray-700 text-xs text-white" />
              <button type="button" data-action="save-secret" data-secret-key="${escapeHtml(key)}" class="px-3 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-xs font-semibold text-white">Save</button>
            </div>
          </div>
        `;
      } else if (widget === 'boolean') {
        block += `
          <div class="setting-item flex items-center justify-between py-3 border-b border-gray-800">
            <label class="text-sm font-medium text-gray-200">${title}</label>
            <input type="checkbox" id="input_${escapeHtml(key)}" data-setting-path="${escapeHtml(key)}" data-setting-type="boolean" ${val ? 'checked' : ''} class="rounded bg-gray-900 border-gray-700 h-4 w-4 text-blue-600 focus:ring-blue-500" />
          </div>
        `;
      } else if (widget === 'select') {
        const options = (prop.enum || []).map(opt => {
          const selected = String(opt) === String(val) ? 'selected' : '';
          return `<option value="${escapeHtml(opt)}" ${selected}>${escapeHtml(opt)}</option>`;
        }).join('');

        block += `
          <div class="setting-item flex flex-col gap-1 py-3 border-b border-gray-800">
            <label class="text-sm font-medium text-gray-200">${title}</label>
            <select id="input_${escapeHtml(key)}" data-setting-path="${escapeHtml(key)}" data-setting-type="string" class="px-3 py-1.5 rounded bg-gray-900 border border-gray-700 text-xs text-white">
              ${options}
            </select>
          </div>
        `;
      } else if (widget === 'number') {
        block += `
          <div class="setting-item flex flex-col gap-1 py-3 border-b border-gray-800">
            <label class="text-sm font-medium text-gray-200">${title}</label>
            <input type="number" step="0.5" id="input_${escapeHtml(key)}" data-setting-path="${escapeHtml(key)}" data-setting-type="number" value="${escapeHtml(val ?? '')}" class="px-3 py-1.5 rounded bg-gray-900 border border-gray-700 text-xs text-white" />
          </div>
        `;
      } else {
        block += `
          <div class="setting-item flex flex-col gap-1 py-3 border-b border-gray-800">
            <label class="text-sm font-medium text-gray-200">${title}</label>
            <input type="text" id="input_${escapeHtml(key)}" data-setting-path="${escapeHtml(key)}" data-setting-type="string" value="${escapeHtml(val ?? '')}" class="px-3 py-1.5 rounded bg-gray-900 border border-gray-700 text-xs text-white" />
          </div>
        `;
      }
    }
    flushBlock();

    this.container.innerHTML = `
      <div class="settings-modal-content flex flex-col h-full">
        ${statusHtml}
        <div class="flex items-center gap-1 pb-3 mb-3 border-b border-gray-800 overflow-x-auto">
          ${tabsHtml}
        </div>
        <div class="settings-fields-body flex-1 overflow-y-auto pr-1">
          ${fieldsHtml || '<div class="text-gray-500 text-sm py-4">No settings configured in this group.</div>'}
        </div>
      </div>
    `;
  }

  async saveSetting(path, value) {
    try {
      this.values[path] = value;
      await this.api.setSettings(path, value);
      this.showNotice(`Setting '${path}' saved`, 'info');
    } catch (err) {
      console.error(`Error saving setting '${path}':`, err);
      this.showNotice(`Error saving setting '${path}': ${err.message}`, 'error');
    }
  }

  async saveSecret(key) {
    const input = document.getElementById(`input_${key}`);
    if (!input || !input.value) return;
    const secretKey = key.replace('secrets.', '');
    try {
      await this.api.command('settings.set', { path: `secrets.${secretKey}`, value: input.value });
      input.value = '';
      this.values[key] = true;
      this.render();
      this.showNotice(`Secret '${secretKey}' saved`, 'info');
    } catch (err) {
      console.error(`Error setting secret:`, err);
      this.showNotice(`Error setting secret: ${err.message}`, 'error');
    }
  }
}
