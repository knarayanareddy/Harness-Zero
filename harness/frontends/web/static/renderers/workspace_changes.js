/** A compact, navigable summary of one turn's durable workspace delta. */
export default function renderWorkspaceChanges(app, rec) {
  const timeline = document.getElementById('timeline');
  if (!timeline) return;

  const meta = rec.metadata || {};
  const files = Array.isArray(meta.files) ? meta.files : [];
  const count = Number.isFinite(Number(meta.total_files)) ? Number(meta.total_files) : files.length;
  const summary = rec.content || (count ? `${count} files changed` : 'No workspace changes this turn.');

  const card = document.createElement('article');
  card.className = 'harness-card notice workspace-changes-card';
  card.dataset.kind = 'workspace.changes';
  card.dataset.level = meta.snapshot_skipped ? 'warn' : 'info';

  const body = document.createElement('div');
  body.className = 'card-body notice-body text-sm';
  const heading = document.createElement('strong');
  heading.textContent = summary;
  body.appendChild(heading);

  if (meta.truncated) {
    const note = document.createElement('p');
    note.className = 'text-xs workspace-changes-truncated';
    note.textContent = `${count}+ files; showing the bounded summary. Open Diffs for the available file list.`;
    body.appendChild(note);
  } else if (count > 0 && files.length) {
    const preview = document.createElement('p');
    preview.className = 'text-xs workspace-changes-preview';
    preview.textContent = files.slice(0, 3).map((file) => file.path).join(' · ');
    body.appendChild(preview);
  }

  if (app.panels?.declaredIds?.().includes('diffs')) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'workspace-changes-open';
    button.textContent = 'Review in Diffs';
    button.addEventListener('click', () => app.switchStudioTab('diffs'));
    body.appendChild(button);
  }
  card.appendChild(body);
  timeline.appendChild(card);
  timeline.scrollTop = timeline.scrollHeight;
}
