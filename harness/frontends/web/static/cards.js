/**
 * Harness-Zero card renderer (ES module).
 *
 * Renders the SDK card contract — `harness/sdk/cards.py`: `Card{kind, id, title, data}` with the
 * per-kind `data` keys listed in `CARD_FIELDS` — plus three web-internal kinds the timeline uses
 * for kernel events:
 *
 *   tool        data = `tool.call` payload        {call_id, name, arguments, risk, plugin}
 *   task        data = `task.*` payload + status  {task_id, label, status, exit_code?, tail?}
 *   compaction  data = `compaction.completed`     {summary_tokens, duration_ms}
 *
 * Every renderer reads exactly the keys its contract declares. There are no alias chains: if a
 * producer sends a different shape, `tests/frontends/test_static_contract.py` fails, not the user.
 *
 * DOM hooks relied on by app.js and the scenario suite (keep stable):
 *   .harness-card[data-card-id]            every card
 *   .harness-card-tool[data-call-id]       tool card; badge is `.card-badge.uppercase`,
 *                                          result goes into `.tool-output`
 *   .harness-card-task[data-task-id][data-status] task card; badge is `.card-badge.uppercase`
 *   .harness-card-approval[data-request-id] approval card; buttons
 *                                          button[data-action="approve-approval"|"deny-approval"];
 *                                          `.card-actions.flex.justify-end` is replaced on resolve
 *   .harness-card-notice.notice[data-level] notices (levels: info | success | warn | error)
 */

export function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/** Sanitizes already-rendered HTML; falls back to escaping when DOMPurify is not loaded. */
export function sanitize(html) {
  if (typeof window !== 'undefined' && window.DOMPurify) {
    return window.DOMPurify.sanitize(html);
  }
  return escapeHtml(html);
}

/** Markdown -> safe HTML (marked + DOMPurify when loaded, escaped text otherwise). */
export function renderMarkdown(body) {
  const text = String(body ?? '');
  if (typeof window !== 'undefined' && window.marked && window.DOMPurify) {
    return window.DOMPurify.sanitize(window.marked.parse(text));
  }
  return `<div class="whitespace-pre-wrap">${escapeHtml(text)}</div>`;
}

const NOTICE_LEVELS = new Set(['info', 'success', 'warn', 'error']);
const TOOL_BADGE = {
  pending: 'PENDING',
  allowed: 'ALLOWED',
  ask: 'APPROVAL REQUIRED',
  denied: 'DENIED',
  completed: 'COMPLETED',
  error: 'ERROR',
};

function fmtJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (_err) {
    return String(value);
  }
}

export class CardRenderer {
  constructor(options = {}) {
    this.sandboxOrigin = options.sandboxOrigin || '';
  }

  /** Returns the HTML string for one card (`{kind, id?, title?, data}`). */
  render(card) {
    if (!card || typeof card !== 'object' || !card.kind) {
      return this.renderFallback({ kind: 'invalid', data: card });
    }
    const data = card.data && typeof card.data === 'object' ? card.data : {};
    switch (card.kind) {
      case 'markdown':
        return this.renderMarkdown(card, data);
      case 'code':
        return this.renderCode(card, data);
      case 'diff':
        return this.renderDiff(card, data);
      case 'table':
        return this.renderTable(card, data);
      case 'notice':
        return this.renderNotice(card, data);
      case 'progress':
        return this.renderProgress(card, data);
      case 'tree':
        return this.renderTree(card, data);
      case 'approval':
        return this.renderApproval(card, data);
      case 'artifact':
        return this.renderArtifact(card, data);
      case 'form':
        return this.renderForm(card, data);
      case 'tool':
        return this.renderTool(card, data);
      case 'task':
        return this.renderTask(card, data);
      case 'compaction':
        return this.renderCompaction(card, data);
      default:
        return this.renderFallback(card);
    }
  }

