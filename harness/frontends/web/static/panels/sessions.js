/**
 * The Sessions pane: the kernel's session rows, one line each.
 *
 * Extracted from `app.js` so the pane is owned here — `PanelRegistry.activate('sessions')` calls this, and the
 * app keeps only the cross-cutting part (adopting the active id and updating the header, via `ctx.select` /
 * `ctx.app.noteSessionRows`).
 */
export default {
  host: 'session-list',
  async render(ctx) {
    const list = ctx.host;
    if (!list) return;
    try {
      // Rows are SessionRow (harness/sdk/types.py): {session_id, workspace, created_at, updated_at, title,
      // turns, message_count, active, busy}; the kernel marks the active one.
      const sessions = await ctx.api.getSessions();
      ctx.app.noteSessionRows?.(sessions || []);
      list.innerHTML = '';
      if (!sessions || sessions.length === 0) {
        list.innerHTML = '<div class="session-empty text-xs">No sessions yet — your first message starts one.</div>';
        return;
      }
      for (const s of sessions) {
        const item = document.createElement('div');
        const isActive = s.session_id === ctx.app.activeSessionId;
        item.className = `session-item ${isActive ? 'active' : ''}`;
        item.dataset.sessionId = s.session_id;
        const when = s.updated_at ? new Date(s.updated_at * 1000).toLocaleString() : '';
        const turns = `${s.turns || 0} turn${s.turns === 1 ? '' : 's'}`;
        item.innerHTML = `
          <div class="session-title truncate">${ctx.escape(s.title || 'New session')}</div>
          <div class="session-meta text-xs">
            <span class="session-id font-mono">${ctx.escape(s.session_id)}</span>
            <span class="session-turns">${ctx.escape(turns)}</span>
            <span class="session-when">${ctx.escape(when)}</span>
            <span class="session-busy">${s.busy ? '⏳' : ''}</span>
          </div>
        `;
        item.addEventListener('click', () => ctx.select(s.session_id));
        list.appendChild(item);
      }
    } catch (err) {
      console.warn('Failed to load sessions:', err);
    }
  },
};
