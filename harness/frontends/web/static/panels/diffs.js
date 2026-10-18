/** Session-scoped workspace-change review, on-demand diffs, and approval-gated restore. */
const CHANGE_COMMAND = 'plugins.hook-checkpoints.changes';
const DIFF_COMMAND = 'plugins.hook-checkpoints.diff';
const RESTORE_COMMAND = 'plugins.hook-checkpoints.restore';
const FULL_SHA = /^[0-9a-f]{40}$/;

export default {
  async render(ctx) {
    const host = ctx.host;
    const sessionId = ctx.app.activeSessionId;
    if (!host) return;
    bindOnce(host);
    if (!sessionId) {
      host.innerHTML = '<div class="diffs-empty">Start or select a session to review workspace changes.</div>';
      host.__diffsState = { ctx, rows: [] };
      return;
    }

    host.innerHTML = '<div class="diffs-loading">Loading workspace changes…</div>';
    try {
      const result = await ctx.api.command(CHANGE_COMMAND, { session_id: sessionId });
      const records = Array.isArray(result?.changes) ? result.changes : [];
      const restored = Array.isArray(result?.restored) ? result.restored : [];
      const rows = records.map((record) => ({
        record,
        metadata: record?.metadata || {},
        files: Array.isArray(record?.metadata?.files) ? record.metadata.files : [],
      })).reverse();
      host.__diffsState = { ctx, rows, sessionId };
      host.innerHTML = renderRows(ctx, rows, restored);
    } catch (err) {
      host.__diffsState = { ctx, rows: [], sessionId };
      host.innerHTML = `<div class="diffs-error">Could not load workspace changes: ${ctx.escape(err?.message || err)}</div>`;
    }
  },
};

function renderRows(ctx, rows, restored) {
  if (!rows.length) {
    return '<div class="diffs-empty">No workspace changes have been recorded for this session yet.</div>';
  }
  const restoreNote = restored.length
    ? `<p class="diffs-restore-note">${restored.length} checkpoint restore${restored.length === 1 ? '' : 's'} recorded.</p>`
    : '';
  const cards = rows.map(({ record, metadata, files }, changeIndex) => {
    const data = record?.data || {};
    const turnId = String(metadata.turn_id || data.turn_id || 'turn');
    const base = metadata.base || data.base || '';
    const countValue = Number(metadata.total_files);
    const count = Number.isFinite(countValue) ? countValue : files.length;
    const added = Number(metadata.added ?? data.added) || 0;
    const removed = Number(metadata.removed ?? data.removed) || 0;
    const truncated = Boolean(metadata.truncated ?? data.truncated);
    const skipped = Boolean(metadata.snapshot_skipped ?? data.snapshot_skipped);
    const title = skipped
      ? 'Workspace changes could not be summarized (snapshot size limit or checkpoint failure).'
      : count === 0
        ? 'No workspace changes this turn.'
        : `${count}${truncated ? '+' : ''} file${count === 1 ? '' : 's'} changed · +${added} −${removed}`;
    const fileRows = files.map((file, fileIndex) => {
      const key = `${changeIndex}:${fileIndex}`;
      const status = String(file.status || 'modified');
      const path = String(file.path || '');
      const rename = file.old_path ? ` <span class="diff-file-old">from ${ctx.escape(file.old_path)}</span>` : '';
      const stats = file.binary
        ? '<span class="diff-binary">binary</span>'
        : `<span class="diff-added">+${Number(file.added_lines) || 0}</span> <span class="diff-removed">−${Number(file.removed_lines) || 0}</span>`;
      const disabled = !FULL_SHA.test(String(base)) || !FULL_SHA.test(String(metadata.head || data.head || ''));
      return `
        <div class="diff-file-row">
          <button type="button" class="diff-file-button" data-diff-key="${key}" ${disabled || file.binary ? 'disabled' : ''}>
            <span class="diff-file-status">${ctx.escape(status)}</span>
            <span class="diff-file-path">${ctx.escape(path)}</span>${rename}
            <span class="diff-file-stats">${stats}</span>
          </button>
          <pre class="diff-file-content hidden" data-diff-content="${key}" aria-live="polite"></pre>
        </div>`;
    }).join('');
    const head = metadata.head || data.head || '';
    const restore = FULL_SHA.test(String(base)) && String(base) !== String(head)
      ? `<button type="button" class="diff-restore-button" data-restore-key="${changeIndex}">Restore to before this turn</button>`
      : '';
    const truncation = truncated
      ? `<p class="diffs-truncated">Only the first ${files.length} changed files are listed here; open a file diff to inspect the bounded text patch.</p>`
      : '';
    return `
      <section class="diff-change-card" data-turn-id="${ctx.escapeAttr(turnId)}">
        <header class="diff-change-heading">
          <strong>${ctx.escape(title)}</strong>
          <span class="diff-change-turn">${ctx.escape(turnId)}</span>
        </header>
        ${record?.content ? `<p class="diff-change-content">${ctx.escape(record.content)}</p>` : ''}
        ${skipped ? '<p class="diffs-warning">The checkpoint size guard is active; no complete per-turn diff is available.</p>' : ''}
        ${fileRows || '<p class="diffs-empty">No file-level changes in this snapshot.</p>'}
        ${truncation}
        ${restore}
        <p class="diff-action-status" data-action-status="${changeIndex}" role="status"></p>
      </section>`;
  }).join('');
  return `${restoreNote}<div class="diffs-list">${cards}</div>`;
}

