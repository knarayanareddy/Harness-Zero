/**
 * The summary that replaced the compacted history. The live view gets a `compaction.completed` card with the
 * token counts; the text itself is what is worth re-reading later, so the log carries that.
 */
export default function renderCompactionSummary(app, rec) {
  app.appendCard({ kind: 'markdown', title: 'Context compacted', data: { body: rec.content || '' } });
}