  // ---- shared chrome -------------------------------------------------------------------

  shell(card, kind, bodyHtml, { attrs = '', extraClass = '', icon = '', header = null } = {}) {
    const id = escapeHtml(card.id || '');
    const title = header ?? (card.title ? escapeHtml(card.title) : '');
    const headerHtml = title
      ? `<div class="card-header">${icon ? `<span class="card-icon">${icon}</span>` : ''}<span class="card-title">${title}</span></div>`
      : '';
    return `
      <div class="harness-card harness-card-${kind} ${extraClass}" data-card-id="${id}" data-kind="${escapeHtml(kind)}" ${attrs}>
        ${headerHtml}
        <div class="card-body">${bodyHtml}</div>
      </div>
    `;
  }

  // ---- SDK kinds -----------------------------------------------------------------------

  renderMarkdown(card, data) {
    return this.shell(card, 'markdown', `<div class="markdown-body">${renderMarkdown(data.body)}</div>`, { icon: '📄' });
  }

  renderCode(card, data) {
    const language = escapeHtml(data.language || 'text');
    const header = card.title
      ? escapeHtml(card.title)
      : data.path
        ? `${escapeHtml(data.path)} <span class="card-meta font-mono text-xs">${language}</span>`
        : `<span class="card-meta font-mono text-xs">${language}</span>`;
    const body = `<pre class="code-block font-mono text-xs overflow-x-auto"><code class="language-${language}">${escapeHtml(data.body)}</code></pre>`;
    return this.shell(card, 'code', body, { icon: '⌨️', header });
  }

  renderDiff(card, data) {
    const stats = data.stats || {};
    const lines = String(data.unified || '').split('\n');
    let renderedLines = '';
    for (const line of lines) {
      let cls = 'diff-line-context';
      if (line.startsWith('+') && !line.startsWith('+++')) cls = 'diff-line-add';
      else if (line.startsWith('-') && !line.startsWith('---')) cls = 'diff-line-del';
      else if (line.startsWith('@@')) cls = 'diff-line-hunk';
      renderedLines += `<div class="diff-line ${cls}">${escapeHtml(line)}</div>`;
    }
    const header = `${escapeHtml(card.title || data.path)} <span class="diff-stats font-mono text-xs"><span class="diff-added">+${Number(stats.added) || 0}</span> <span class="diff-removed">-${Number(stats.removed) || 0}</span></span>`;
    return this.shell(card, 'diff', `<div class="diff-container font-mono text-xs">${renderedLines}</div>`, { icon: '📝', header });
  }

  renderTable(card, data) {
    const columns = Array.isArray(data.columns) ? data.columns : [];
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const thHtml = columns.map((c) => `<th class="table-th">${escapeHtml(c)}</th>`).join('');
    const trHtml = rows
      .map((row) => {
        const cells = Array.isArray(row) ? row : columns.map((c) => (row && typeof row === 'object' ? row[c] : ''));
        return `<tr>${cells.map((cell) => `<td class="table-td">${escapeHtml(cell)}</td>`).join('')}</tr>`;
      })
      .join('');
    const body = `
      <div class="table-container overflow-x-auto">
        <table class="w-full text-left text-sm border-collapse">
          ${thHtml ? `<thead><tr>${thHtml}</tr></thead>` : ''}
          <tbody>${trHtml}</tbody>
        </table>
      </div>`;
    return this.shell(card, 'table', body, { icon: '▦' });
  }

  renderNotice(card, data) {
    const level = NOTICE_LEVELS.has(data.level) ? data.level : 'info';
    const body = `<div class="notice-body text-sm">${renderMarkdown(data.body)}</div>`;
    return this.shell(card, 'notice', body, {
      extraClass: 'notice',
      attrs: `data-level="${level}" role="${level === 'error' ? 'alert' : 'status'}"`,
    });
  }

