/**
 * How that approval ended — the decision and who made it.
 */
export default function renderApprovalResolved(app, rec) {
  const meta = rec.metadata || {};
  app.resolveApprovalUI({
    approval_id: meta.approval_id,
    approved: !!meta.approved,
    by: meta.by,
    reason: meta.reason,
  });
}
