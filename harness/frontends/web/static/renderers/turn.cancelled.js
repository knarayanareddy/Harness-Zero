/**
 * A cancelled turn, with the reason when there is one.
 */
export default function renderTurnCancelled(app, rec) {
  app.appendNotice(rec.content || 'Turn cancelled.', 'warn');
}
