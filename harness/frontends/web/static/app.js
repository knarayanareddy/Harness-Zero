/**
 * Harness-Zero Studio Main Entrypoint (ES Module).
 * Coordinates timeline, SSE event streaming, approvals, and commands API (§11).
 */

import { api } from './api.js';
import { HarnessEventStream } from './events.js';
import { cardRenderer, escapeHtml } from './cards.js';

/** Quotes a value for use inside a CSS attribute selector ([data-x=...]); works without CSS.escape (jsdom). */
const cssString = (value) => JSON.stringify(String(value ?? ''));
import { SettingsPanel } from './settings.js';

export class HarnessStudioApp {
  constructor() {
    this.api = api;
    this.events = new HarnessEventStream(this.api);
    this.activeSessionId = null;
    this.isBusy = false;
    this.settingsPanel = null;
    this.sandboxOrigin = '';

    // Active streaming elements
    this.currentAssistantBlock = null;
    this.currentTextElement = null;
    this.currentThinkingBlock = null;
    this.currentThinkingElement = null;
  }

  async init() {
    // 1. Initial health and environment check
    try {
      const health = await this.api.health();
      this.sandboxOrigin = health.sandbox_origin || '';
      cardRenderer.sandboxOrigin = this.sandboxOrigin;

      if (health.active_session) {
        this.activeSessionId = health.active_session.session_id;
      }
      this.updateEconomics(health.spend);
      this.updateDaemonInfo(health);
    } catch (err) {
      console.warn('[HarnessStudio] Initial health check failed:', err);
    }

    // 2. Setup Settings Panel
    const settingsContainer = document.getElementById('settings-content');
    if (settingsContainer) {
      this.settingsPanel = new SettingsPanel(this.api, settingsContainer);
    }

    // 3. Setup Models & Strategy Pickers
    await this.populateModels();
    await this.populateStrategies();

    // 4. Connect the event stream *before* rendering history, so nothing that happens while the
    //    transcript is being fetched is lost.
    this.setupEventHandlers();
    this.events.connect();

    // 5. Sessions belong to the kernel: it resumes the last conversation on start and creates a session
    //    lazily on the first prompt (`submit_turn` without session_id). The client never creates one at boot.
    await this.loadSessions();
    if (this.activeSessionId) {
      await this.reloadTranscript();
    } else {
      this.showEmptyTimelineHint();
    }

    // 6. Bind DOM UI listeners
    this.bindControls();

    // 7. Provider onboarding + permission mode badge (both are settings)
    try {
      const provider = await this.api.getSettings('kernel.provider', '');
      if (!provider) {
        this.showOnboardingBanner();
      }
    } catch (e) {
      console.warn('Failed to check provider onboarding:', e);
    }
    await this.updatePermissionBadge();
  }

  /** `/health` → workspace pill and version display (the only sources those two values have). */
  updateDaemonInfo(health) {
    const ws = document.getElementById('workspace-pill');
    if (ws && health.workspace) {
      const name = String(health.workspace).replace(/[\\/]+$/, '').split(/[\\/]/).pop() || health.workspace;
      ws.textContent = `📁 ${name}`;
      ws.title = health.workspace;
    }
    const version = document.getElementById('version-display');
    if (version && health.version) version.textContent = `v${health.version}`;
  }

  /** `kernel.security.permission_mode` (ask | allow | deny) — the mode the PolicyGate enforces. */
  async updatePermissionBadge() {
    const badge = document.getElementById('permission-mode-badge');
    if (!badge) return;
    try {
      const mode = await this.api.getSettings('kernel.security.permission_mode', 'ask');
      badge.textContent = `permissions: ${mode || 'ask'}`;
      badge.dataset.warn = mode === 'allow' ? 'true' : 'false';
    } catch (_err) {
      badge.textContent = 'permissions: ?';
    }
  }

  /** Status bar: which session the next prompt goes to (title from SessionRow when known). */
  updateSessionDisplay(rows) {
    const el = document.getElementById('active-session-display');
    if (!el) return;
    if (!this.activeSessionId) {
      el.textContent = 'No session — your first message starts one';
      return;
    }
    const row = (rows || this._sessionRows || []).find((r) => r.session_id === this.activeSessionId);
    el.textContent = row && row.title ? `Session: ${row.title}` : `Session: ${this.activeSessionId}`;
    el.title = this.activeSessionId;
  }

