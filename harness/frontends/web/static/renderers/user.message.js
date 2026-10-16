/**
 * A prompt. `content` is the text, as the live path renders it too; a legacy record can carry its tool results
 * inline, and those close the matching tool card.
 */
import { textContentOf } from '../registry.js';

export default function renderUserMessage(app, rec) {
  if (rec.content) app.appendUserTurn(rec.content);
  for (const tr of rec.tool_results || []) {
    app.updateToolResult({ call_id: tr.tool_use_id, is_error: !!tr.is_error, preview: textContentOf(tr.content) });
  }
}
