/**
 * Tool output, painted into the card the call opened (`call_id` is the join).
 */
import { textContentOf } from '../registry.js';

export default function renderToolResult(app, rec) {
  for (const tr of rec.tool_results || []) {
    app.updateToolResult({ call_id: tr.tool_use_id, is_error: !!tr.is_error, preview: textContentOf(tr.content) });
  }
}
