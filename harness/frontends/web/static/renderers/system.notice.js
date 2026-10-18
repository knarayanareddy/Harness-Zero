/**
 * A notice the harness asked the user to see (`level` decides the tone).
 */
export default function renderSystemNotice(app, rec) {
  const meta = rec.metadata || {};
  if (rec.content) app.appendNotice(rec.content, meta.level || 'info');
}
