import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from '../../node_modules/jsdom/lib/api.js';

const bootstrapDom = new JSDOM('<div id="timeline"></div>', { url: 'http://localhost/' });
globalThis.window = bootstrapDom.window;
globalThis.document = bootstrapDom.window.document;
const { HarnessStudioApp } = await import('../../harness/frontends/web/static/app.js');
bootstrapDom.window.close();

function makeApp(html = '') {
  const dom = new JSDOM(`
    <div id="timeline"></div>
    <button id="btn-send"></button><button id="btn-stop" class="hidden"></button>
    <div id="approval-banner" class="hidden"></div>
    <button id="btn-approval-approve"></button><button id="btn-approval-deny"></button>
    <div id="question-waiting" class="hidden"></div>
    <div id="onboarding-banner"></div>
    ${html}
  `, { url: 'http://localhost/' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  const app = new HarnessStudioApp();
  app.activeSessionId = 'B';
  app.setupEventHandlers();
  return { app, dom };
}

function event(app, type, sessionId, data) {
  const envelope = { type, data };
  if (sessionId !== undefined) envelope.session_id = sessionId;
  app.events._dispatch(type, envelope);
}

test('foreign-session lifecycle, stream, card, and approval events do not paint or change busy state', () => {
  const { app, dom } = makeApp();
  const timeline = document.getElementById('timeline');

  event(app, 'turn.started', 'A', {});
  event(app, 'model.text.delta', 'A', { text: 'FOREIGN_TEXT' });
  event(app, 'model.thinking.delta', 'A', { text: 'FOREIGN_THINKING' });
  event(app, 'tool.call', 'A', { call_id: 'call-A', name: 'foreign_tool', arguments: {} });
  event(app, 'tool.output.delta', 'A', { call_id: 'call-A', stream: 'stdout', text: 'FOREIGN_OUTPUT' });
  event(app, 'task.started', 'A', { task_id: 'task-A', label: 'foreign task' });
  event(app, 'ui.card', 'A', { card: { kind: 'notice', data: { body: 'FOREIGN_CARD' } } });
  event(app, 'approval.required', 'A', { approval_id: 'approval-A', kind: 'tool_execution', summary: 'FOREIGN_APPROVAL' });
  event(app, 'turn.error', 'A', { message: 'FOREIGN_ERROR' });

  assert.equal(timeline.textContent, '');
  assert.equal(app.isBusy, false);
  assert.equal(document.getElementById('approval-banner').classList.contains('hidden'), true);
  assert.equal(document.querySelector('.harness-card-tool'), null);
  assert.equal(document.querySelector('.harness-card-approval'), null);
  dom.window.close();
});

test('same-session stream and approval remain actionable; unscoped settings events remain global', async () => {
  const { app, dom } = makeApp();
  const resolutions = [];
  let settingsRefreshes = 0;
  app.api = {
    resolveApproval: async (...args) => resolutions.push(args),
  };
  app.updatePermissionBadge = () => { settingsRefreshes += 1; };
  app.bindControls();

  event(app, 'turn.started', 'B', {});
  event(app, 'model.text.delta', 'B', { text: 'B_TEXT' });
  event(app, 'model.thinking.delta', 'B', { text: 'B_THINKING' });
  event(app, 'tool.call', 'B', { call_id: 'call-B', name: 'safe_tool', arguments: {} });
  event(app, 'tool.output.delta', 'B', { call_id: 'call-B', stream: 'stdout', text: 'B_OUTPUT' });
  event(app, 'approval.required', 'B', { approval_id: 'approval-B', kind: 'tool_execution', summary: 'Approve B' });

  assert.equal(app.isBusy, true);
  assert.match(document.getElementById('timeline').textContent, /B_TEXT/);
  assert.match(document.getElementById('timeline').textContent, /B_THINKING/);
  assert.match(document.getElementById('timeline').textContent, /B_OUTPUT/);
  assert.equal(document.getElementById('approval-banner').getAttribute('data-request-id'), 'approval-B');
  document.getElementById('btn-approval-approve').click();
  assert.deepEqual(resolutions, [['approval-B', 'approved', '']]);

  event(app, 'settings.changed', undefined, { paths: ['kernel.security.permission_mode'] });
  assert.equal(settingsRefreshes, 1);
  event(app, 'system.notice', undefined, { message: 'WORKSPACE_NOTICE' });
  assert.match(document.getElementById('timeline').textContent, /WORKSPACE_NOTICE/);
  event(app, 'ui.card', undefined, { card: { kind: 'notice', data: { body: 'GLOBAL_CARD' } } });
  assert.match(document.getElementById('timeline').textContent, /GLOBAL_CARD/);
  let pluginEvents = 0;
  app.events.on('plugin.loaded', () => { pluginEvents += 1; });
  event(app, 'plugin.loaded', undefined, {
    name: 'workspace-plugin', version: '1', provides: [], capabilities: [], trust: 'trusted',
  });
  assert.equal(pluginEvents, 1);
  event(app, 'turn.completed', 'B', {});
  assert.equal(app.isBusy, false);
  dom.window.close();
});

test('session switch clears A transient state and a late A terminal event cannot unlock B', async () => {
  const { app, dom } = makeApp();
  const timeline = document.getElementById('timeline');
  app.activeSessionId = 'A';
  event(app, 'turn.started', 'A', {});
  event(app, 'model.text.delta', 'A', { text: 'A_TEXT' });
  event(app, 'question.asked', 'A', {
    interaction_id: 'question-A',
    questions: [{ id: 'q', question: 'Question for A', options: [{ id: 'yes', label: 'Yes' }] }],
  });
  event(app, 'approval.required', 'A', { approval_id: 'approval-A', kind: 'tool_execution', summary: 'Approve A' });
  assert.equal(app.isBusy, true);
  assert.equal(app.pendingQuestions.has('question-A'), true);
  assert.equal(document.getElementById('question-waiting').classList.contains('hidden'), false);

  let resolveHistory;
  let historyRequested;
  const requested = new Promise((resolve) => { historyRequested = resolve; });
  const renderedB = new Promise((resolve) => {
    app.renderers = { render: (_app, record) => resolve(record) };
  });
  app.api.command = (name, args) => {
    assert.equal(name, 'sessions.get');
    historyRequested(args.session_id);
    return new Promise((resolve) => { resolveHistory = resolve; });
  };

  event(app, 'session.switched', 'B', { session_id: 'B' });
  assert.equal(app.activeSessionId, 'B');
  assert.equal(app.isBusy, false);
  assert.equal(app.currentAssistantBlock, null);
  assert.equal(app.pendingQuestions.size, 0);
  assert.equal(document.getElementById('question-waiting').classList.contains('hidden'), true);
  assert.equal(document.getElementById('approval-banner').classList.contains('hidden'), true);
  assert.equal(timeline.textContent, '');
  assert.equal(await requested, 'B');

  event(app, 'turn.started', 'B', {});
  event(app, 'model.text.delta', 'B', { text: 'B_TEXT' });
  event(app, 'approval.required', 'B', { approval_id: 'approval-B', kind: 'tool_execution', summary: 'Approve B' });
  event(app, 'turn.cancelled', 'A', { reason: 'late A cancel' });
  event(app, 'turn.completed', 'A', {});
  event(app, 'turn.error', 'A', { message: 'late A error' });
  event(app, 'model.text.delta', 'A', { text: 'LATE_A_TEXT' });
  event(app, 'approval.resolved', 'A', { approval_id: 'approval-B', approved: true });
  assert.equal(app.isBusy, true);
  assert.equal(document.getElementById('approval-banner').getAttribute('data-request-id'), 'approval-B');
  assert.match(timeline.textContent, /B_TEXT/);
  assert.doesNotMatch(timeline.textContent, /LATE_A_TEXT|late A cancel|late A error/);

  resolveHistory({ records: [{ kind: 'user.message', content: 'B_HISTORY' }] });
  const record = await renderedB;
  assert.equal(record.content, 'B_HISTORY');
  dom.window.close();
});

test('session-only events without a session identity cannot affect the selected conversation', () => {
  const { app, dom } = makeApp();
  for (const sessionId of [undefined, null]) {
    event(app, 'turn.started', sessionId, {});
    event(app, 'model.text.delta', sessionId, { text: 'UNSCOPED_STREAM' });
    event(app, 'model.thinking.delta', sessionId, { text: 'UNSCOPED_THINKING' });
    event(app, 'approval.required', sessionId, { approval_id: 'unscoped', kind: 'tool_execution', summary: 'UNSCOPED_APPROVAL' });
    assert.equal(app.isBusy, false);
    assert.equal(document.getElementById('timeline').textContent, '');
    assert.equal(document.getElementById('approval-banner').classList.contains('hidden'), true);
    event(app, 'turn.started', 'B', {});
    event(app, 'turn.completed', sessionId, {});
    event(app, 'turn.cancelled', sessionId, { reason: 'UNSCOPED_CANCEL' });
    event(app, 'turn.error', sessionId, { message: 'UNSCOPED_ERROR' });
    assert.equal(app.isBusy, true);
    event(app, 'turn.completed', 'B', {});
    document.getElementById('timeline').replaceChildren();
  }
  dom.window.close();
});
