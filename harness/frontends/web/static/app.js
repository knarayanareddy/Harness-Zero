/**
 * Harness-Zero Studio Main Entrypoint (ES Module).
 * Coordinates timeline, SSE event streaming, approvals, and commands API (§11).
 */

import { api } from './api.js';
import { HarnessEventStream } from './events.js';
import { cardRenderer, escapeHtml } from './cards.js';
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

    // 4. Load Sessions
    await this.loadSessions();

    // 5. Connect SSE Event Stream
    this.setupEventHandlers();
    this.events.connect();

    // 6. Bind DOM UI listeners
    this.bindControls();
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

    this.events.on('turn.cancelled', () => {
      this.setBusy(false);
      this.finalizeStreamingBlock();
      this.appendNotice('Turn cancelled by user.', 'warning');
    });

    this.events.on('turn.error', (data) => {
      this.setBusy(false);
      this.finalizeStreamingBlock();
      this.appendNotice(data.error || 'Turn execution error', 'error');
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
      if (data.text) {
        this.appendTerminalOutput(data.text);
      }
    });

    // Tool execution & cards
    this.events.on('tool.call', (data) => {
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
      this.appendCard({ kind: 'compaction', payload: data });
    });

    this.events.on('ui.card', (data) => {
      const card = data.card || data;
      this.appendCard(card);
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

    // Session Switch
    this.events.on('session.switched', (data) => {
      this.activeSessionId = data.session_id;
      this.loadSessions();
      this.reloadTranscript();
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
        input.style.height = 'auto';

        // Add user block to timeline immediately
        this.appendUserTurn(text);

        // Submit to API
        try {
          const model = document.getElementById('model-select')?.value;
          await this.api.submitTurn(text, {
            session_id: this.activeSessionId,
            model: model || undefined,
          });
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

    // Terminal clear button
    const clearTerminalBtn = document.getElementById('btn-terminal-clear');
    if (clearTerminalBtn) {
      clearTerminalBtn.addEventListener('click', () => {
        const term = document.getElementById('terminal-body');
        if (term) term.textContent = '';
      });
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

      const modeBtn = e.target.closest('[data-mode]');
      if (modeBtn) {
        document.querySelectorAll('.mode-segment-btn').forEach((b) => b.classList.remove('active'));
        modeBtn.classList.add('active');
        return;
      }

      const slashItem = e.target.closest('[data-slash]');
      if (slashItem) {
        const cmd = slashItem.getAttribute('data-slash');
        const input = document.getElementById('composer-input');
        if (input) {
          input.value = cmd + ' ';
          input.focus();
        }
        const slashMenu = document.getElementById('slash-menu');
        if (slashMenu) slashMenu.style.display = 'none';
        return;
      }

      const noticeBtn = e.target.closest('[data-notice]');
      if (noticeBtn) {
        this.appendNotice(noticeBtn.getAttribute('data-notice'), 'info');
        return;
      }

      const stabBtn = e.target.closest('[data-stab]');
      if (stabBtn) {
        document.querySelectorAll('.settings-tab-btn').forEach((b) => b.classList.remove('active'));
        stabBtn.classList.add('active');
        const targetPage = stabBtn.getAttribute('data-stab');
        document.querySelectorAll('.settings-page').forEach((p) => p.classList.remove('active'));
        const pageElem = document.getElementById(`spage-${targetPage}`);
        if (pageElem) pageElem.classList.add('active');
        return;
      }

      const toggleBtn = e.target.closest('[data-action="toggle-active"]');
      if (toggleBtn) {
        toggleBtn.classList.toggle('active');
        return;
      }
    });

    // Slash menu trigger on composer input
    if (input) {
      input.addEventListener('input', () => {
        const slashMenu = document.getElementById('slash-menu');
        if (slashMenu) {
          if (input.value.startsWith('/')) {
            slashMenu.style.display = 'block';
          } else {
            slashMenu.style.display = 'none';
          }
        }
      });
    }

    // Settings search filtering
    const searchInput = document.getElementById('settings-search-input');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        const q = e.target.value.toLowerCase().trim();
        document.querySelectorAll('.settings-tab-btn').forEach((btn) => {
          const label = (btn.getAttribute('data-label') || btn.textContent).toLowerCase();
          btn.style.display = label.includes(q) ? 'flex' : 'none';
        });
      });
    }
  }

  switchStudioTab(tabName) {
    document.querySelectorAll('.studio-tab').forEach((t) => t.classList.remove('active'));
    const activeTab = document.querySelector(`.studio-tab[data-tab="${tabName}"]`);
    if (activeTab) activeTab.classList.add('active');
  }

  switchActivity(actName) {
    document.querySelectorAll('.activity-item').forEach((a) => a.classList.remove('active'));
    const activeAct = document.querySelector(`.activity-item[data-act="${actName}"]`);
    if (activeAct) activeAct.classList.add('active');
    if (actName === 'chat') {
      const input = document.getElementById('composer-input');
      if (input) input.focus();
    } else if (['files', 'search', 'skills', 'tree'].includes(actName)) {
      this.switchStudioTab(actName);
    }
  }

  appendUserTurn(text) {
    const timeline = document.getElementById('timeline');
    if (!timeline) return;

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
    block.className = `turn-block turn-${role}`;
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

    const cardHtml = cardRenderer.render({ kind: 'tool', payload: data });
    const wrapper = document.createElement('div');
    wrapper.innerHTML = cardHtml;
    timeline.appendChild(wrapper.firstElementChild);
    timeline.scrollTop = timeline.scrollHeight;
  }

  updateToolDecision(data) {
    const callId = data.call_id;
    if (!callId) return;
    const card = document.querySelector(`.harness-card-tool[data-call-id="${callId}"]`);
    if (card) {
      const badge = card.querySelector('.uppercase');
      if (badge) {
        badge.textContent = data.decision || 'ALLOWED';
        if (data.decision === 'DENIED') {
          badge.className = 'px-2 py-0.5 rounded text-xs font-mono uppercase bg-red-900/60 text-red-300 border border-red-800';
        } else if (data.decision === 'APPROVAL_REQUIRED') {
          badge.className = 'px-2 py-0.5 rounded text-xs font-mono uppercase bg-amber-900/60 text-amber-300 border border-amber-800';
        }
      }
    }
  }

  updateToolResult(data) {
    const callId = data.call_id;
    if (!callId) return;
    const card = document.querySelector(`.harness-card-tool[data-call-id="${callId}"]`);
    if (card) {
      const badge = card.querySelector('.uppercase');
      if (badge && badge.textContent === 'RUNNING') {
        badge.textContent = data.is_error ? 'ERROR' : 'COMPLETED';
        badge.className = data.is_error
          ? 'px-2 py-0.5 rounded text-xs font-mono uppercase bg-red-900/60 text-red-300 border border-red-800'
          : 'px-2 py-0.5 rounded text-xs font-mono uppercase bg-emerald-900/60 text-emerald-300 border border-emerald-800';
      }

      // Append result preview if not already present
      if (!card.querySelector('.text-emerald-300')) {
        const resultStr = escapeHtml(typeof data.result === 'object' ? JSON.stringify(data.result, null, 2) : String(data.result || ''));
        const resDiv = document.createElement('div');
        resDiv.className = 'mt-2 text-xs';
        resDiv.innerHTML = `<div class="text-gray-400 font-mono font-medium mb-1">Output:</div><pre class="p-2 bg-gray-950/90 rounded border border-gray-800 text-emerald-300 font-mono overflow-x-auto max-h-48">${resultStr}</pre>`;
        card.appendChild(resDiv);
      }
    }
  }

  appendTask(data) {
    this.appendCard({ kind: 'task', payload: data });
  }

  completeTask(data) {
    const taskId = data.task_id;
    const card = document.querySelector(`.harness-card-task`);
    if (card) {
      card.className = 'harness-card harness-card-task border p-3 rounded my-2 text-emerald-400 border-emerald-800 bg-emerald-950/30';
      const badge = card.querySelector('.uppercase');
      if (badge) badge.textContent = 'COMPLETED';
    } else {
      this.appendCard({ kind: 'task', payload: { ...data, status: 'completed' } });
    }
  }

  appendCard(card) {
    const timeline = document.getElementById('timeline');
    if (!timeline) return;

    const cardHtml = cardRenderer.render(card);
    const wrapper = document.createElement('div');
    wrapper.innerHTML = cardHtml;
    timeline.appendChild(wrapper.firstElementChild);
    timeline.scrollTop = timeline.scrollHeight;

    // Proceed banner only for artifact.request_feedback (§11, WEB-04)
    if (card.kind === 'artifact' && (card.payload?.request_feedback || card.payload?.feedback_requested)) {
      this.showProceedBanner(card.payload);
    }
  }

  showProceedBanner(payload) {
    const banner = document.getElementById('proceed-banner');
    if (banner) {
      banner.classList.remove('hidden');
      banner.setAttribute('data-artifact', payload.filename || payload.path || '');
    }
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
    const card = { kind: 'notice', payload: { message: msg, level } };
    this.appendCard(card);
  }

  appendTerminalOutput(text) {
    const term = document.getElementById('terminal-body');
    if (term) {
      term.textContent += text;
      term.scrollTop = term.scrollHeight;
    }
  }

  showApproval(data) {
    // 1. Append approval card to timeline
    const card = { kind: 'approval', payload: data };
    this.appendCard(card);

    // 2. Show top banner for high visibility
    const banner = document.getElementById('approval-banner');
    if (banner) {
      banner.classList.remove('hidden');
      banner.setAttribute('data-request-id', data.request_id || '');
    }
  }

  resolveApprovalUI(data) {
    const reqId = data.request_id;
    // Hide top banner if matches
    const banner = document.getElementById('approval-banner');
    if (banner && banner.getAttribute('data-request-id') === reqId) {
      banner.classList.add('hidden');
    }

    // Update approval card status in timeline
    const card = document.querySelector(`.harness-card-approval[data-request-id="${reqId}"]`);
    if (card) {
      card.classList.remove('border-amber-500/70', 'bg-amber-950/30');
      card.classList.add('border-gray-700', 'bg-gray-800/40', 'opacity-70');
      const actions = card.querySelector('.flex.justify-end');
      if (actions) {
        actions.innerHTML = `<span class="text-xs font-semibold uppercase text-gray-400">Resolved: ${escapeHtml(data.decision || 'done')}</span>`;
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
    if (dot) {
      dot.className = 'w-2 h-2 rounded-full ' + (
        state === 'online' ? 'bg-emerald-400' :
        state === 'reconnecting' ? 'bg-amber-400 animate-pulse' :
        'bg-red-500'
      );
    }
  }

  updateEconomics(spendData) {
    const elem = document.getElementById('economics-indicator');
    if (!elem || !spendData) return;
    const cap = spendData.cap_usd !== undefined ? `$${Number(spendData.cap_usd).toFixed(2)}` :
                spendData.spend_cap_usd !== undefined ? `$${Number(spendData.spend_cap_usd).toFixed(2)}` : '$5.00';
    let totalVal = 0.0;
    if (spendData.session_usd !== undefined) {
      totalVal = Number(spendData.session_usd);
    } else if (spendData.totals?.total_cost_usd !== undefined) {
      totalVal = Number(spendData.totals.total_cost_usd);
    }
    const total = `$${totalVal.toFixed(4)}`;

    if (spendData.unknown_pricing) {
      elem.textContent = `${total} / ${cap} (unknown pricing)`;
      elem.className = 'text-amber-400 font-semibold';
      elem.title = 'Model pricing unknown - cost estimates unavailable for this provider';
    } else {
      elem.textContent = `${total} / ${cap}`;
      elem.className = 'text-emerald-400';
      elem.title = 'Session Spend / Cap';
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
          <span class="stability-badge stability-${stab}">${escapeHtml(stab.toUpperCase())}</span>
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

  async populateModels() {
    const select = document.getElementById('model-select');
    if (!select) return;
    try {
      const models = await this.api.listModels();
      if (models && models.length > 0) {
        select.innerHTML = '<option value="">Default Model</option>';
        for (const m of models) {
          const opt = document.createElement('option');
          opt.value = m.id || m.model_name || m.name;
          opt.textContent = `${m.provider ? m.provider + ': ' : ''}${m.id || m.name}`;
          select.appendChild(opt);
        }
      }

      // Restore saved selection
      const savedModel = localStorage.getItem('harness_selected_model');
      if (savedModel && Array.from(select.options).some(o => o.value === savedModel)) {
        select.value = savedModel;
      }

      select.addEventListener('change', () => {
        localStorage.setItem('harness_selected_model', select.value);
      });
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
      const sessions = await this.api.getSessions();
      list.innerHTML = '';
      for (const s of (sessions || [])) {
        const item = document.createElement('div');
        const isActive = s.session_id === this.activeSessionId;
        item.className = `session-item ${isActive ? 'active' : ''}`;
        item.innerHTML = `
          <span class="truncate">${escapeHtml(s.session_id)}</span>
          <span class="text-xs opacity-60">${s.busy ? '⏳' : ''}</span>
        `;
        item.addEventListener('click', () => this.switchSession(s.session_id));
        list.appendChild(item);
      }
    } catch (err) {
      console.warn('Failed to load sessions:', err);
    }
  }

  async switchSession(sessionId) {
    if (sessionId === this.activeSessionId) return;
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
    if (!this.activeSessionId) return;

    try {
      const sData = await this.api.command('sessions.get', { session_id: this.activeSessionId });
      for (const rec of (sData.records || [])) {
        const type = rec.type || rec.role;
        if (type === 'USER_INPUT' || type === 'user') {
          this.appendUserTurn(rec.content || '');
        } else if (type === 'PLANNER_RESPONSE' || type === 'MODEL' || type === 'assistant') {
          this.appendTurnBlock('assistant');
          this.streamAssistantText(rec.content || '');
          this.finalizeStreamingBlock();
        }
      }
    } catch (err) {
      console.warn('Failed to reload transcript:', err);
    }
  }

  openSettings() {
    const modal = document.getElementById('settings-modal');
    if (modal) {
      modal.classList.remove('hidden');
      modal.style.display = 'flex';
      this.settingsPanel?.load();
    }
  }

  closeSettings() {
    const modal = document.getElementById('settings-modal');
    if (modal) {
      modal.classList.add('hidden');
      modal.style.display = 'none';
    }
  }
}

// Global bootstrap
document.addEventListener('DOMContentLoaded', () => {
  const app = new HarnessStudioApp();
  window.HarnessApp = app;
  app.init();
});