function bindOnce(host) {
  if (host.dataset.diffsBound === '1') return;
  host.dataset.diffsBound = '1';
  host.addEventListener('click', async (event) => {
    const button = event.target.closest?.('[data-diff-key], [data-restore-key]');
    if (!button || button.disabled) return;
    const state = host.__diffsState;
    if (!state?.ctx) return;
    const ctx = state.ctx;
    const diffKey = button.getAttribute('data-diff-key');
    if (diffKey) {
      const [changeIndex, fileIndex] = diffKey.split(':').map(Number);
      const row = state.rows?.[changeIndex];
      const file = row?.files?.[fileIndex];
      if (!row || !file || !FULL_SHA.test(String(row.metadata.base || row.record?.data?.base || '')) || !FULL_SHA.test(String(row.metadata.head || row.record?.data?.head || ''))) return;
      const output = host.querySelector(`[data-diff-content="${diffKey}"]`);
      if (!output) return;
      button.disabled = true;
      try {
        const result = await ctx.api.command(DIFF_COMMAND, {
          path: file.path,
          base: row.metadata.base || row.record.data.base,
          head: row.metadata.head || row.record.data.head,
        });
        output.textContent = result.binary
          ? 'Binary file changed; no text diff is available.'
          : String(result.diff || '(No textual diff.)');
        output.classList.remove('hidden');
      } catch (err) {
        output.textContent = `Could not load diff: ${String(err?.message || err)}`;
        output.classList.remove('hidden');
      } finally {
        button.disabled = false;
      }
      return;
    }

    const changeIndex = Number(button.getAttribute('data-restore-key'));
    const row = state.rows?.[changeIndex];
    const base = row?.metadata?.base || row?.record?.data?.base;
    const status = host.querySelector(`[data-action-status="${changeIndex}"]`);
    if (!row || !FULL_SHA.test(String(base)) || !state.sessionId) return;
    button.disabled = true;
    if (status) status.textContent = 'Waiting for explicit user approval…';
    try {
      const result = await ctx.api.command(RESTORE_COMMAND, { session_id: state.sessionId, sha: base });
      if (status) {
        status.textContent = result.status === 'restored'
          ? `Restored to ${String(result.sha || base).slice(0, 8)} (${Number(result.files_touched) || 0} file(s) changed).`
          : `${String(result.status || 'restore failed')}: ${String(result.reason || '')}`;
      }
      await ctx.app.panels?.refresh('diffs');
    } catch (err) {
      if (status) status.textContent = `Restore failed: ${String(err?.message || err)}`;
    } finally {
      button.disabled = false;
    }
  });
}
