/**
 * An assistant turn: its text as a finished block, then its calls.
 *
 * Both halves are painted the way the live stream paints them, so a reload of a long turn looks like the turn
 * the user watched. The calls are added with status `running` on purpose — the verdict belongs to the next
 * records, exactly as it does live, which is also why a call whose result never arrived stays open instead of
 * claiming success.
 */
export default function renderAssistantMessage(app, rec) {
  if (rec.content) {
    app.appendTurnBlock('assistant');
    app.streamAssistantText(rec.content);
    app.finalizeStreamingBlock();
  }
  for (const tc of rec.tool_calls || []) {
    app.appendToolCall({ call_id: tc.id, name: tc.name, arguments: tc.arguments || {}, status: 'running' });
  }
}
