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
    this.activeGroup = 'Models & Providers';
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

    // Filter properties for current group
    let fieldsHtml = '';
    for (const [key, prop] of Object.entries(this.schema.properties || {})) {
      const ui = prop['x-ui'] || {};
      if (ui.group !== this.activeGroup) continue;

      const title = escapeHtml(prop.title || key);
      const widget = ui.widget || (prop.type === 'boolean' ? 'boolean' : 'text');
      const val = this.values[key];

      if (widget === 'secret') {
        const isSet = Boolean(val);
        const badge = isSet
          ? '<span class="text-xs px-2 py-0.5 rounded bg-emerald-900/60 text-emerald-300 font-mono">Configured ✓</span>'
          : '<span class="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400 font-mono">Not Set</span>';

        fieldsHtml += `
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
        fieldsHtml += `
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

        fieldsHtml += `
          <div class="setting-item flex flex-col gap-1 py-3 border-b border-gray-800">
            <label class="text-sm font-medium text-gray-200">${title}</label>
            <select id="input_${escapeHtml(key)}" data-setting-path="${escapeHtml(key)}" data-setting-type="string" class="px-3 py-1.5 rounded bg-gray-900 border border-gray-700 text-xs text-white">
              ${options}
            </select>
          </div>
        `;
      } else if (widget === 'number') {
        fieldsHtml += `
          <div class="setting-item flex flex-col gap-1 py-3 border-b border-gray-800">
            <label class="text-sm font-medium text-gray-200">${title}</label>
            <input type="number" step="0.5" id="input_${escapeHtml(key)}" data-setting-path="${escapeHtml(key)}" data-setting-type="number" value="${escapeHtml(val ?? '')}" class="px-3 py-1.5 rounded bg-gray-900 border border-gray-700 text-xs text-white" />
          </div>
        `;
      } else {
        fieldsHtml += `
          <div class="setting-item flex flex-col gap-1 py-3 border-b border-gray-800">
            <label class="text-sm font-medium text-gray-200">${title}</label>
            <input type="text" id="input_${escapeHtml(key)}" data-setting-path="${escapeHtml(key)}" data-setting-type="string" value="${escapeHtml(val ?? '')}" class="px-3 py-1.5 rounded bg-gray-900 border border-gray-700 text-xs text-white" />
          </div>
        `;
      }
    }

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
