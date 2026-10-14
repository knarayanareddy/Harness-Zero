/**
 * Harness-Zero Cards Renderer (ES Module)
 * Implements standard UI cards: diff, table, plan, form, artifact, notice, approval, and fallback.
 * Uses DOMPurify when available, with safe HTML escaping fallback for headless environments.
 */

export function sanitize(html) {
  if (typeof window !== 'undefined' && window.DOMPurify) {
    return window.DOMPurify.sanitize(html);
  }
  // Safe basic escape for headless Node or when DOMPurify is loading
  return String(html)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

export function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

export class CardRenderer {
  constructor(options = {}) {
    this.onAction = options.onAction || (() => {});
    this.sandboxOrigin = options.sandboxOrigin || '';
  }

  render(card, context = {}) {
    if (!card || typeof card !== 'object') {
      return this.renderFallback({ kind: 'empty', payload: {} });
    }

    const kind = card.kind;
    const payload = card.payload || {};

    switch (kind) {
      case 'diff':
        return this.renderDiff(payload, context);
      case 'table':
        return this.renderTable(payload, context);
      case 'plan':
        return this.renderPlan(payload, context);
      case 'form':
        return this.renderForm(payload, context);
      case 'artifact':
        return this.renderArtifact(payload, context);
      case 'notice':
        return this.renderNotice(payload, context);
      case 'approval':
        return this.renderApproval(payload, context);
      default:
        return this.renderFallback(card, context);
    }
  }

  renderDiff(payload, _context) {
    const file = escapeHtml(payload.file || payload.path || 'File Diff');
    const diffText = payload.diff || payload.content || '';
    const lines = diffText.split('\n');

    let renderedLines = '';
    for (const line of lines) {
      let cls = 'diff-line-context';
      if (line.startsWith('+') && !line.startsWith('+++')) {
        cls = 'diff-line-add';
      } else if (line.startsWith('-') && !line.startsWith('---')) {
        cls = 'diff-line-del';
      } else if (line.startsWith('@@')) {
        cls = 'diff-line-hunk';
      }
      renderedLines += `<div class="diff-line ${cls}">${escapeHtml(line)}</div>`;
    }

    return `
      <div class="harness-card harness-card-diff">
        <div class="card-header">
          <span class="card-icon">📝</span>
          <span class="card-title">${file}</span>
        </div>
        <div class="diff-container font-mono text-xs">
          ${renderedLines}
        </div>
      </div>
    `;
  }

  renderTable(payload, _context) {
    const title = payload.title ? `<div class="card-title">${escapeHtml(payload.title)}</div>` : '';
    const headers = payload.headers || payload.columns || [];
    const rows = payload.rows || [];

    let thHtml = '';
    for (const h of headers) {
      thHtml += `<th class="table-th">${escapeHtml(h)}</th>`;
    }

    let trHtml = '';
    for (const row of rows) {
      let tdHtml = '';
      const cells = Array.isArray(row) ? row : Object.values(row);
      for (const cell of cells) {
        tdHtml += `<td class="table-td">${escapeHtml(cell)}</td>`;
      }
      trHtml += `<tr>${tdHtml}</tr>`;
    }

    return `
      <div class="harness-card harness-card-table">
        ${title ? `<div class="card-header">${title}</div>` : ''}
        <div class="table-container overflow-x-auto">
          <table class="w-full text-left text-sm border-collapse">
            ${thHtml ? `<thead><tr>${thHtml}</tr></thead>` : ''}
            <tbody>${trHtml}</tbody>
          </table>
        </div>
      </div>
    `;
  }

  renderPlan(payload, _context) {
    const title = escapeHtml(payload.title || 'Execution Plan');
    const steps = payload.steps || [];

    let stepsHtml = '';
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i];
      const desc = typeof step === 'string' ? step : (step.text || step.description || step.title || '');
      const status = typeof step === 'object' && step.status ? step.status : 'pending';
      const statusIcon = status === 'completed' || status === 'done' ? '✓' : status === 'running' ? '⏳' : status === 'failed' ? '✗' : '○';
      const statusCls = `step-status-${status}`;

      stepsHtml += `
        <li class="plan-step flex items-start gap-2 py-1 ${statusCls}">
          <span class="step-badge">${statusIcon}</span>
          <span class="step-num text-xs text-gray-400">${i + 1}.</span>
          <span class="step-desc text-sm flex-1">${escapeHtml(desc)}</span>
        </li>
      `;
    }

    return `
      <div class="harness-card harness-card-plan">
        <div class="card-header flex items-center gap-2">
          <span class="card-icon">📋</span>
          <span class="card-title font-semibold">${title}</span>
        </div>
        <ul class="plan-steps-list mt-2 space-y-1">
          ${stepsHtml}
        </ul>
      </div>
    `;
  }

  renderForm(payload, _context) {
    const title = escapeHtml(payload.title || 'Input Request');
    const formId = escapeHtml(payload.form_id || payload.id || `form_${Date.now()}`);
    const fields = payload.fields || [];

    let fieldsHtml = '';
    for (const f of fields) {
      const name = escapeHtml(f.name || '');
      const label = escapeHtml(f.label || name);
      const type = escapeHtml(f.type || 'text');
      const placeholder = escapeHtml(f.placeholder || '');
      const defVal = escapeHtml(f.default ?? '');

      if (type === 'textarea') {
        fieldsHtml += `
          <div class="form-field my-2">
            <label class="block text-xs font-medium text-gray-300 mb-1">${label}</label>
            <textarea name="${name}" placeholder="${placeholder}" class="form-textarea w-full px-2 py-1 rounded bg-gray-800 text-sm">${defVal}</textarea>
          </div>
        `;
      } else if (type === 'select') {
        const options = (f.options || []).map(opt => {
          const val = typeof opt === 'object' ? opt.value : opt;
          const lbl = typeof opt === 'object' ? opt.label : opt;
          return `<option value="${escapeHtml(val)}">${escapeHtml(lbl)}</option>`;
        }).join('');
        fieldsHtml += `
          <div class="form-field my-2">
            <label class="block text-xs font-medium text-gray-300 mb-1">${label}</label>
            <select name="${name}" class="form-select w-full px-2 py-1 rounded bg-gray-800 text-sm">${options}</select>
          </div>
        `;
      } else {
        fieldsHtml += `
          <div class="form-field my-2">
            <label class="block text-xs font-medium text-gray-300 mb-1">${label}</label>
            <input type="${type}" name="${name}" value="${defVal}" placeholder="${placeholder}" class="form-input w-full px-2 py-1 rounded bg-gray-800 text-sm" />
          </div>
        `;
      }
    }

    return `
      <div class="harness-card harness-card-form" data-form-id="${formId}">
        <div class="card-header font-semibold text-sm mb-2">${title}</div>
        <form class="card-form-element" onsubmit="event.preventDefault(); window.HarnessApp?.submitCardForm('${formId}', this);">
          ${fieldsHtml}
          <div class="form-actions mt-3 flex justify-end gap-2">
            <button type="submit" class="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm">Submit</button>
          </div>
        </form>
      </div>
    `;
  }

  renderArtifact(payload, _context) {
    const filename = escapeHtml(payload.filename || payload.path || 'artifact');
    const artifactType = escapeHtml(payload.type || payload.content_type || 'file');
    const sbOrigin = this.sandboxOrigin || '';
    const previewUrl = sbOrigin ? `${sbOrigin}/artifacts/${encodeURIComponent(filename)}` : `/artifacts/${encodeURIComponent(filename)}`;

    const isHtml = filename.endsWith('.html') || filename.endsWith('.htm');
    const isImage = filename.endsWith('.png') || filename.endsWith('.jpg') || filename.endsWith('.svg');

    let previewContent = '';
    if (isHtml) {
      previewContent = `
        <div class="artifact-preview-frame mt-2 border border-gray-700 rounded overflow-hidden">
          <iframe src="${previewUrl}" class="w-full h-64 border-none" sandbox="allow-same-origin"></iframe>
        </div>
      `;
    } else if (isImage) {
      previewContent = `
        <div class="artifact-preview-img mt-2">
          <img src="${previewUrl}" alt="${filename}" class="max-h-64 rounded border border-gray-700" />
        </div>
      `;
    }

    return `
      <div class="harness-card harness-card-artifact border border-emerald-800/40 bg-emerald-950/20 p-3 rounded">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="card-icon text-emerald-400">📦</span>
            <span class="card-title font-medium text-emerald-200">${filename}</span>
            <span class="text-xs px-2 py-0.5 rounded bg-emerald-900/40 text-emerald-300 font-mono">${artifactType}</span>
          </div>
          <div class="artifact-actions flex gap-2">
            <a href="${previewUrl}" target="_blank" rel="noopener noreferrer" class="text-xs text-emerald-400 hover:underline">Open External ↗</a>
          </div>
        </div>
        ${previewContent}
      </div>
    `;
  }

  renderNotice(payload, _context) {
    const level = payload.level || 'info';
    const message = payload.message || payload.text || '';
    const levelCls = level === 'error' ? 'notice-error bg-red-950/30 border-red-800 text-red-200' :
                     level === 'warning' ? 'notice-warning bg-amber-950/30 border-amber-800 text-amber-200' :
                     level === 'success' ? 'notice-success bg-green-950/30 border-green-800 text-green-200' :
                     'notice-info bg-blue-950/30 border-blue-800 text-blue-200';

    return `
      <div class="harness-card harness-card-notice border p-3 rounded my-2 ${levelCls}">
        <div class="text-sm">${sanitize(message)}</div>
      </div>
    `;
  }

  renderApproval(payload, _context) {
    const reqId = escapeHtml(payload.request_id || payload.id || '');
    const action = escapeHtml(payload.action || payload.tool || payload.command || 'Action');
    const risk = escapeHtml(payload.risk_class || payload.risk || 'HIGH');
    const details = payload.details || payload.arguments ? JSON.stringify(payload.details || payload.arguments, null, 2) : '';

    return `
      <div class="harness-card harness-card-approval border-2 border-amber-500/70 bg-amber-950/30 p-4 rounded-lg my-3" data-request-id="${reqId}">
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center gap-2">
            <span class="text-xl">⚠️</span>
            <span class="font-bold text-amber-200 text-base">Approval Required: ${action}</span>
          </div>
          <span class="px-2 py-0.5 rounded text-xs font-bold uppercase bg-amber-600/30 text-amber-300 border border-amber-500/40">Risk: ${risk}</span>
        </div>
        ${details ? `<pre class="bg-gray-900/90 text-gray-200 p-2 rounded text-xs overflow-x-auto font-mono my-2 border border-gray-800">${escapeHtml(details)}</pre>` : ''}
        <div class="flex justify-end gap-3 mt-3">
          <button type="button" onclick="window.HarnessApp?.resolveApproval('${reqId}', 'denied')" class="px-3 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-200 text-sm font-medium">Deny</button>
          <button type="button" onclick="window.HarnessApp?.resolveApproval('${reqId}', 'approved')" class="px-4 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-white text-sm font-bold shadow">Approve</button>
        </div>
      </div>
    `;
  }

  renderFallback(card, _context) {
    const kind = escapeHtml(card.kind || 'unknown');
    const payloadStr = escapeHtml(JSON.stringify(card.payload || card, null, 2));

    return `
      <div class="harness-card harness-card-fallback border border-gray-700 bg-gray-800/40 p-3 rounded my-2">
        <div class="text-xs text-gray-400 font-mono mb-1">Card: ${kind}</div>
        <pre class="text-xs text-gray-300 overflow-x-auto font-mono">${payloadStr}</pre>
      </div>
    `;
  }
}

export const cardRenderer = new CardRenderer();