  renderProgress(card, data) {
    const label = escapeHtml(data.label || '');
    const value = data.value === null || data.value === undefined ? null : Math.max(0, Math.min(1, Number(data.value)));
    const pct = value === null ? null : Math.round(value * 100);
    // A native <progress>: no value = indeterminate, no inline style needed for the fill.
    const bar = pct === null
      ? `<progress class="progress-bar" aria-label="${label}"></progress>`
      : `<progress class="progress-bar" value="${pct}" max="100" aria-label="${label}"></progress>`;
    const body = `
      <div class="flex items-center justify-between text-xs mb-1">
        <span class="progress-label">${label}</span>
        <span class="progress-value font-mono">${pct === null ? '…' : `${pct}%`}</span>
      </div>
      ${bar}`;
    return this.shell(card, 'progress', body);
  }

  renderTree(card, data) {
    // Node shape: {label, children?: [node]} (`id`/`name` accepted as the label when `label` is absent).
    const renderNodes = (nodes, depth) => {
      if (!Array.isArray(nodes) || nodes.length === 0) return '';
      const items = nodes
        .map((n) => {
          const node = n && typeof n === 'object' ? n : { label: String(n) };
          const label = escapeHtml(node.label ?? node.name ?? node.id ?? '');
          const children = renderNodes(node.children, depth + 1);
          return `<li class="tree-node" data-depth="${depth}"><span class="tree-label">${children ? '▸ ' : ''}${label}</span>${children}</li>`;
        })
        .join('');
      return `<ul class="tree-list">${items}</ul>`;
    };
    return this.shell(card, 'tree', renderNodes(data.nodes, 0), { icon: '🌳' });
  }

  renderApproval(card, data) {
    const reqId = escapeHtml(data.approval_id || '');
    const summary = escapeHtml(data.summary || '');
    const options = Array.isArray(data.options) ? data.options : ['approve', 'deny'];
    const details = data.details && Object.keys(data.details).length ? fmtJson(data.details) : '';
    const buttons = [];
    if (options.includes('deny')) {
      buttons.push(`<button type="button" data-action="deny-approval" data-request-id="${reqId}" class="btn btn-secondary px-3 py-1.5 rounded text-sm font-medium">Deny</button>`);
    }
    if (options.includes('approve')) {
      buttons.push(`<button type="button" data-action="approve-approval" data-request-id="${reqId}" class="btn btn-warning px-4 py-1.5 rounded text-sm font-bold">Approve</button>`);
    }
    const header = `Approval Required: ${escapeHtml(card.title || summary)}`;
    const body = `
      ${card.title && summary ? `<div class="approval-summary text-sm mb-2">${summary}</div>` : ''}
      ${details ? `<pre class="approval-details font-mono text-xs overflow-x-auto">${escapeHtml(details)}</pre>` : ''}
      <div class="card-actions flex justify-end gap-3 mt-3">${buttons.join('')}</div>`;
    return this.shell(card, 'approval', body, { icon: '⚠️', header, attrs: `data-request-id="${reqId}"` });
  }

  renderArtifact(card, data) {
    const name = escapeHtml(data.name || 'artifact');
    const mediaType = String(data.media_type || 'application/octet-stream');
    const rawUrl = String(data.url || '');
    const isAbsolute = /^https?:\/\//i.test(rawUrl);
    const url = isAbsolute || !this.sandboxOrigin ? rawUrl : `${this.sandboxOrigin}${rawUrl.startsWith('/') ? '' : '/'}${rawUrl}`;
    const safeUrl = escapeHtml(url);
    let preview = '';
    if (mediaType === 'text/html') {
      preview = `<div class="artifact-preview mt-2"><iframe src="${safeUrl}" class="artifact-frame w-full h-64" sandbox="" referrerpolicy="no-referrer" title="${name}"></iframe></div>`;
    } else if (mediaType.startsWith('image/')) {
      preview = `<div class="artifact-preview mt-2"><img src="${safeUrl}" alt="${name}" class="artifact-image max-h-64" /></div>`;
    }
    const body = `
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0">
          <span class="artifact-name font-medium truncate">${name}</span>
          <span class="card-meta font-mono text-xs">${escapeHtml(mediaType)}</span>
        </div>
        <a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="artifact-link text-xs">Open ↗</a>
      </div>
      ${preview}
      ${data.request_feedback ? '<div class="artifact-feedback text-xs mt-2">Feedback requested — use the banner above to proceed.</div>' : ''}`;
    return this.shell(card, 'artifact', body, { icon: '📦', attrs: `data-artifact-name="${name}"` });
  }

