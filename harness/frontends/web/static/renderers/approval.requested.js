/**
 * A still-unanswered approval: re-rendered as the interactive card it was, keyed by `approval_id` so the
 * Resolve path works from a reloaded session too. `kind` is the policy's `approval_kind`.
 */
export default function renderApprovalRequested(app, rec) {
  const meta = rec.metadata || {};
  app.showApproval({
    approval_id: meta.approval_id,
    call_id: meta.call_id,
    kind: meta.approval_kind,
    summary: rec.content,
    details: meta.details || {},
    timeout_s: meta.timeout_s,
    options: meta.options || ['approve', 'deny'],
  });
}
