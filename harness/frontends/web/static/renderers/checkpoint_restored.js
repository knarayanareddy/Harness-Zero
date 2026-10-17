/** Make an approved restore visible in the conversation and refresh the Diffs projection. */
export default function renderCheckpointRestored(app, rec) {
  app.appendNotice(rec.content || 'Workspace restored to a checkpoint.', 'warn');
  app.panels?.refresh('diffs');
}
