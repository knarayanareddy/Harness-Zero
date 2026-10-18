/**
 * A failed turn.
 */
export default function renderTurnError(app, rec) {
  app.appendNotice(rec.content || 'Turn failed.', 'error');
}
