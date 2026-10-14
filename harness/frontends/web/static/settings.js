/**
 * Schema-driven Settings Panel for Harness-Zero Studio (ES Module).
 * Renders tab groups according to x-ui.group and widgets based on JSON Schema (§11, WEB-05).
 * Secrets display has() status and allow masked writes only.
 */

import { escapeHtml } from './cards.js';

export class SettingsPanel {
  constructor(api, containerElement) {
    this.api = api;
    this.container = containerElement;
    this.schema = null;
    this.values = {};
    this.activeGroup = 'Models & Providers';
  }

  async load() {
    try {
      // Default schema definition covering core settings groups
      this.schema = {
        groups: [
          'Models & Providers',
          'Security & Sandboxing',
          'Execution & Strategies',
          'Economics & Spend',
          'Secrets',
        ],
        properties: {
          'kernel.provider.default': {
            title: 'Default Provider',
            type: 'string',
            enum: ['openai_compat', 'anthropic', 'gemini', 'ollama', 'mock'],
            default: 'openai_compat',
            'x-ui': { group: 'Models & Providers', widget: 'select' },
          },
          'kernel.model.default': {
            title: 'Default Model',
            type: 'string',
            default: 'gpt-4o',
            'x-ui': { group: 'Models & Providers', widget: 'text' },
          },
          'kernel.security.jail_enabled': {
            title: 'Workspace Jail Boundary',
            type: 'boolean',
            default: true,
            'x-ui': { group: 'Security & Sandboxing', widget: 'boolean' },
          },
          'kernel.security.allow_shell_execution': {
            title: 'Allow Shell Commands',
            type: 'boolean',
            default: true,
            'x-ui': { group: 'Security & Sandboxing', widget: 'boolean' },
          },
          'kernel.security.spend_cap_usd': {
            title: 'Spend Cap (USD)',
            type: 'number',
            default: 5.0,
            minimum: 0.0,
            'x-ui': { group: 'Economics & Spend', widget: 'number' },
          },
          'kernel.strategy.default': {
            title: 'Turn Strategy',
            type: 'string',
            enum: ['single_loop', 'architect_editor'],
            default: 'single_loop',
            'x-ui': { group: 'Execution & Strategies', widget: 'select' },
          },
          'secrets.OPENAI_API_KEY': {
            title: 'OpenAI API Key',
            type: 'string',
            'x-ui': { group: 'Secrets', widget: 'secret' },
          },
          'secrets.ANTHROPIC_API_KEY': {
            title: 'Anthropic API Key',
            type: 'string',
            'x-ui': { group: 'Secrets', widget: 'secret' },
          },
          'secrets.GEMINI_API_KEY': {
            title: 'Gemini API Key',
            type: 'string',
            'x-ui': { group: 'Secrets', widget: 'secret' },
          },
        },
      };

      // Fetch current values
      for (const key of Object.keys(this.schema.properties)) {
        if (key.startsWith('secrets.')) {
          // Secrets are checked for existence, not raw content
          const secretName = key.replace('secrets.', '');
          try {
            const hasSecret = await this.api.command('settings.get', { path: `has_secret.${secretName}`, default: false });
            this.values[key] = Boolean(hasSecret);
          } catch {
            this.values[key] = false;
          }
        } else {
          try {
            const val = await this.api.getSettings(key, this.schema.properties[key].default);
            this.values[key] = val;
          } catch {
            this.values[key] = this.schema.properties[key].default;
          }
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

  render() {
    if (!this.container) return;

    const groups = this.schema.groups || [];
    let tabsHtml = '';
    for (const g of groups) {
      const activeCls = g === this.activeGroup ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white hover:bg-gray-800';
      tabsHtml += `
        <button type="button" onclick="window.HarnessApp?.settingsPanel?.setGroup('${escapeHtml(g)}')" class="px-3 py-1.5 rounded text-xs font-medium transition ${activeCls}">
          ${escapeHtml(g)}
        </button>
      `;
    }

    // Filter properties for current group
    let fieldsHtml = '';
    for (const [key, prop] of Object.entries(this.schema.properties)) {
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
              <button type="button" onclick="window.HarnessApp?.settingsPanel?.saveSecret('${escapeHtml(key)}')" class="px-3 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-xs font-semibold text-white">Save</button>
            </div>
          </div>
        `;
      } else if (widget === 'boolean') {
        fieldsHtml += `
          <div class="setting-item flex items-center justify-between py-3 border-b border-gray-800">
            <label class="text-sm font-medium text-gray-200">${title}</label>
            <input type="checkbox" id="input_${escapeHtml(key)}" ${val ? 'checked' : ''} onchange="window.HarnessApp?.settingsPanel?.saveSetting('${escapeHtml(key)}', this.checked)" class="rounded bg-gray-900 border-gray-700 h-4 w-4 text-blue-600 focus:ring-blue-500" />
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
            <select id="input_${escapeHtml(key)}" onchange="window.HarnessApp?.settingsPanel?.saveSetting('${escapeHtml(key)}', this.value)" class="px-3 py-1.5 rounded bg-gray-900 border border-gray-700 text-xs text-white">
              ${options}
            </select>
          </div>
        `;
      } else if (widget === 'number') {
        fieldsHtml += `
          <div class="setting-item flex flex-col gap-1 py-3 border-b border-gray-800">
            <label class="text-sm font-medium text-gray-200">${title}</label>
            <input type="number" step="0.5" id="input_${escapeHtml(key)}" value="${escapeHtml(val ?? '')}" onchange="window.HarnessApp?.settingsPanel?.saveSetting('${escapeHtml(key)}', parseFloat(this.value))" class="px-3 py-1.5 rounded bg-gray-900 border border-gray-700 text-xs text-white" />
          </div>
        `;
      } else {
        fieldsHtml += `
          <div class="setting-item flex flex-col gap-1 py-3 border-b border-gray-800">
            <label class="text-sm font-medium text-gray-200">${title}</label>
            <input type="text" id="input_${escapeHtml(key)}" value="${escapeHtml(val ?? '')}" onchange="window.HarnessApp?.settingsPanel?.saveSetting('${escapeHtml(key)}', this.value)" class="px-3 py-1.5 rounded bg-gray-900 border border-gray-700 text-xs text-white" />
          </div>
        `;
      }
    }

    this.container.innerHTML = `
      <div class="settings-modal-content flex flex-col h-full">
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
    } catch (err) {
      alert(`Error saving setting '${path}': ${err.message}`);
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
    } catch (err) {
      alert(`Error setting secret: ${err.message}`);
    }
  }
}
