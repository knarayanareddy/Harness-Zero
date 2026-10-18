/** Jobs feature UI, loaded only while the tools-jobs plugin is enabled. */

const LIST_COMMAND = 'plugins.tools-jobs.list';
const OUTPUT_COMMAND = 'plugins.tools-jobs.output';
const KILL_COMMAND = 'plugins.tools-jobs.kill';

const jobsPanel = {
  async render(ctx) {
    const host = ctx.host;
    if (!host) return;
    bindOnce(ctx);
    const sessionId = ctx.app.activeSessionId;
    if (!sessionId) {
      host.innerHTML = '<div class="job-empty">Start or select a session to see its jobs.</div>';
      return;
    }

    let rows;
    try {
      rows = (await ctx.api.command(LIST_COMMAND, { session_id: sessionId })) || [];
    } catch (err) {
      host.innerHTML = `<div class="job-empty">The kernel could not list jobs: ${ctx.escape(String(err?.message || err))}</div>`;
      return;
    }

    const running = rows.filter((row) => row.running).length;
    const cards = rows.map((row) => jobCard(ctx, row)).join('');
    host.innerHTML = `
      <div class="jobs-summary">
        <div><strong>${running}</strong> running · <strong>${rows.length}</strong> retained</div>
        <button type="button" class="job-refresh" data-refresh-jobs>Refresh</button>
      </div>
      <div class="jobs-command-status" role="status" aria-live="polite"></div>
      ${cards || '<div class="job-empty">No jobs yet. Use job_start or start a long command with run_command.</div>'}`;
  },
};

export const panels = { jobs: jobsPanel };

function jobCard(ctx, row) {
  const jobId = String(row.job_id || '');
  const escapedId = ctx.escape(jobId);
  const args = ctx.escapeAttr(JSON.stringify({ session_id: '@active', job_id: jobId }));
  const state = row.running ? 'running' : (row.reason === 'lost' ? 'lost' : 'exited');
  const kill = row.running
    ? `<button type="button" class="job-kill" data-command="${KILL_COMMAND}" data-args="${args}">Stop job</button>`
    : '';
  const exit = row.exit_code == null ? '—' : String(row.exit_code);
  const output = row.tail || '';
  const started = row.started_at ? new Date(Number(row.started_at) * 1000).toLocaleTimeString() : '';
  const spill = row.spill_path ? `<div class="job-spill">Output: <code>${ctx.escape(row.spill_path)}</code></div>` : '';
  return `
    <article class="job-card" data-job-id="${ctx.escapeAttr(jobId)}" data-state="${ctx.escapeAttr(state)}">
      <header class="job-card-head">
        <div>
          <h3>${ctx.escape(row.label || jobId)}</h3>
          <code class="job-id">${escapedId}</code>
        </div>
        <span class="job-state" data-state="${ctx.escapeAttr(state)}">${ctx.escape(state)}</span>
      </header>
      <div class="job-command"><code>${ctx.escape(row.command || '')}</code></div>
      <div class="job-meta">${started ? `Started ${ctx.escape(started)} · ` : ''}Exit: ${ctx.escape(exit)}${row.reason ? ` · ${ctx.escape(row.reason)}` : ''}</div>
      <pre class="job-tail">${ctx.escape(output || '(waiting for output)')}</pre>
      ${spill}
      <footer class="job-actions">
        <button type="button" class="job-output" data-job-output="${ctx.escapeAttr(jobId)}">Load recent output</button>
        ${kill}
      </footer>
      <pre class="job-full-output hidden" data-job-output-view></pre>
    </article>`;
}

function bindOnce(ctx) {
  const host = ctx.host;
  if (host.dataset.jobsBound === '1') return;
  host.dataset.jobsBound = '1';

  host.addEventListener('click', async (event) => {
    const refresh = event.target.closest?.('[data-refresh-jobs]');
    if (refresh) {
      await ctx.app.panels?.refresh('jobs');
      return;
    }
    const button = event.target.closest?.('[data-job-output]');
    if (!button) return;
    const card = button.closest('.job-card');
    const view = card?.querySelector('[data-job-output-view]');
    const jobId = button.getAttribute('data-job-output');
    const sessionId = ctx.app.activeSessionId;
    if (!view || !jobId || !sessionId) return;
    button.disabled = true;
    try {
      const result = await ctx.api.command(OUTPUT_COMMAND, {
        session_id: sessionId,
        job_id: jobId,
        tail_lines: 200,
      });
      view.textContent = result?.output || '(no output)';
      view.classList.remove('hidden');
    } catch (err) {
      view.textContent = String(err?.message || err);
      view.classList.remove('hidden');
    } finally {
      button.disabled = false;
    }
  });

  document.addEventListener('harness:command-done', async (event) => {
    if (event.detail?.command !== KILL_COMMAND) return;
    await ctx.app.panels?.refresh('jobs');
    const status = host.querySelector('.jobs-command-status');
    const result = event.detail?.result;
    if (status) {
      status.textContent = result?.ok
        ? 'Job stopped.'
        : `Stop not completed: ${result?.reason || 'approval was denied or the job already exited'}.`;
    }
  });
}
