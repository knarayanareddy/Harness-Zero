/** Open a durable deliverable in the Artifacts viewer, using a sandboxed file frame. */
export default function renderDeliverable(app, rec) {
  const meta = rec.metadata || {};
  const rawPath = String(meta.path || '');
  const kind = meta.kind === 'artifact' ? 'artifact' : 'file';
  const path = rawPath.replaceAll('\\', '/');
  const segments = path.split('/');
  if (!path || path.startsWith('/') || segments.some((part) => !part || part === '.' || part === '..')) {
    app.appendNotice('A recorded deliverable path is invalid and cannot be opened.', 'warn');
    return;
  }

  const name = segments[segments.length - 1];
  const url = kind === 'artifact'
    ? `/artifacts/${encodeURIComponent(name)}`
    : `/workspace-files/${segments.map((part) => encodeURIComponent(part)).join('/')}`;
  const host = document.getElementById('studio-content-area');
  if (!host) return;

  const card = document.createElement('article');
  card.className = 'deliverable-viewer';
  card.dataset.kind = kind;
  card.dataset.path = path;

  const heading = document.createElement('div');
  heading.className = 'deliverable-viewer-heading';
  const title = document.createElement('strong');
  title.textContent = name;
  heading.appendChild(title);
  const caption = document.createElement('span');
  caption.className = 'deliverable-viewer-caption';
  caption.textContent = String(meta.caption || '');
  heading.appendChild(caption);
  const link = document.createElement('a');
  link.href = url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = 'Open ↗';
  heading.appendChild(link);
  card.appendChild(heading);

  const iframe = document.createElement('iframe');
  iframe.className = 'deliverable-viewer-frame';
  iframe.src = url;
  iframe.setAttribute('sandbox', '');
  iframe.referrerPolicy = 'no-referrer';
  iframe.loading = 'lazy';
  iframe.title = `${name} — ${String(meta.caption || 'presented deliverable')}`;
  card.appendChild(iframe);

  const facts = document.createElement('div');
  facts.className = 'deliverable-viewer-facts';
  const size = Number(meta.size);
  facts.textContent = `${Number.isFinite(size) ? `${size} bytes` : 'size unknown'} · SHA-256 ${String(meta.sha256 || 'unknown')}`;
  card.appendChild(facts);

  const status = document.createElement('div');
  status.className = 'deliverable-viewer-status';
  status.setAttribute('role', 'status');
  status.textContent = 'Checking file…';
  card.appendChild(status);
  fetch(url, { method: 'HEAD', credentials: 'same-origin' })
    .then((response) => {
      status.textContent = response.ok ? '' : 'This deliverable is missing or no longer available.';
      if (!response.ok) iframe.remove();
    })
    .catch(() => {
      status.textContent = 'This deliverable could not be opened.';
      iframe.remove();
    });

  host.prepend(card);
  app.switchStudioTab('artifact');
}