  renderForm(card, data) {
    // `schema` is a JSON Schema object: {properties: {name: {type, title?, description?, enum?, default?, format?}}, required?: []}
    const formId = escapeHtml(data.form_id || '');
    const schema = data.schema && typeof data.schema === 'object' ? data.schema : {};
    const props = schema.properties && typeof schema.properties === 'object' ? schema.properties : {};
    const required = new Set(Array.isArray(schema.required) ? schema.required : []);
    const fieldsHtml = Object.entries(props)
      .map(([name, spec]) => {
        const s = spec && typeof spec === 'object' ? spec : {};
        const safeName = escapeHtml(name);
        const label = escapeHtml(s.title || name);
        const req = required.has(name) ? ' required' : '';
        const help = s.description ? `<div class="form-help text-xs">${escapeHtml(s.description)}</div>` : '';
        const defVal = s.default === undefined || s.default === null ? '' : escapeHtml(s.default);
        let control;
        if (Array.isArray(s.enum)) {
          const opts = s.enum.map((v) => `<option value="${escapeHtml(v)}"${String(v) === String(s.default) ? ' selected' : ''}>${escapeHtml(v)}</option>`).join('');
          control = `<select name="${safeName}" class="form-select w-full"${req}>${opts}</select>`;
        } else if (s.type === 'boolean') {
          control = `<input type="checkbox" name="${safeName}" value="true" class="form-checkbox"${s.default ? ' checked' : ''} />`;
        } else if (s.type === 'integer' || s.type === 'number') {
          const step = s.type === 'integer' ? ' step="1"' : ' step="any"';
          control = `<input type="number" name="${safeName}" value="${defVal}" class="form-input w-full"${step}${req} />`;
        } else if (s.format === 'textarea' || s.format === 'multiline') {
          control = `<textarea name="${safeName}" class="form-textarea w-full" rows="4"${req}>${defVal}</textarea>`;
        } else {
          const type = s.format === 'password' ? 'password' : s.format === 'email' ? 'email' : 'text';
          control = `<input type="${type}" name="${safeName}" value="${defVal}" class="form-input w-full"${req} />`;
        }
        return `<div class="form-field my-2"><label class="form-label block text-xs font-medium mb-1">${label}${required.has(name) ? ' *' : ''}</label>${control}${help}</div>`;
      })
      .join('');
    const body = `
      <form class="card-form-element" data-form-id="${formId}">
        ${fieldsHtml || '<div class="text-xs">This form has no fields.</div>'}
        <div class="card-actions form-actions mt-3 flex justify-end gap-2">
          <button type="submit" class="btn btn-primary px-3 py-1 rounded text-sm">${escapeHtml(data.submit_label || 'Submit')}</button>
        </div>
      </form>`;
    return this.shell(card, 'form', body, { icon: '📝', header: escapeHtml(card.title || schema.title || 'Input requested'), attrs: `data-form-id="${formId}"` });
  }

  // ---- web-internal kinds --------------------------------------------------------------