  setupEventHandlers() {
    // Connection status indicators
    this.events.on('$connected', () => {
      this.setStatusIndicator('online', 'Connected');
    });

    this.events.on('$reconnecting', ({ attempt }) => {
      this.setStatusIndicator('reconnecting', `Reconnecting (${attempt})...`);
    });

    this.events.on('$disconnected', () => {
      this.setStatusIndicator('offline', 'Disconnected');
    });

    // Lifecycle events
    this.events.on('turn.started', (data) => {
      this.setBusy(true);
      this.appendTurnBlock('assistant');
    });

    this.events.on('turn.completed', () => {
      this.setBusy(false);
      this.finalizeStreamingBlock();
    });

    this.events.on('turn.cancelled', (data) => {
      this.setBusy(false);
      this.finalizeStreamingBlock();
      this.appendNotice(data.reason || 'Turn cancelled.', 'warn');
    });

    this.events.on('turn.error', (data) => {
      this.setBusy(false);
      this.finalizeStreamingBlock();
      this.appendNotice(data.message, 'error');
    });

    // Streaming Deltas
    this.events.on('model.text.delta', (data) => {
      if (!this.currentAssistantBlock) {
        this.appendTurnBlock('assistant');
      }
      if (data.text) {
        this.streamAssistantText(data.text);
      }
    });

    this.events.on('model.thinking.delta', (data) => {
      if (!this.currentAssistantBlock) {
        this.appendTurnBlock('assistant');
      }
      if (data.text) {
        this.streamThinkingText(data.text);
      }
    });

    this.events.on('tool.output.delta', (data) => {
      // {call_id, stream, text}: streamed into the tool card it belongs to (there is no separate terminal).
      if (data.text) {
        this.appendToolOutput(data.call_id, data.text, data.stream);
      }
    });

    this.events.on('settings.changed', (data) => {
      const paths = Array.isArray(data.paths) ? data.paths : [];
      if (paths.some((p) => String(p).startsWith('kernel.security'))) this.updatePermissionBadge();
      if (paths.includes('kernel.provider')) {
        document.getElementById('onboarding-banner')?.classList.add('hidden');
      }
    });

    // Tool execution & cards
    this.events.on('tool.call', (data) => {
      // Text streamed before this call stays above the card; text from the next model
      // call starts a fresh block below it (model.text.delta creates one on demand).
      if (this.currentAssistantBlock) {
        const hasContent = (this.currentTextElement?.textContent || '') || this.currentThinkingElement;
        if (hasContent) this.finalizeStreamingBlock();
        else { this.currentAssistantBlock.remove(); this.finalizeStreamingBlock(); }
      }
      this.appendToolCall(data);
    });

    this.events.on('tool.decision', (data) => {
      this.updateToolDecision(data);
    });

    this.events.on('tool.result', (data) => {
      this.updateToolResult(data);
    });

    this.events.on('task.started', (data) => {
      this.appendTask(data);
    });

    this.events.on('task.completed', (data) => {
      this.completeTask(data);
    });

    this.events.on('compaction.started', () => {
      this.appendNotice('Compacting context memory to optimize tokens...', 'info');
    });

    this.events.on('compaction.completed', (data) => {
      this.appendCard({ kind: 'compaction', data });
    });

    this.events.on('ui.card', (data) => {
      this.appendCard(data.card);
    });

    // Approvals
    this.events.on('approval.required', (data) => {
      this.showApproval(data);
    });

    this.events.on('approval.resolved', (data) => {
      this.resolveApprovalUI(data);
    });

    // Economics
    this.events.on('cost.updated', (data) => {
      this.updateEconomics(data);
    });

    // Context Assembler Inspector
    this.events.on('context.assembled', (data) => {
      this.updateContextInspector(data);
    });

    // Session lifecycle is kernel-owned: a session created lazily for our first prompt announces itself.
    this.events.on('session.started', (data) => {
      if (!this.activeSessionId) {
        this.activeSessionId = data.session_id;
        this.clearEmptyTimelineHint();
      }
      this.loadSessions();
      this.updateSessionDisplay();
    });

    this.events.on('session.switched', (data) => {
      if (data.session_id === this.activeSessionId) return;
      this.activeSessionId = data.session_id;
      this.loadSessions();
      this.reloadTranscript();
    });

    // Settings changed elsewhere (CLI, another tab): refresh what depends on them.
    this.events.on('settings.changed', (data) => {
      const paths = data.paths || [];
      if (paths.some((p) => p.startsWith('providers.') || p.startsWith('secrets.') || p.startsWith('kernel.'))) {
        this.populateModels();
      }
    });
  }

  bindControls() {
    // Send form
    const form = document.getElementById('composer-form');
    const input = document.getElementById('composer-input');
    const stopBtn = document.getElementById('btn-stop');

    if (form && input) {
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = input.value.trim();
        if (!text || this.isBusy) return;
        input.value = '';

        // Add user block to timeline immediately
        this.appendUserTurn(text);

        // Submit to API (no session yet → the kernel creates one and announces it via session.started)
        try {
          const modelSelect = document.getElementById('model-select');
          const selectedOption = modelSelect?.selectedOptions?.[0];
          const model = selectedOption?.value || undefined;
          const provider = selectedOption?.getAttribute('data-provider') || undefined;

          const options = {};
          if (model) options.model = model;
          if (provider) options.provider = provider;

          const res = await this.api.submitTurn(text, {
            session_id: this.activeSessionId || undefined,
            options,
          });

          if (res?.result?.error) {
            this.appendNotice(res.result.error, 'error');
            this.setBusy(false);
          }
        } catch (err) {
          this.appendNotice(`Submit failed: ${err.message}`, 'error');
          this.setBusy(false);
        }
      });

