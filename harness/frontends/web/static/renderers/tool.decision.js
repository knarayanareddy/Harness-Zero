/**
 * The policy's verdict on a call, with its reason — the part that used to be dropped on reload.
 */
export default function renderToolDecision(app, rec) {
  const meta = rec.metadata || {};
  app.updateToolDecision({
    call_id: meta.call_id,
    action: meta.action,
    policy: meta.policy,
    reason: meta.reason,
    plugin: meta.plugin,
    risk: meta.risk,
  });
}
