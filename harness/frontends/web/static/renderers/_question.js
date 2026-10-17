/** Shared painters for durable question.asked and question.answered records. */
export function renderQuestionAsked(app, record) {
  const meta = record.metadata || {};
  app.showQuestion({
    interaction_id: meta.interaction_id,
    call_id: meta.call_id,
    questions: meta.questions || [],
    timeout_s: meta.timeout_s,
  });
}

export function renderQuestionAnswered(app, record) {
  const meta = record.metadata || {};
  app.resolveQuestionUI({
    interaction_id: meta.interaction_id,
    answers: meta.answers || {},
    status: meta.status || 'answered',
    by: meta.by || '',
    reason: meta.reason || '',
  });
}