      // Shift+Enter newline vs Enter to submit
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          form.dispatchEvent(new Event('submit', { cancelable: true }));
        }
      });
    }

    if (stopBtn) {
      stopBtn.addEventListener('click', async () => {
        try {
          await this.api.cancelTurn(this.activeSessionId);
        } catch (err) {
          console.error('Failed to cancel turn:', err);
        }
      });
    }

    // Header buttons
    const openSettingsBtn = document.getElementById('btn-open-settings');
    if (openSettingsBtn) {
      openSettingsBtn.addEventListener('click', () => this.openSettings());
    }

    const closeSettingsBtn = document.getElementById('btn-close-settings');
    if (closeSettingsBtn) {
      closeSettingsBtn.addEventListener('click', () => this.closeSettings());
    }

    const toggleInspectorBtn = document.getElementById('btn-context-inspector');
    if (toggleInspectorBtn) {
      toggleInspectorBtn.addEventListener('click', () => this.toggleContextInspector());
    }

    const closeInspectorBtn = document.getElementById('btn-close-inspector');
    if (closeInspectorBtn) {
      closeInspectorBtn.addEventListener('click', () => this.toggleContextInspector());
    }

    // Sidebar buttons
    const newSessionBtn = document.getElementById('btn-new-session');
    if (newSessionBtn) {
      newSessionBtn.addEventListener('click', () => this.createNewSession());
    }

    // Approval banner buttons
    const bannerDenyBtn = document.getElementById('btn-approval-deny');
    if (bannerDenyBtn) {
      bannerDenyBtn.addEventListener('click', () => {
        const banner = document.getElementById('approval-banner');
        const reqId = banner?.getAttribute('data-request-id');
        if (reqId) this.resolveApproval(reqId, 'denied');
      });
    }

    const bannerApproveBtn = document.getElementById('btn-approval-approve');
    if (bannerApproveBtn) {
      bannerApproveBtn.addEventListener('click', () => {
        const banner = document.getElementById('approval-banner');
        const reqId = banner?.getAttribute('data-request-id');
        if (reqId) this.resolveApproval(reqId, 'approved');
      });
    }

    // Proceed banner buttons
    const proceedDismissBtn = document.getElementById('btn-proceed-dismiss');
    if (proceedDismissBtn) {
      proceedDismissBtn.addEventListener('click', () => this.dismissProceedBanner());
    }

    const proceedSubmitBtn = document.getElementById('btn-proceed-submit');
    if (proceedSubmitBtn) {
      proceedSubmitBtn.addEventListener('click', () => this.submitProceedFeedback());
    }

    // Timeline event delegation (for card approvals & card form submissions)
    const timeline = document.getElementById('timeline');
    if (timeline) {
      timeline.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-action]');
        if (!btn) return;
        const action = btn.getAttribute('data-action');
        const reqId = btn.getAttribute('data-request-id');
        if (action === 'deny-approval' && reqId) {
          this.resolveApproval(reqId, 'denied');
        } else if (action === 'approve-approval' && reqId) {
          this.resolveApproval(reqId, 'approved');
        }
      });

      timeline.addEventListener('submit', (e) => {
        const formElem = e.target.closest('form[data-form-id]');
        if (!formElem) return;
        e.preventDefault();
        const formId = formElem.getAttribute('data-form-id');
        if (formId) {
          this.submitCardForm(formId, formElem);
        }
      });
    }

    const settingsModal = document.getElementById('settings-modal');
    if (settingsModal) {
      settingsModal.addEventListener('click', (e) => {
        if (e.target === settingsModal) {
          this.closeSettings();
        }
      });
    }

    // Interactive event delegation for studio tabs, activities, and suggestion chips
    document.addEventListener('click', (e) => {
      const promptChip = e.target.closest('[data-prompt]');
      if (promptChip) {
        const text = promptChip.getAttribute('data-prompt');
        const input = document.getElementById('composer-input');
        if (input) {
          input.value = text;
          input.focus();
          const form = document.getElementById('composer-form');
          if (form) form.dispatchEvent(new Event('submit', { cancelable: true }));
        }
        return;
      }

      const tabBtn = e.target.closest('[data-tab]');
      if (tabBtn) {
        const tabName = tabBtn.getAttribute('data-tab');
        this.switchStudioTab(tabName);
        return;
      }

      const actBtn = e.target.closest('[data-act]');
      if (actBtn) {
        const actName = actBtn.getAttribute('data-act');
        this.switchActivity(actName);
        return;
      }
    });

    // Settings search filtering for real schema-driven settings
    const searchInput = document.getElementById('settings-search-input');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        const q = e.target.value.toLowerCase().trim();
        document.querySelectorAll('.setting-item').forEach((item) => {
          const label = item.querySelector('label')?.textContent.toLowerCase() || '';
          item.classList.toggle('hidden', !label.includes(q));
        });
      });
    }
  }

  async switchStudioTab(tabName) {
    document.querySelectorAll('.studio-tab').forEach((t) => t.classList.remove('active'));
    const activeTab = document.querySelector(`.studio-tab[data-tab="${tabName}"]`);
    if (activeTab) activeTab.classList.add('active');

    const artifactView = document.getElementById('studio-content-area');
    const sessionList = document.getElementById('session-list');
    const economicsView = document.getElementById('economics-view');

    if (tabName === 'sessions') {
      if (sessionList) sessionList.classList.remove('hidden');
      if (artifactView) artifactView.classList.add('hidden');
      if (economicsView) economicsView.classList.add('hidden');
      await this.loadSessions();
    } else if (tabName === 'economics') {
      if (economicsView) {
        economicsView.classList.remove('hidden');
        await this.renderEconomicsTab();
      }
      if (artifactView) artifactView.classList.add('hidden');
      if (sessionList) sessionList.classList.add('hidden');
    } else {
      if (artifactView) artifactView.classList.remove('hidden');
      if (sessionList) sessionList.classList.add('hidden');
      if (economicsView) economicsView.classList.add('hidden');
    }
  }

  async renderEconomicsTab() {
    const container = document.getElementById('economics-view');
    if (!container) return;
    try {
      // EconomicsSnapshot (harness/sdk/types.py): {turn_usd, session_usd, lifetime_usd, cap_usd,
      // unknown_pricing, usage: Usage.to_dict()} — the same shape as the `cost.updated` event.
      const data = await this.api.economics(this.activeSessionId);
      const usage = data.usage || {};
      const capUsd = data.cap_usd != null ? `$${Number(data.cap_usd).toFixed(2)}` : '—';
      const inputTok = usage.input_tokens || 0;
      const outputTok = usage.output_tokens || 0;
      const totalTok = inputTok + outputTok;
      const sessionUsd = `$${Number(data.session_usd || 0).toFixed(4)}`;
      const totalSpend = `$${Number(data.lifetime_usd || 0).toFixed(4)}`;
      const unknown = data.unknown_pricing
        ? '<div class="economics-stat-label mb-3">Some calls used a model without known pricing — spend is a lower bound.</div>'
        : '';

      container.innerHTML = `
        <div class="economics-card">
          <h3 class="economics-title">Spend &amp; Token Economics</h3>
          <div class="economics-grid">
            <div class="economics-stat">
              <div class="economics-stat-label">Session Spend / Turn Cap</div>
              <div class="economics-stat-value" data-accent="${data.unknown_pricing ? 'false' : 'true'}" data-warn="${data.unknown_pricing ? 'true' : 'false'}">${escapeHtml(sessionUsd)} / ${escapeHtml(capUsd)}</div>
            </div>
            <div class="economics-stat">
              <div class="economics-stat-label">Workspace Lifetime Cost</div>
              <div class="economics-stat-value">${escapeHtml(totalSpend)}</div>
            </div>
          </div>
          ${unknown}
          <div class="economics-stat-label mb-2">Session Token Consumption</div>
          <div class="economics-rows">
            <div class="economics-row"><span>Input Tokens</span><span class="economics-row-value">${escapeHtml(String(inputTok))}</span></div>
            <div class="economics-row"><span>Output Tokens</span><span class="economics-row-value">${escapeHtml(String(outputTok))}</span></div>
            <div class="economics-row" data-total="true"><span>Total Tokens</span><span class="economics-row-value">${escapeHtml(String(totalTok))}</span></div>
          </div>
        </div>
      `;
    } catch (err) {
      container.innerHTML = `<div class="economics-card text-red-400">Failed to load economics: ${escapeHtml(err.message)}</div>`;
    }
  }

  switchActivity(actName) {
    document.querySelectorAll('.activity-item').forEach((a) => a.classList.remove('active'));
    const activeAct = document.querySelector(`.activity-item[data-act="${actName}"]`);
    if (activeAct) activeAct.classList.add('active');
    if (actName === 'chat') {
      const input = document.getElementById('composer-input');
      if (input) input.focus();
    } else if (['sessions', 'economics'].includes(actName)) {
      this.switchStudioTab(actName);
    }
  }

  appendUserTurn(text) {
    const timeline = document.getElementById('timeline');
    if (!timeline) return;
    this.clearEmptyTimelineHint();

    const block = document.createElement('div');
    block.className = 'turn-block turn-user';
    block.innerHTML = `
      <div class="message-bubble whitespace-pre-wrap">${escapeHtml(text)}</div>
    `;
    timeline.appendChild(block);
    timeline.scrollTop = timeline.scrollHeight;
  }

  appendTurnBlock(role = 'assistant') {
    const timeline = document.getElementById('timeline');
    if (!timeline) return;

    const block = document.createElement('div');
    block.className = role === 'user' ? 'turn-block turn-user' : 'turn-block turn-assistant';
    block.innerHTML = `
      <div class="message-bubble">
        <div class="thinking-container"></div>
        <div class="content-body markdown-body"></div>
      </div>
    `;
    timeline.appendChild(block);
    timeline.scrollTop = timeline.scrollHeight;

    this.currentAssistantBlock = block;
    this.currentTextElement = block.querySelector('.content-body');
    this.currentThinkingBlock = block.querySelector('.thinking-container');
    this.currentThinkingElement = null;
  }

  streamAssistantText(chunk) {
    if (!this.currentTextElement) return;
    this.currentTextElement.textContent += chunk;
    const timeline = document.getElementById('timeline');
    if (timeline) timeline.scrollTop = timeline.scrollHeight;
  }

  streamThinkingText(chunk) {
    if (!this.currentThinkingBlock) return;
    if (!this.currentThinkingElement) {
      const details = document.createElement('details');
      details.className = 'thinking-block';
      details.open = true;
      details.innerHTML = `
        <summary>Reasoning Process</summary>
        <div class="thinking-content whitespace-pre-wrap mt-2 font-mono text-xs"></div>
      `;
      this.currentThinkingBlock.appendChild(details);
      this.currentThinkingElement = details.querySelector('.thinking-content');
    }
    this.currentThinkingElement.textContent += chunk;
  }

  finalizeStreamingBlock() {
    if (this.currentTextElement && window.marked && window.DOMPurify) {
      try {
        const rawText = this.currentTextElement.textContent || '';
        this.currentTextElement.innerHTML = window.DOMPurify.sanitize(window.marked.parse(rawText));
      } catch (err) {
        console.warn('Failed to parse markdown on finalize:', err);
      }
    }
    this.currentAssistantBlock = null;
    this.currentTextElement = null;
    this.currentThinkingBlock = null;
    this.currentThinkingElement = null;
  }

  appendToolCall(data) {
    const timeline = document.getElementById('timeline');
    if (!timeline) return;

    const cardHtml = cardRenderer.render({ kind: 'tool', id: data.call_id, data });
    const wrapper = document.createElement('div');
    wrapper.innerHTML = cardHtml;
    timeline.appendChild(wrapper.firstElementChild);
    timeline.scrollTop = timeline.scrollHeight;
  }

  updateToolDecision(data) {
    const callId = data.call_id;
    if (!callId) return;
    const card = document.querySelector(`.harness-card-tool[data-call-id=${cssString(callId)}]`);
    if (card) {
      const badge = card.querySelector('.card-badge');
      if (badge) {
        // `tool.decision` payload: {call_id, action: allow|deny|ask, policy, risk, reason}.
        if (data.action === 'deny') {
          badge.textContent = 'DENIED';
          badge.dataset.status = 'denied';
        } else if (data.action === 'ask') {
          badge.textContent = 'APPROVAL REQUIRED';
          badge.dataset.status = 'ask';
        } else {
          badge.textContent = 'ALLOWED';
          badge.dataset.status = 'allowed';
        }
        badge.title = `${data.policy}: ${data.reason}`;
      }
    }
  }

  updateToolResult(data) {
    const callId = data.call_id;
    if (!callId) return;
    const card = document.querySelector(`.harness-card-tool[data-call-id=${cssString(callId)}]`);
    if (card) {
      // `tool.result` payload: {call_id, name, is_error, duration_ms, preview, metadata}.
      const badge = card.querySelector('.card-badge');
      if (badge && badge.dataset.status !== 'denied') {
        badge.textContent = data.is_error ? 'ERROR' : 'COMPLETED';
        badge.dataset.status = data.is_error ? 'error' : 'completed';
      }
      const streamed = card.querySelector('.tool-output');
      if (streamed) {
        // Output already streamed in via tool.output.delta: only the verdict changes.
        streamed.dataset.error = data.is_error ? 'true' : 'false';
        const label = streamed.querySelector('.tool-output-label');
        if (label && data.is_error) label.textContent = 'Error:';
      } else if (data.preview || data.is_error) {
        card.querySelector('.card-body').insertAdjacentHTML('beforeend', cardRenderer.renderToolOutput(data.preview || '', data.is_error));
      }
    }
  }

  /** `tool.output.delta` → the <pre> of the tool card's `.tool-output` (created on the first chunk). */
  appendToolOutput(callId, text, stream) {
    if (!callId) return;
    const card = document.querySelector(`.harness-card-tool[data-call-id=${cssString(callId)}]`);
    if (!card) return;
    let out = card.querySelector('.tool-output');
    if (!out) {
      card.querySelector('.card-body').insertAdjacentHTML('beforeend', cardRenderer.renderToolOutput('', stream === 'stderr'));
      out = card.querySelector('.tool-output');
    }
    const pre = out?.querySelector('pre');
    if (pre) {
      pre.textContent += text;
      pre.scrollTop = pre.scrollHeight;
    }
  }

  appendTask(data) {
    // `task.started` payload: {task_id, label}
    this.appendCard({ kind: 'task', id: data.task_id, data: { ...data, status: 'running' } });
  }

  completeTask(data) {
    // `task.completed` payload: {task_id, label, exit_code, tail} — replace the running card in place.
    const card = document.querySelector(`.harness-card-task[data-task-id=${cssString(data.task_id)}]`);
    const html = cardRenderer.render({ kind: 'task', id: data.task_id, data: { ...data, status: 'completed' } });
    if (card) {
      card.outerHTML = html;
    } else {
      this.appendCardHtml(html);
    }
  }

  /** Appends an SDK card ({kind, id, title, data}) to the timeline; artifacts also go to the studio. */
  appendCard(card) {
    if (!card) return;
    this.appendCardHtml(cardRenderer.render(card));

    if (card.kind === 'artifact') {
      this.showArtifact(card);
      if (card.data?.request_feedback) this.showProceedBanner(card);
    }
  }

  /** Renders an artifact card into the Artifacts tab (newest on top) and brings the tab forward. */
  showArtifact(card) {
    const view = document.getElementById('studio-content-area');
    if (!view) return;
    view.querySelector('.artifact-empty')?.remove();
    const wrapper = document.createElement('div');
    wrapper.innerHTML = cardRenderer.render(card);
    view.prepend(wrapper.firstElementChild);
    this.switchStudioTab('artifact');
  }

  appendCardHtml(cardHtml) {
    const timeline = document.getElementById('timeline');
    if (!timeline) return;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = cardHtml;
    timeline.appendChild(wrapper.firstElementChild);
    timeline.scrollTop = timeline.scrollHeight;
  }

  showProceedBanner(card) {
    // `card.data` is the artifact: {name, media_type, url, request_feedback}; `card.title` is its summary.
    const banner = document.getElementById('proceed-banner');
    if (!banner) return;
    const artifact = card.data || {};
    banner.classList.remove('hidden');
    banner.setAttribute('data-artifact', artifact.name || '');
    const title = document.getElementById('banner-artifact-title');
    if (title) title.textContent = artifact.name || 'artifact';
    const desc = document.getElementById('banner-artifact-desc');
    if (desc) desc.textContent = card.title && card.title !== artifact.name ? card.title : `${artifact.media_type || ''} — review it in the Artifacts tab, then proceed or dismiss.`;
  }

  dismissProceedBanner() {
    const banner = document.getElementById('proceed-banner');
    if (banner) banner.classList.add('hidden');
  }

  async submitProceedFeedback() {
    this.dismissProceedBanner();
    this.appendUserTurn('Proceed with artifact.');
    try {
      const model = document.getElementById('model-select')?.value;
      await this.api.submitTurn('Proceed with artifact.', {
        session_id: this.activeSessionId,
        model: model || undefined,
      });
    } catch (err) {
      this.appendNotice(`Proceed turn failed: ${err.message}`, 'error');
    }
  }

  appendNotice(msg, level = 'info') {
    // Same shape as Card.notice(): levels info | success | warn | error.
    this.appendCard({ kind: 'notice', data: { level, body: String(msg ?? '') } });
  }

  showApproval(data) {
    // `approval.required` payload: {approval_id, call_id, kind, summary, details, timeout_s, options}
    const reqId = data.approval_id;
    // 1. Append approval card to timeline (SDK `approval` card shape; kind/timeout go in the title)
    const timeout = data.timeout_s ? ` · ${Math.round(data.timeout_s)}s to respond` : '';
    this.appendCard({
      kind: 'approval',
      id: reqId,
      title: `${data.kind || 'action'}${timeout}`,
      data: { approval_id: reqId, summary: data.summary, details: data.details || {}, options: data.options || ['approve', 'deny'] },
    });

    // 2. Show top banner for high visibility
    const banner = document.getElementById('approval-banner');
    if (banner) {
      banner.classList.remove('hidden');
      banner.setAttribute('data-request-id', reqId);
    }
  }

  resolveApprovalUI(data) {
    // `approval.resolved` payload: {approval_id, approved, by, reason}
    const reqId = data.approval_id;
    // Hide top banner if matches
    const banner = document.getElementById('approval-banner');
    if (banner && banner.getAttribute('data-request-id') === reqId) {
      banner.classList.add('hidden');
    }

    // Update approval card status in timeline
    const card = document.querySelector(`.harness-card-approval[data-request-id=${cssString(reqId)}]`);
    if (card) {
      card.classList.add('approval-resolved');
      card.dataset.resolved = data.approved ? 'approved' : 'denied';
      const actions = card.querySelector('.card-actions');
      if (actions) {
        const outcome = data.approved ? 'approved' : 'denied';
        const by = data.by ? ` by ${escapeHtml(data.by)}` : '';
        actions.innerHTML = `<span class="text-xs font-semibold uppercase">Resolved: ${outcome}${by}</span>`;
      }
    }
  }

  async resolveApproval(requestId, decision, feedback = '') {
    try {
      await this.api.resolveApproval(requestId, decision, feedback);
    } catch (err) {
      this.appendNotice(`Failed to resolve approval: ${err.message}`, 'error');
    }
  }

  async submitCardForm(formId, formElem) {
    const formData = new FormData(formElem);
    const data = Object.fromEntries(formData.entries());
    try {
      await this.api.command('form.submit', { form_id: formId, data });
      this.appendNotice(`Form submitted successfully.`, 'success');
      formElem.querySelector('button[type="submit"]')?.setAttribute('disabled', 'true');
    } catch (err) {
      this.appendNotice(`Form submission failed: ${err.message}`, 'error');
    }
  }

  setBusy(busy) {
    this.isBusy = busy;
    const sendBtn = document.getElementById('btn-send');
    const stopBtn = document.getElementById('btn-stop');
    if (busy) {
      sendBtn?.classList.add('hidden');
      stopBtn?.classList.remove('hidden');
    } else {
      sendBtn?.classList.remove('hidden');
      stopBtn?.classList.add('hidden');
    }
  }

  setStatusIndicator(state, text) {
    const dot = document.getElementById('status-dot');
    const label = document.getElementById('status-text');
    if (label) label.textContent = text;
    if (dot) dot.dataset.state = state;
  }

  updateEconomics(snapshot) {
    // EconomicsSnapshot — from `cost.updated`, `GET /health`.spend and the `economics` command alike.
    const elem = document.getElementById('economics-indicator');
    if (!elem || !snapshot) return;
    const cap = snapshot.cap_usd != null ? `$${Number(snapshot.cap_usd).toFixed(2)}` : '—';
    const total = `$${Number(snapshot.session_usd || 0).toFixed(4)}`;

    if (snapshot.unknown_pricing) {
      elem.textContent = `${total} / ${cap} (unknown pricing)`;
      elem.dataset.warn = 'true';
      elem.title = 'Some calls used a model without known pricing — session spend is a lower bound';
    } else {
      elem.textContent = `${total} / ${cap}`;
      elem.dataset.warn = 'false';
      elem.title = 'Session spend / per-turn cap';
    }
  }

  updateContextInspector(contextData) {
    if (!contextData) return;
    const badge = document.getElementById('context-tokens-badge');
    const totalTokElem = document.getElementById('inspector-total-tokens');
    const histElem = document.getElementById('inspector-history-count');
    const container = document.getElementById('inspector-blocks');

    const totalTokens = contextData.total_tokens || 0;
    if (badge) badge.textContent = `${totalTokens} tok`;
    if (totalTokElem) totalTokElem.textContent = `${totalTokens} tokens`;
    if (histElem) histElem.textContent = `History: ${contextData.history_messages || 0} msgs`;
    const stabilityElem = document.getElementById('inspector-stability-summary');
    if (stabilityElem) {
      const counts = { static: 0, session: 0, turn: 0 };
      for (const b of contextData.blocks || []) {
        const k = String(b.stability || 'turn').toLowerCase();
        counts[k] = (counts[k] || 0) + 1;
      }
      stabilityElem.textContent = `Blocks: ${counts.static} static · ${counts.session} session · ${counts.turn} turn`;
    }

    if (!container) return;
    container.innerHTML = '';
    const blocks = contextData.blocks || [];
    if (blocks.length === 0) {
      container.innerHTML = '<div class="text-xs text-gray-500 italic p-3 text-center">No blocks assembled.</div>';
      return;
    }

    for (const b of blocks) {
      const card = document.createElement('div');
      card.className = 'context-block-card';
      const stab = (b.stability || 'turn').toLowerCase();
      const tokens = b.tokens !== undefined ? `${b.tokens} tok` : '';

      card.innerHTML = `
        <div class="flex items-center justify-between mb-1">
          <span class="font-medium text-xs text-white truncate max-w-[180px]" title="${escapeHtml(b.id || '')}">${escapeHtml(b.id || b.source || 'block')}</span>
          <span class="stability-badge" data-stability="${escapeHtml(stab)}">${escapeHtml(stab.toUpperCase())}</span>
        </div>
        <div class="flex items-center justify-between text-xs text-gray-400">
          <span>Source: ${escapeHtml(b.source || 'kernel')}</span>
          <span class="font-mono text-gray-300">${escapeHtml(tokens)}</span>
        </div>
      `;
      container.appendChild(card);
    }
  }

  toggleContextInspector() {
    const inspector = document.getElementById('context-inspector');
    if (!inspector) return;
    inspector.classList.toggle('hidden');
  }

  showOnboardingBanner() {
    const banner = document.getElementById('onboarding-banner');
    if (!banner) return;
    banner.classList.remove('hidden');

    const configureBtn = document.getElementById('btn-onboarding-configure');
    const dismissBtn = document.getElementById('btn-onboarding-dismiss');

    if (configureBtn && !configureBtn._hasListener) {
      configureBtn._hasListener = true;
      configureBtn.addEventListener('click', () => {
        this.openSettings();
        this.settingsPanel?.setGroup('Models & Providers');
      });
    }

    if (dismissBtn && !dismissBtn._hasListener) {
      dismissBtn._hasListener = true;
      dismissBtn.addEventListener('click', () => {
        banner.classList.add('hidden');
      });
    }
  }

  async populateModels() {
    const select = document.getElementById('model-select');
    if (!select) return;
    try {
      // ModelRow (harness/sdk/types.py): {id, display_name, provider, context_tokens, input_usd_per_mtok,
      // output_usd_per_mtok, configured, reachable, status: 'ready' | 'needs_key' | 'unreachable'}.
      // The provider decided the status; the picker only groups by it.
      const models = await this.api.listModels();
      select.innerHTML = '<option value="">Default Model</option>';
      const groups = {
        ready: { label: 'Ready', suffix: '', disabled: false },
        needs_key: { label: 'Needs API key (Settings → Secrets)', suffix: ' (needs key)', disabled: true },
        unreachable: { label: 'Not running', suffix: ' (unreachable)', disabled: true },
      };
      const optgroups = {};
      for (const m of (models || [])) {
        const g = groups[m.status] || groups.ready;
        if (!optgroups[m.status]) {
          optgroups[m.status] = document.createElement('optgroup');
          optgroups[m.status].label = g.label;
        }
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.dataset.provider = m.provider;
        opt.textContent = `${m.provider}: ${m.display_name || m.id}${g.suffix}`;
        opt.disabled = g.disabled;
        optgroups[m.status].appendChild(opt);
      }
      for (const key of Object.keys(groups)) {
        if (optgroups[key]) select.appendChild(optgroups[key]);
      }

      // Restore saved selection
      const savedModel = localStorage.getItem('harness_selected_model');
      if (savedModel && Array.from(select.options).some(o => o.value === savedModel && !o.disabled)) {
        select.value = savedModel;
      }

      const updateModelDisplay = () => {
        const display = document.getElementById('active-model-display');
        if (display) {
          const opt = select.selectedOptions[0];
          display.textContent = opt && opt.value ? opt.textContent : 'Default Model';
        }
      };

      select.addEventListener('change', () => {
        localStorage.setItem('harness_selected_model', select.value);
        updateModelDisplay();
      });
      updateModelDisplay();
    } catch (err) {
      console.warn('Failed to populate models:', err);
    }
  }

  async populateStrategies() {
    const select = document.getElementById('strategy-select');
    if (!select) return;
    try {
      // Restore from localStorage or settings
      const savedStrategy = localStorage.getItem('harness_selected_strategy');
      if (savedStrategy) {
        select.value = savedStrategy;
      } else {
        const defaultStrategy = await this.api.getSettings('kernel.strategy', 'single_loop');
        if (defaultStrategy) select.value = defaultStrategy;
      }

      select.addEventListener('change', async () => {
        const val = select.value;
        localStorage.setItem('harness_selected_strategy', val);
        try {
          await this.api.setSettings('kernel.strategy', val);
        } catch (err) {
          console.warn('Failed to persist strategy setting:', err);
        }
      });
    } catch (err) {
      console.warn('Failed to setup strategy picker:', err);
    }
  }

  async loadSessions() {
    const list = document.getElementById('session-list');
    if (!list) return;
    try {
      // Rows are SessionRow (harness/sdk/types.py): {session_id, workspace, created_at, updated_at,
      // title, turns, message_count, active, busy}; the kernel marks the active one.
      const sessions = await this.api.getSessions();
      if (!this.activeSessionId) {
        const active = (sessions || []).find((s) => s.active);
        if (active) this.activeSessionId = active.session_id;
      }
      this._sessionRows = sessions || [];
      this.updateSessionDisplay(this._sessionRows);
      list.innerHTML = '';
      if (!sessions || sessions.length === 0) {
        list.innerHTML = '<div class="session-empty text-xs">No sessions yet — your first message starts one.</div>';
        return;
      }
      for (const s of sessions) {
        const item = document.createElement('div');
        const isActive = s.session_id === this.activeSessionId;
        item.className = `session-item ${isActive ? 'active' : ''}`;
        item.dataset.sessionId = s.session_id;
        const when = s.updated_at ? new Date(s.updated_at * 1000).toLocaleString() : '';
        const turns = `${s.turns || 0} turn${s.turns === 1 ? '' : 's'}`;
        item.innerHTML = `
          <div class="session-title truncate">${escapeHtml(s.title || 'New session')}</div>
          <div class="session-meta text-xs">
            <span class="session-id font-mono">${escapeHtml(s.session_id)}</span>
            <span class="session-turns">${escapeHtml(turns)}</span>
            <span class="session-when">${escapeHtml(when)}</span>
            <span class="session-busy">${s.busy ? '⏳' : ''}</span>
          </div>
        `;
        item.addEventListener('click', () => this.switchSession(s.session_id));
        list.appendChild(item);
      }
    } catch (err) {
      console.warn('Failed to load sessions:', err);
    }
  }

  showEmptyTimelineHint() {
    const timeline = document.getElementById('timeline');
    if (!timeline || timeline.querySelector('.timeline-empty')) return;
    const hint = document.createElement('div');
    hint.className = 'timeline-empty text-xs';
    hint.textContent = 'No conversation yet. Type a message below to start a session.';
    timeline.appendChild(hint);
  }

  clearEmptyTimelineHint() {
    document.querySelector('#timeline .timeline-empty')?.remove();
  }

  async switchSession(sessionId) {
    if (sessionId === this.activeSessionId) {
      await this.reloadTranscript();
      return;
    }
    try {
      await this.api.switchSession(sessionId);
      this.activeSessionId = sessionId;
      await this.loadSessions();
      await this.reloadTranscript();
    } catch (err) {
      this.appendNotice(`Failed to switch session: ${err.message}`, 'error');
    }
  }

  async createNewSession() {
    try {
      const s = await this.api.createSession();
      this.activeSessionId = s.session_id;
      await this.loadSessions();
      await this.reloadTranscript();
    } catch (err) {
      this.appendNotice(`Failed to create session: ${err.message}`, 'error');
    }
  }

  async reloadTranscript() {
    const timeline = document.getElementById('timeline');
    if (!timeline) return;
    timeline.innerHTML = '';
    if (!this.activeSessionId) {
      this.showEmptyTimelineHint();
      return;
    }

    try {
      // TranscriptRecord (harness/sdk/types.py): {role, content, tool_calls: [{id, name, arguments}],
      // tool_results: [{tool_use_id, content: [{type, text}], is_error}], thinking?, metadata}
      const sData = await this.api.command('sessions.get', { session_id: this.activeSessionId });
      for (const rec of (sData.records || [])) {
        if (rec.role === 'user') {
          // Tool results are stored as user-role records with empty content: no empty bubble for them.
          if (rec.content) this.appendUserTurn(rec.content);
          for (const tr of (rec.tool_results || [])) {
            const text = (tr.content || []).filter((b) => b.type === 'text').map((b) => b.text).join('');
            this.updateToolResult({ call_id: tr.tool_use_id, is_error: !!tr.is_error, preview: text });
          }
        } else if (rec.role === 'assistant') {
          if (rec.content) {
            this.appendTurnBlock('assistant');
            this.streamAssistantText(rec.content);
            this.finalizeStreamingBlock();
          }
          for (const tc of (rec.tool_calls || [])) {
            this.appendToolCall({ call_id: tc.id, name: tc.name, arguments: tc.arguments || {}, status: 'completed' });
          }
        } else if (rec.role === 'system' && rec.content) {
          this.appendNotice(rec.content, 'info');
        }
      }
      if (!timeline.children.length) this.showEmptyTimelineHint();
    } catch (err) {
      console.warn('Failed to reload transcript:', err);
    }
  }

  openSettings() {
    const modal = document.getElementById('settings-modal');
    if (modal) {
      modal.classList.remove('hidden');
      this.settingsPanel?.load();
    }
  }

  closeSettings() {
    const modal = document.getElementById('settings-modal');
    if (modal) {
      modal.classList.add('hidden');
    }
  }
}

// Global bootstrap
document.addEventListener('DOMContentLoaded', () => {
  const app = new HarnessStudioApp();
  window.HarnessApp = app;
  app.init();
});