  renderTool(card, data) {
    const name = escapeHtml(data.name || 'tool');
    const callId = escapeHtml(data.call_id || '');
    const argsStr = data.arguments && Object.keys(data.arguments).length ? escapeHtml(fmtJson(data.arguments)) : '';
    const meta = [data.plugin, data.risk].filter(Boolean).map((v) => escapeHtml(v)).join(' · ');
    const status = TOOL_BADGE[data.status] || TOOL_BADGE.pending;
    const body = `
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0">
          <span class="card-icon">⚡</span>
          <span class="tool-name font-mono font-bold text-sm">${name}</span>
          ${meta ? `<span class="card-meta text-xs">${meta}</span>` : ''}
        </div>
        <span class="card-badge uppercase font-mono text-xs" data-status="${escapeHtml(data.status || 'pending')}">${status}</span>
      </div>
      ${argsStr ? `<details class="tool-arguments mt-2 text-xs"><summary class="cursor-pointer font-mono">Arguments</summary><pre class="p-2 mt-1 font-mono overflow-x-auto">${argsStr}</pre></details>` : ''}`;
    return this.shell(card, 'tool', body, { attrs: `data-call-id="${callId}"` });
  }

  /**
   * Markup app.js appends into a tool card: once when `tool.output.delta` starts streaming
   * (empty text, then appended to the <pre>), or when `tool.result` arrives with a preview.
   */
  renderToolOutput(preview, isError) {
    const text = typeof preview === 'string' ? preview : fmtJson(preview);
    return `<div class="tool-output mt-2 text-xs" data-error="${isError ? 'true' : 'false'}"><div class="tool-output-label font-mono font-medium mb-1">${isError ? 'Error:' : 'Output:'}</div><pre class="p-2 font-mono overflow-x-auto max-h-48">${escapeHtml(text)}</pre></div>`;
  }

  renderTask(card, data) {
    const taskId = escapeHtml(data.task_id || '');
    const label = escapeHtml(data.label || data.task_id || 'task');
    const status = data.status === 'completed' ? (data.exit_code === 0 || data.exit_code === null || data.exit_code === undefined ? 'completed' : 'failed') : 'running';
    const icon = status === 'completed' ? '✓' : status === 'failed' ? '✗' : '⏳';
    const exit = data.exit_code === null || data.exit_code === undefined ? '' : ` (exit ${escapeHtml(data.exit_code)})`;
    const body = `
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0">
          <span class="card-icon">${icon}</span>
          <span class="task-label font-mono font-semibold text-xs">${label}</span>
          <span class="card-meta text-xs">${taskId}</span>
        </div>
        <span class="card-badge uppercase font-mono text-xs" data-status="${status}">${status.toUpperCase()}${exit}</span>
      </div>
      ${data.tail ? `<pre class="task-tail mt-2 p-2 font-mono text-xs overflow-x-auto max-h-48">${escapeHtml(data.tail)}</pre>` : ''}`;
    return this.shell(card, 'task', body, { attrs: `data-task-id="${taskId}" data-status="${status}"` });
  }

  renderCompaction(card, data) {
    const summaryTokens = Number(data.summary_tokens) || 0;
    const durationMs = Math.round(Number(data.duration_ms) || 0);
    const body = `
      <div class="flex items-center gap-2">
        <span class="card-icon">🗜️</span>
        <span class="font-semibold text-sm">Context compacted</span>
      </div>
      <div class="text-xs mt-1 font-mono">Summary is ${summaryTokens.toLocaleString()} tokens (took ${durationMs.toLocaleString()} ms).</div>`;
    return this.shell(card, 'compaction', body);
  }

  renderFallback(card) {
    const kind = escapeHtml(card?.kind || 'unknown');
    const body = `
      <div class="text-xs font-mono mb-1">Unsupported card kind: ${kind}</div>
      <pre class="text-xs overflow-x-auto font-mono">${escapeHtml(fmtJson(card?.data ?? card ?? null))}</pre>`;
    return this.shell({ id: card?.id, title: card?.title }, 'fallback', body);
  }
}

export const cardRenderer = new CardRenderer();
