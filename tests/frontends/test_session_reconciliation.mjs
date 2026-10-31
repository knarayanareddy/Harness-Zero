import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from '../../node_modules/jsdom/lib/api.js';
import sessionsPanel from '../../harness/frontends/web/static/panels/sessions.js';
import { readFile } from 'node:fs/promises';

test('timeline cards retain intrinsic height instead of clipping overflow content', async () => {
  // Static regression guard; the actual geometry is verified separately in Chromium.
  const css = await readFile(new URL('../../harness/frontends/web/static/styles.css', import.meta.url), 'utf8');
  assert.match(css, /\.timeline-stream\s*>\s*\*\s*\{[^}]*flex-shrink:\s*0\s*;/);
});

// Import the shipped app before a real DOM exists so its page bootstrap listener cannot start a second app.
globalThis.window = {};
globalThis.document = { addEventListener() {}, querySelector() { return null; } };
const { HarnessStudioApp } = await import('../../harness/frontends/web/static/app.js');
await import('./test_session_event_scope.mjs');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function record(step, kind, content = '', metadata = {}, extra = {}) {
  const role = kind === 'user.message' || kind === 'tool.result' ? 'user'
    : kind === 'assistant.message' ? 'assistant' : 'system';
  return { kind, role, content, metadata, step_index: step, ...extra };
}

async function makeApp(apiOverrides = {}) {
  const dom = new JSDOM(`
    <div id="timeline"></div>
    <div id="session-list"></div>
    <div id="active-session-display"></div>
    <form id="composer-form"><input id="composer-input" /><button id="btn-send"></button></form>
    <button id="btn-stop" class="hidden"></button>
    <div id="approval-banner" class="hidden"></div>
    <button id="btn-approval-approve"></button><button id="btn-approval-deny"></button>
    <div id="question-waiting" class="hidden"></div>
    <div id="onboarding-banner"></div>
  `, { url: 'http://localhost/' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  const app = new HarnessStudioApp();
  app.activeSessionId = 'B';
  app.api = {
    command: async (name) => name === 'ui.contributions'
      ? { panels: [{ id: 'artifact' }, { id: 'sessions' }, { id: 'economics' }, { id: 'plugins' }], record_renderers: {}, record_kinds: [], client_modules: [] }
      : {},
    getSessions: async () => [{ session_id: 'B', title: 'B', active: true }],
    ...apiOverrides,
  };
  await app.setupRegistries();
  app.setupEventHandlers();
  return { app, dom };
}

function event(app, type, sessionId, data = {}, extra = {}) {
  const envelope = { type, data, ...extra };
  if (sessionId !== undefined) envelope.session_id = sessionId;
  app.events._dispatch(type, envelope);
}

test('late A history cannot paint B after a session switch', async () => {
  const history = { A: deferred(), B: deferred() };
  const { app, dom } = await makeApp({
    command: async (name, args) => {
      if (name === 'ui.contributions') return { panels: [{ id: 'artifact' }, { id: 'sessions' }, { id: 'economics' }, { id: 'plugins' }], record_renderers: {}, record_kinds: [], client_modules: [] };
      assert.equal(name, 'sessions.get');
      return history[args.session_id].promise;
    },
  });
  app.activeSessionId = 'A';
  const aLoad = app.reloadTranscript();
  event(app, 'session.switched', 'B', { session_id: 'B' });
  history.B.resolve({ records: [{ kind: 'user.message', content: 'B_HISTORY' }], busy: false });
  await Promise.resolve();
  history.A.resolve({ records: [{ kind: 'user.message', content: 'A_HISTORY' }], busy: false });
  await Promise.all([aLoad, Promise.resolve()]);
  assert.match(document.getElementById('timeline').textContent, /B_HISTORY/);
  assert.doesNotMatch(document.getElementById('timeline').textContent, /A_HISTORY/);
  assert.equal(app.activeSessionId, 'B');
  dom.window.close();
});

test('newer same-session history responses supersede older requests', async () => {
  const requests = [deferred(), deferred()];
  let requestIndex = 0;
  const { app, dom } = await makeApp({
    command: async (name) => {
      if (name === 'ui.contributions') return { panels: [{ id: 'artifact' }, { id: 'sessions' }, { id: 'economics' }, { id: 'plugins' }], record_renderers: {}, record_kinds: [], client_modules: [] };
      assert.equal(name, 'sessions.get');
      return requests[requestIndex++].promise;
    },
  });
  const older = app.reloadTranscript();
  const newer = app.reloadTranscript();
  requests[1].resolve({ records: [record(8, 'user.message', 'B_NEWER')], busy: false });
  await newer;
  requests[0].resolve({ records: [record(2, 'user.message', 'B_OLDER')], busy: false });
  await older;
  assert.match(document.getElementById('timeline').textContent, /B_NEWER/);
  assert.doesNotMatch(document.getElementById('timeline').textContent, /B_OLDER/);
  dom.window.close();
});

test('snapshot reconciliation deduplicates stable records but retains identical legitimate prompts', async () => {
  const { app, dom } = await makeApp();
  const repeatedPrompt = record(1, 'user.message', 'same prompt');
  const secondPrompt = record(2, 'user.message', 'same prompt');
  app.renderRecord(repeatedPrompt);
  app.renderRecord(secondPrompt);
  app.renderRecord(repeatedPrompt);
  assert.equal(document.querySelectorAll('#timeline .turn-user').length, 2);
  assert.equal([...document.querySelectorAll('#timeline .turn-user')]
    .filter((node) => node.textContent.trim() === 'same prompt').length, 2);
  dom.window.close();
});

test('a new live prompt cannot reuse an identical already-canonical history bubble', async () => {
  const { app, dom } = await makeApp();
  app.renderRecord(record(1, 'user.message', 'same legitimate prompt'));
  event(app, 'turn.started', 'B', {}, {
    turn_id: 'new-turn', record: record(2, 'user.message', 'same legitimate prompt'),
  });
  const users = [...document.querySelectorAll('#timeline .turn-user')];
  assert.equal(users.length, 2);
  assert.deepEqual(users.map((node) => node.dataset.recordKey), ['["B",1]', '["B",2]']);
  dom.window.close();
});

test('a provisional prompt is only a placeholder and never a content-matching identity', async () => {
  const { app, dom } = await makeApp();
  app.appendUserTurn('unacknowledged local preview');
  event(app, 'turn.started', 'B', {}, {
    turn_id: 'canonical-turn', record: record(5, 'user.message', 'actual canonical prompt'),
  });
  const users = [...document.querySelectorAll('#timeline .turn-user')];
  assert.equal(users.length, 1);
  assert.equal(users[0].querySelector('.message-bubble').textContent, 'actual canonical prompt');
  assert.equal(users[0].dataset.recordKey, '["B",5]');
  dom.window.close();
});

test('late history merges with B streaming nodes and keeps the live tool output', async () => {
  const history = deferred();
  const { app, dom } = await makeApp({
    command: async (name) => {
      if (name === 'ui.contributions') return { panels: [], record_renderers: {}, record_kinds: [], client_modules: [] };
      assert.equal(name, 'sessions.get');
      return history.promise;
    },
  });
  app.appendUserTurn('B prompt');
  const userRecord = record(1, 'user.message', 'B prompt');
  event(app, 'turn.started', 'B', {}, { turn_id: 'turn-B-1', record: userRecord });
  event(app, 'model.text.delta', 'B', { text: 'live answer' }, { turn_id: 'turn-B-1' });
  event(app, 'tool.call', 'B', { call_id: 'call-B', name: 'inspect', arguments: {} }, { turn_id: 'turn-B-1' });
  event(app, 'tool.output.delta', 'B', { call_id: 'call-B', stream: 'stdout', text: 'live tool output' }, { turn_id: 'turn-B-1' });
  const assistantRecord = record(2, 'assistant.message', 'live answer', {}, {
    tool_calls: [{ id: 'call-B', name: 'inspect', arguments: {} }],
  });
  event(app, 'model.completed', 'B', {}, { turn_id: 'turn-B-1', record: assistantRecord });
  const loading = app.reloadTranscript();
  history.resolve({ records: [userRecord, assistantRecord], busy: true });
  await loading;
  const timeline = document.getElementById('timeline');
  assert.equal(timeline.querySelectorAll('.turn-user').length, 1);
  assert.equal(timeline.querySelectorAll('.turn-assistant').length, 1);
  assert.equal(timeline.querySelectorAll('.harness-card-tool[data-call-id="call-B"]').length, 1);
  assert.match(timeline.textContent, /live tool output/);
  assert.equal(app.isBusy, true);
  dom.window.close();
});

test('deferred history restores canonical step order without moving a shared live tool card', async () => {
  const history = deferred();
  const { app, dom } = await makeApp({ command: async (name) => {
    if (name === 'ui.contributions') return { panels: [], record_renderers: {}, record_kinds: [], client_modules: [] };
    assert.equal(name, 'sessions.get');
    return history.promise;
  } });
  const loading = app.reloadTranscript();
  event(app, 'tool.call', 'B', { call_id: 'chronological-call', name: 'inspect', arguments: {} });
  event(app, 'tool.output.delta', 'B', { call_id: 'chronological-call', stream: 'stdout', text: 'preserved live output' });
  event(app, 'model.text.delta', 'B', { text: 'latest live answer' });
  const liveAssistant = record(5, 'assistant.message', 'latest live answer', {}, {
    tool_calls: [{ id: 'chronological-call', name: 'inspect', arguments: {} }],
  });
  event(app, 'model.completed', 'B', {}, { record: liveAssistant });
  const toolCard = document.querySelector('.harness-card-tool[data-call-id="chronological-call"]');
  event(app, 'tool.result', 'B', { call_id: 'chronological-call', name: 'inspect', is_error: false }, {
    record: record(6, 'tool.result', '', {}, { tool_results: [{ tool_use_id: 'chronological-call', content: 'done' }] }),
  });

  history.resolve({ records: [
    record(1, 'user.message', 'older prompt'),
    record(2, 'assistant.message', 'older answer'),
    record(3, 'user.message', 'next prompt'),
    record(4, 'assistant.message', 'next answer'),
  ], busy: false });
  await loading;

  const timeline = document.getElementById('timeline');
  assert.deepEqual([...timeline.children]
    .filter((node) => node.dataset.recordKey)
    .map((node) => node.dataset.recordKey), [
    '["B",1]', '["B",2]', '["B",3]', '["B",4]', '["B",5]', '["B",5]',
  ]);
  assert.equal(document.querySelectorAll('.harness-card-tool[data-call-id="chronological-call"]').length, 1);
  assert.equal(document.querySelector('.harness-card-tool[data-call-id="chronological-call"]'), toolCard);
  assert.match(toolCard.textContent, /preserved live output/);
  dom.window.close();
});

test('snapshot adopts a partial assistant bubble and delayed completion preserves later model messages', async () => {
  const history = deferred();
  const { app, dom } = await makeApp({ command: async (name) => {
    if (name === 'ui.contributions') return { panels: [], record_renderers: {}, record_kinds: [], client_modules: [] };
    assert.equal(name, 'sessions.get');
    return history.promise;
  } });
  const loading = app.reloadTranscript();
  event(app, 'turn.started', 'B', {}, {
    turn_id: 'snapshot-stream-turn', record: record(1, 'user.message', 'active prompt'),
  });
  event(app, 'model.text.delta', 'B', { text: 'partial stream text' }, { turn_id: 'snapshot-stream-turn' });
  event(app, 'model.thinking.delta', 'B', { text: 'ephemeral thinking' }, { turn_id: 'snapshot-stream-turn' });
  history.resolve({ records: [
    record(1, 'user.message', 'active prompt'),
    record(2, 'assistant.message', 'canonical complete answer'),
  ], busy: true });
  await loading;

  let assistants = [...document.querySelectorAll('#timeline .turn-assistant')];
  assert.equal(assistants.length, 1);
  assert.equal(assistants[0].dataset.recordKey, '["B",2]');
  assert.equal(assistants[0].querySelector('.content-body').textContent, 'canonical complete answer');
  assert.match(assistants[0].querySelector('.thinking-container').textContent, /ephemeral thinking/);

  event(app, 'model.completed', 'B', {}, {
    turn_id: 'snapshot-stream-turn', record: record(2, 'assistant.message', 'canonical complete answer'),
  });
  event(app, 'model.text.delta', 'B', { text: 'later model delta' }, { turn_id: 'snapshot-stream-turn' });
  event(app, 'model.completed', 'B', {}, {
    turn_id: 'snapshot-stream-turn', record: record(3, 'assistant.message', 'later canonical model answer'),
  });

  assistants = [...document.querySelectorAll('#timeline .turn-assistant')];
  assert.equal(assistants.length, 2);
  assert.deepEqual(assistants.map((node) => node.dataset.recordKey), ['["B",2]', '["B",3]']);
  assert.deepEqual(assistants.map((node) => node.querySelector('.content-body').textContent), [
    'canonical complete answer', 'later canonical model answer',
  ]);
  assert.equal(assistants.filter((node) => node.dataset.recordKey === '["B",2]').length, 1);
  dom.window.close();
});

test('busy snapshot adopts the assistant closed at the actual tool-call boundary', async () => {
  const history = deferred();
  const { app, dom } = await makeApp({ command: async (name) => {
    if (name === 'ui.contributions') return { panels: [], record_renderers: {}, record_kinds: [], client_modules: [] };
    assert.equal(name, 'sessions.get');
    return history.promise;
  } });
  const turnId = 'actual-tool-turn';
  const callId = 't24-partial-read';
  const user = record(5, 'user.message', 'T24_PARTIAL');
  const assistant = record(6, 'assistant.message', 'T24 partial answer A', {}, {
    tool_calls: [{ id: callId, name: 'view_file', arguments: { file_path: 'fixture.txt' } }],
  });
  const decision = record(7, 'tool.decision', 'Allowed', {
    call_id: callId, plugin: 'tools', action: 'allow', policy: 'policy_gate', risk: 'low', reason: 'OK',
  });
  const result = record(8, 'tool.result', '', {}, { tool_results: [{
    tool_use_id: callId, content: [{ type: 'text', text: '    1 | T24 fixture tool output\n' }], is_error: false,
  }] });

  event(app, 'turn.started', 'B', {}, { turn_id: turnId, record: user });
  event(app, 'model.text.delta', 'B', { text: assistant.content }, { turn_id: turnId });
  const partialBlock = app.currentAssistantBlock;
  // The actual model.completed A callback is held; tool.call closes its live node first.
  event(app, 'tool.call', 'B', {
    call_id: callId, name: 'view_file', arguments: { file_path: 'fixture.txt' }, plugin: 'tools', risk: 'low',
  }, { turn_id: turnId });
  event(app, 'tool.decision', 'B', decision.metadata, { turn_id: turnId, record: decision });
  event(app, 'tool.output.delta', 'B', { call_id: callId, stream: 'stdout', text: '    1 | T24 fixture tool output\n' }, { turn_id: turnId });
  event(app, 'tool.result', 'B', {
    call_id: callId, name: 'view_file', is_error: false, duration_ms: 1, preview: '', metadata: {},
  }, { turn_id: turnId, record: result });

  const loading = app.reloadTranscript();
  history.resolve({ records: [user, assistant, decision, result], busy: true });
  await loading;

  const assistants = [...document.querySelectorAll('#timeline .turn-assistant')];
  const unkeyedPartial = [...document.querySelectorAll('#timeline .turn-assistant:not([data-record-key])')]
    .filter((node) => node.querySelector('.content-body').textContent.includes(assistant.content));
  const card = document.querySelector(`.harness-card-tool[data-call-id="${callId}"]`);
  assert.equal(assistants.length, 1);
  assert.equal(assistants[0], partialBlock);
  assert.equal(assistants[0].dataset.recordKey, '["B",6]');
  assert.equal(assistants[0].querySelector('.content-body').textContent, assistant.content);
  assert.equal(unkeyedPartial.length, 0);
  assert.equal(document.querySelectorAll(`.harness-card-tool[data-call-id="${callId}"]`).length, 1);
  assert.equal(card.dataset.recordKey, '["B",6]');
  assert.match(card.querySelector('.tool-output pre').textContent, /T24 fixture tool output/);
  assert.equal(app.isBusy, true);
  dom.window.close();
});

test('a delayed older tool completion cannot finalize a newer same-turn stream', async () => {
  const history = deferred();
  const { app, dom } = await makeApp({ command: async (name) => {
    if (name === 'ui.contributions') return { panels: [], record_renderers: {}, record_kinds: [], client_modules: [] };
    assert.equal(name, 'sessions.get');
    return history.promise;
  } });
  const turnId = 'actual-tool-turn';
  const callId = 'delayed-tool-call';
  const user = record(1, 'user.message', 'Inspect then continue');
  const assistantA = record(2, 'assistant.message', 'Older answer A', {}, {
    tool_calls: [{ id: callId, name: 'inspect', arguments: {} }],
  });
  event(app, 'turn.started', 'B', {}, { turn_id: turnId, record: user });
  event(app, 'model.text.delta', 'B', { text: assistantA.content }, { turn_id: turnId });
  const olderBlock = app.currentAssistantBlock;
  event(app, 'tool.call', 'B', { call_id: callId, name: 'inspect', arguments: {} }, { turn_id: turnId });
  event(app, 'model.text.delta', 'B', { text: 'Newer partial B' }, { turn_id: turnId });
  const newerBlock = app.currentAssistantBlock;
  assert.notEqual(newerBlock, olderBlock);

  const assistantB = record(3, 'assistant.message', 'Newer partial B plus later text');
  const loading = app.reloadTranscript();
  history.resolve({ records: [user, assistantA], busy: true });
  await loading;
  event(app, 'model.completed', 'B', {}, { turn_id: turnId, record: assistantA });
  assert.equal(app.currentAssistantBlock, newerBlock);
  event(app, 'model.text.delta', 'B', { text: ' plus later text' }, { turn_id: turnId });
  event(app, 'model.completed', 'B', {}, { turn_id: turnId, record: assistantB });

  const assistants = [...document.querySelectorAll('#timeline .turn-assistant')];
  assert.deepEqual(assistants.map((node) => node.dataset.recordKey), ['["B",2]', '["B",3]']);
  assert.deepEqual(assistants.map((node) => node.querySelector('.content-body').textContent), [
    'Older answer A', 'Newer partial B plus later text',
  ]);
  assert.equal(assistants[0], olderBlock);
  assert.equal(assistants[1], newerBlock);
  dom.window.close();
});

test('a new model request cannot bind a tool-only assistant to the previous orphan', async () => {
  const history = deferred();
  const { app, dom } = await makeApp({ command: async (name) => {
    if (name === 'ui.contributions') return { panels: [], record_renderers: {}, record_kinds: [], client_modules: [] };
    assert.equal(name, 'sessions.get');
    return history.promise;
  } });
  const turnId = 'tool-only-followup-turn';
  const callA1 = 't24-partial-read';
  const callA2 = 't24-parallel-read';
  const callB = 't24-followup-read';
  const user = record(1, 'user.message', 'T24_PARTIAL');
  const assistantA = record(2, 'assistant.message', 'T24 partial answer A', { stop_reason: 'tool_use' }, {
    tool_calls: [
      { id: callA1, name: 'view_file', arguments: { file_path: 'fixture.txt' } },
      { id: callA2, name: 'view_file', arguments: { file_path: 'other.txt' } },
    ],
  });
  const decisionA1 = record(3, 'tool.decision', 'Allowed A1', {
    call_id: callA1, plugin: 'tools', action: 'allow', policy: 'policy_gate', risk: 'low', reason: 'OK',
  });
  const decisionA2 = record(4, 'tool.decision', 'Allowed A2', {
    call_id: callA2, plugin: 'tools', action: 'allow', policy: 'policy_gate', risk: 'low', reason: 'OK',
  });
  const resultsA = record(5, 'tool.result', '', {}, { tool_results: [
    { tool_use_id: callA1, content: [{ type: 'text', text: 'A1 durable result' }], is_error: false },
    { tool_use_id: callA2, content: [{ type: 'text', text: 'A2 durable result' }], is_error: false },
  ] });
  const assistantB = record(6, 'assistant.message', '', { stop_reason: 'tool_use' }, {
    tool_calls: [{ id: callB, name: 'view_file', arguments: { file_path: 'next.txt' } }],
  });
  const decisionB = record(7, 'tool.decision', 'Allowed B', {
    call_id: callB, plugin: 'tools', action: 'allow', policy: 'policy_gate', risk: 'low', reason: 'OK',
  });
  const resultB = record(8, 'tool.result', '', {}, { tool_results: [{
    tool_use_id: callB, content: [{ type: 'text', text: 'B durable result' }], is_error: false,
  }] });
  const requestEvent = () => event(app, 'model.started', 'B', {
    provider: 'fixture-provider', model: 'fixture-model', request_tokens_estimate: 1,
  }, { turn_id: turnId });
  const callEvent = (callId, filePath) => event(app, 'tool.call', 'B', {
    call_id: callId, name: 'view_file', arguments: { file_path: filePath }, plugin: 'tools', risk: 'low',
  });
  const resultEvent = (callId, name, preview) => event(app, 'tool.result', 'B', {
    call_id: callId, name, is_error: false, duration_ms: 1, preview, metadata: {},
  });

  event(app, 'turn.started', 'B', {}, { turn_id: turnId, record: user });
  requestEvent();
  event(app, 'model.text.delta', 'B', { text: assistantA.content }, { turn_id: turnId });
  const orphanA = app.currentAssistantBlock;
  // model.completed A is held by the client; both calls came from that same request.
  callEvent(callA1, 'fixture.txt');
  callEvent(callA2, 'other.txt');
  event(app, 'tool.decision', 'B', decisionA1.metadata, { record: decisionA1 });
  event(app, 'tool.decision', 'B', decisionA2.metadata, { record: decisionA2 });
  event(app, 'tool.output.delta', 'B', { call_id: callA1, stream: 'stdout', text: 'A1 live output' }, { turn_id: turnId });
  event(app, 'tool.output.delta', 'B', { call_id: callA2, stream: 'stdout', text: 'A2 live output' }, { turn_id: turnId });
  resultEvent(callA1, 'view_file', 'A1 result');
  resultEvent(callA2, 'view_file', 'A2 result');

  // This is the actual next request boundary, not a made-up persisted request ID.
  requestEvent();
  event(app, 'model.completed', 'B', { stop_reason: 'tool_use', usage: {} }, { turn_id: turnId, record: assistantB });
  callEvent(callB, 'next.txt'); // B returns a tool call without any text/thinking delta.
  event(app, 'tool.decision', 'B', decisionB.metadata, { record: decisionB });
  resultEvent(callB, 'view_file', 'B result');
  event(app, 'model.completed', 'B', { stop_reason: 'tool_use', usage: {} }, { turn_id: turnId, record: assistantA });

  const loading = app.reloadTranscript();
  history.resolve({ records: [user, assistantA, decisionA1, decisionA2, resultsA, assistantB, decisionB, resultB], busy: true });
  await loading;

  const assistants = [...document.querySelectorAll('#timeline .turn-assistant')];
  assert.equal(assistants.length, 1);
  assert.deepEqual(assistants.map((node) => node.dataset.recordKey), ['["B",2]']);
  assert.deepEqual(assistants.map((node) => node.querySelector('.content-body').textContent), [assistantA.content]);
  assert.equal(assistants[0], orphanA, JSON.stringify(assistants.map((node) => ({
    key: node.dataset.recordKey, content: node.querySelector('.content-body').textContent,
  }))));
  const cards = [...document.querySelectorAll('.harness-card-tool')];
  assert.deepEqual(cards.map((card) => card.dataset.callId), [callA1, callA2, callB]);
  assert.deepEqual(cards.map((card) => card.dataset.recordKey), ['["B",2]', '["B",2]', '["B",6]']);
  assert.ok(assistants[0].compareDocumentPosition(cards[0]) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
  assert.ok(cards[0].compareDocumentPosition(cards[1]) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
  assert.ok(cards[1].compareDocumentPosition(cards[2]) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
  assert.match(cards[0].textContent, /A1 live output/);
  assert.match(cards[1].textContent, /A2 live output/);
  assert.match(cards[2].textContent, /B result/);
  assert.equal(app.isBusy, true);
  dom.window.close();
});

test('canonical adoption preserves sanitized Markdown on a closed A node without finalizing B', async () => {
  const { app, dom } = await makeApp();
  const turnId = 'markdown-orphan-turn';
  const callId = 'markdown-tool-call';
  const parsed = [];
  const sanitized = [];
  // Controlled adapters prove the shipped safe-rendering pipeline is used; they do not emulate library semantics.
  dom.window.marked = { parse: (text) => {
    parsed.push(text);
    return text === '**Markdown A**' ? '<p><strong>Markdown A</strong></p>' : `<p>${text}</p>`;
  } };
  dom.window.DOMPurify = { sanitize: (html) => { sanitized.push(html); return html; } };

  event(app, 'turn.started', 'B', {}, { turn_id: turnId, record: record(1, 'user.message', 'render safely') });
  event(app, 'model.started', 'B', {
    provider: 'fixture-provider', model: 'fixture-model', request_tokens_estimate: 1,
  }, { turn_id: turnId });
  event(app, 'model.text.delta', 'B', { text: '**Markdown A**' }, { turn_id: turnId });
  const blockA = app.currentAssistantBlock;
  event(app, 'tool.call', 'B', {
    call_id: callId, name: 'inspect', arguments: {}, plugin: 'tools', risk: 'low',
  });
  event(app, 'tool.result', 'B', {
    call_id: callId, name: 'inspect', is_error: false, duration_ms: 1, preview: 'A tool result', metadata: {},
  });

  event(app, 'model.started', 'B', {
    provider: 'fixture-provider', model: 'fixture-model', request_tokens_estimate: 1,
  }, { turn_id: turnId });
  event(app, 'model.text.delta', 'B', { text: 'B active partial' }, { turn_id: turnId });
  const blockB = app.currentAssistantBlock;
  event(app, 'model.completed', 'B', { stop_reason: 'tool_use', usage: {} }, {
    turn_id: turnId, record: record(2, 'assistant.message', '**Markdown A**', { stop_reason: 'tool_use' }, {
      tool_calls: [{ id: callId, name: 'inspect', arguments: {} }],
    }),
  });

  assert.equal(app.currentAssistantBlock, blockB);
  assert.equal(blockA.querySelector('.content-body').innerHTML, '<p><strong>Markdown A</strong></p>');
  assert.deepEqual(parsed, ['**Markdown A**']);
  assert.deepEqual(sanitized, ['<p><strong>Markdown A</strong></p>']);
  event(app, 'model.text.delta', 'B', { text: ' plus later' }, { turn_id: turnId });
  event(app, 'model.completed', 'B', { stop_reason: 'end_turn', usage: {} }, {
    turn_id: turnId, record: record(3, 'assistant.message', 'B active partial plus later'),
  });
  assert.equal(blockA.querySelector('.content-body').innerHTML, '<p><strong>Markdown A</strong></p>');
  assert.equal(blockB.querySelector('.content-body').innerHTML, '<p>B active partial plus later</p>');
  assert.deepEqual(parsed, ['**Markdown A**', 'B active partial plus later']);
  assert.deepEqual(sanitized, parsed.map((text, index) => index === 0
    ? '<p><strong>Markdown A</strong></p>' : '<p>B active partial plus later</p>'));
  dom.window.close();

  const plain = await makeApp();
  assert.equal(plain.dom.window.marked, undefined);
  assert.equal(plain.dom.window.DOMPurify, undefined);
  event(plain.app, 'turn.started', 'B', {}, { turn_id: turnId, record: record(1, 'user.message', 'plain fallback') });
  event(plain.app, 'model.started', 'B', {
    provider: 'fixture-provider', model: 'fixture-model', request_tokens_estimate: 1,
  }, { turn_id: turnId });
  event(plain.app, 'model.text.delta', 'B', { text: '<script>plain fallback</script>' }, { turn_id: turnId });
  const plainBlock = plain.app.currentAssistantBlock;
  event(plain.app, 'tool.call', 'B', { call_id: 'plain-fallback-call', name: 'inspect', arguments: {} });
  event(plain.app, 'model.completed', 'B', { stop_reason: 'tool_use', usage: {} }, {
    turn_id: turnId, record: record(2, 'assistant.message', '<script>plain fallback</script>', {}, {
      tool_calls: [{ id: 'plain-fallback-call', name: 'inspect', arguments: {} }],
    }),
  });
  assert.equal(plainBlock.querySelector('.content-body').textContent, '<script>plain fallback</script>');
  assert.equal(plainBlock.querySelector('.content-body').querySelector('script'), null);
  plain.dom.window.close();
});

test('a stale busy snapshot keeps an older answer separate from the current turn stream', async () => {
  const history = deferred();
  const { app, dom } = await makeApp({ command: async (name) => {
    if (name === 'ui.contributions') return { panels: [], record_renderers: {}, record_kinds: [], client_modules: [] };
    assert.equal(name, 'sessions.get');
    return history.promise;
  } });
  const loading = app.reloadTranscript();
  const startedUser = record(3, 'user.message', 'current turn prompt');
  event(app, 'turn.started', 'B', {}, { record: startedUser });
  event(app, 'model.text.delta', 'B', { text: 'current stream before snapshot' });
  const currentStream = app.currentAssistantBlock;

  history.resolve({ records: [
    record(1, 'user.message', 'historical prompt'),
    record(2, 'assistant.message', 'historical answer'),
  ], busy: true });
  await loading;

  let assistants = [...document.querySelectorAll('#timeline .turn-assistant')];
  assert.equal(assistants.length, 2);
  assert.deepEqual(assistants.map((node) => node.dataset.recordKey), ['["B",2]', undefined]);
  assert.deepEqual(assistants.map((node) => node.querySelector('.content-body').textContent), [
    'historical answer', 'current stream before snapshot',
  ]);
  assert.equal(assistants[1], currentStream);

  event(app, 'model.text.delta', 'B', { text: ' and after snapshot' });
  const currentAnswer = record(4, 'assistant.message', 'current stream before snapshot and after snapshot');
  event(app, 'model.completed', 'B', {}, { record: currentAnswer });

  assistants = [...document.querySelectorAll('#timeline .turn-assistant')];
  assert.equal(assistants.length, 2);
  assert.deepEqual(assistants.map((node) => node.dataset.recordKey), ['["B",2]', '["B",4]']);
  assert.deepEqual(assistants.map((node) => node.querySelector('.content-body').textContent), [
    'historical answer', 'current stream before snapshot and after snapshot',
  ]);
  dom.window.close();
});

test('tool cards stay in assistant call-list order before and after a fresh transcript projection', async () => {
  const calls = [
    { id: 'first-call', name: 'inspect_first', arguments: { path: 'first' } },
    { id: 'second-call', name: 'inspect_second', arguments: { path: 'second' } },
  ];
  const records = [
    record(1, 'user.message', 'inspect both'),
    record(2, 'assistant.message', 'Starting inspections', {}, { tool_calls: calls }),
    record(3, 'system.notice', 'Interleaved notice'),
    record(4, 'tool.decision', 'Second inspection allowed', {
      call_id: 'second-call', plugin: 'tools', action: 'allow', policy: 'policy_gate', risk: 'low', reason: 'OK',
    }),
    record(5, 'tool.decision', 'First inspection allowed', {
      call_id: 'first-call', plugin: 'tools', action: 'allow', policy: 'policy_gate', risk: 'low', reason: 'OK',
    }),
    record(6, 'tool.result', '', {}, { tool_results: [
      { tool_use_id: 'first-call', content: [{ type: 'text', text: 'first result' }], is_error: false },
      { tool_use_id: 'second-call', content: [{ type: 'text', text: 'second result' }], is_error: false },
    ] }),
  ];
  const { app, dom } = await makeApp();
  event(app, 'turn.started', 'B', {}, { record: records[0] });
  event(app, 'model.completed', 'B', {}, { record: records[1] });
  event(app, 'system.notice', 'B', { message: 'Interleaved notice' }, { record: records[2] });
  for (const call of calls) {
    event(app, 'tool.call', 'B', {
      call_id: call.id, name: call.name, arguments: call.arguments, plugin: 'tools', risk: 'low',
    });
  }
  for (const [recordIndex, call] of [[3, calls[1]], [4, calls[0]]]) {
    const decision = records[recordIndex];
    event(app, 'tool.decision', 'B', decision.metadata, { record: decision });
    event(app, 'tool.result', 'B', {
      call_id: call.id, name: call.name, is_error: false, duration_ms: 1, preview: `${call.id} result`, metadata: {},
    });
  }

  const keysBeforeReload = [...document.querySelector('#timeline').children]
    .map((node) => node.dataset.recordKey);
  const callsBeforeReload = [...document.querySelectorAll('#timeline .harness-card-tool')]
    .map((node) => node.dataset.callId);
  const reloaded = await makeApp({ command: async (name) => {
    if (name === 'ui.contributions') return { panels: [], record_renderers: {}, record_kinds: [], client_modules: [] };
    assert.equal(name, 'sessions.get');
    return { records, busy: false };
  } });
  await reloaded.app.reloadTranscript();
  const keysAfterReload = [...reloaded.dom.window.document.querySelector('#timeline').children]
    .map((node) => node.dataset.recordKey);
  const callsAfterReload = [...reloaded.dom.window.document.querySelectorAll('#timeline .harness-card-tool')]
    .map((node) => node.dataset.callId);

  assert.deepEqual(callsBeforeReload, ['first-call', 'second-call']);
  assert.deepEqual(keysBeforeReload, ['["B",1]', '["B",2]', '["B",2]', '["B",2]', '["B",3]']);
  assert.deepEqual(callsAfterReload, callsBeforeReload);
  assert.deepEqual(keysAfterReload, keysBeforeReload);
  dom.window.close();
  reloaded.dom.window.close();
});

test('stored tool-only card keeps readable spacing when live decision metadata arrives', async () => {
  const { app, dom } = await makeApp();
  const callId = 'stored-tool-only-call';
  const assistant = record(1, 'assistant.message', '', {}, {
    tool_calls: [{ id: callId, name: 'write_to_file', arguments: { file_path: 'note.txt' } }],
  });
  app.renderRecord(assistant);

  const card = document.querySelector(`.harness-card-tool[data-call-id="${callId}"]`);
  assert.ok(card);
  event(app, 'tool.call', 'B', {
    call_id: callId, name: 'write_to_file', arguments: { file_path: 'note.txt' },
  });
  const decision = record(2, 'tool.decision', 'Allowed', {
    call_id: callId, plugin: 'fs-tools', risk: 'low', action: 'allow', policy: 'policy_gate', reason: 'approved',
  });
  event(app, 'tool.decision', 'B', decision.metadata, { record: decision });
  // Reconciliation of the same decision must not add another subtitle or separator.
  app.updateToolDecision(decision.metadata);

  const timeline = document.querySelector('#timeline');
  const cards = [...timeline.querySelectorAll('.harness-card-tool[data-call-id]')];
  const nameRow = card.querySelector('.tool-name').parentElement;
  assert.equal(cards.length, 1);
  assert.equal(cards[0], card);
  assert.equal(card.dataset.recordKey, '["B",1]');
  assert.equal(app._toolCallOwners.get(callId).key, '["B",1]');
  assert.deepEqual([...timeline.children].map((node) => node.dataset.recordKey), ['["B",1]']);
  assert.equal(nameRow.textContent.replace(/\s+/g, ' ').trim(), '⚡ write_to_file fs-tools · low');
  assert.equal(nameRow.querySelectorAll('.card-meta').length, 1);
  assert.deepEqual(cards.map((node) => node.dataset.callId), [callId]);
  dom.window.close();
});

test('only reconnect reloads the selected session and a late reconnect snapshot cannot clobber it', async () => {
  const reconnectA = deferred();
  const requests = [];
  const { app, dom } = await makeApp({
    command: async (name, args) => {
      if (name === 'ui.contributions') return { panels: [], record_renderers: {}, record_kinds: [], client_modules: [] };
      assert.equal(name, 'sessions.get');
      requests.push(args.session_id);
      return args.session_id === 'A' ? reconnectA.promise : {
        records: [
          record(1, 'user.message', 'B reconnect history'),
          record(2, 'question.asked', '', {
            interaction_id: 'B-reconnect-question',
            questions: [{ id: 'choice', question: 'Continue?', options: [{ id: 'yes', label: 'Yes' }] }],
          }),
        ],
        busy: true,
      };
    },
    switchSession: async () => ({}),
  });

  app.activeSessionId = null;
  app.events._dispatchInternal('connected', {});
  assert.deepEqual(requests, []);
  assert.equal(document.getElementById('timeline').textContent, '');

  app.activeSessionId = 'A';
  app.events._dispatchInternal('disconnected', {});
  app.events._dispatchInternal('connected', {});
  assert.deepEqual(requests, ['A']);
  const switchToB = app.switchSession('B');
  await switchToB;
  reconnectA.resolve({ records: [record(1, 'user.message', 'stale A reconnect history')], busy: false });
  await Promise.resolve();

  assert.deepEqual(requests, ['A', 'B']);
  assert.equal(app.activeSessionId, 'B');
  assert.match(document.getElementById('timeline').textContent, /B reconnect history/);
  assert.doesNotMatch(document.getElementById('timeline').textContent, /stale A reconnect history/);
  assert.equal(app.isBusy, true);
  assert.equal(app.pendingQuestions.has('B-reconnect-question'), true);
  dom.window.close();
});

test('two canonical assistant messages within one turn retain distinct identities', async () => {
  const { app, dom } = await makeApp();
  event(app, 'turn.started', 'B', {}, { turn_id: 'multi-model-turn', record: record(1, 'user.message', 'Inspect then answer') });
  event(app, 'model.text.delta', 'B', { text: 'Inspecting' }, { turn_id: 'multi-model-turn' });
  event(app, 'model.completed', 'B', {}, {
    turn_id: 'multi-model-turn', record: record(2, 'assistant.message', 'Inspecting'),
  });
  event(app, 'model.text.delta', 'B', { text: 'Final answer' }, { turn_id: 'multi-model-turn' });
  event(app, 'model.completed', 'B', {}, {
    turn_id: 'multi-model-turn', record: record(3, 'assistant.message', 'Final answer'),
  });
  const assistants = [...document.querySelectorAll('#timeline .turn-assistant')];
  assert.equal(assistants.length, 2);
  assert.deepEqual(assistants.map((node) => node.dataset.recordKey), ['["B",2]', '["B",3]']);
  assert.deepEqual(assistants.map((node) => node.querySelector('.content-body').textContent), ['Inspecting', 'Final answer']);
  dom.window.close();
});

test('snapshot approval and question outcomes resolve their durable pending cards', async () => {
  const records = [
    record(1, 'approval.requested', 'Approve command', { approval_id: 'approval-B', approval_kind: 'tool_execution' }),
    record(2, 'approval.resolved', '', { approval_id: 'approval-B', approved: true, by: 'user' }),
    record(3, 'question.asked', '', {
      interaction_id: 'question-B',
      questions: [{ id: 'choice', question: 'Choose', options: [{ id: 'yes', label: 'Yes' }] }],
    }),
    record(4, 'question.answered', '', { interaction_id: 'question-B', status: 'answered', answers: { choice: { option_id: 'yes' } } }),
  ];
  const { app, dom } = await makeApp({ command: async (name) => {
    if (name === 'ui.contributions') return { panels: [{ id: 'artifact' }, { id: 'sessions' }, { id: 'economics' }, { id: 'plugins' }], record_renderers: {}, record_kinds: [], client_modules: [] };
    assert.equal(name, 'sessions.get');
    return { records, busy: true };
  } });
  await app.reloadTranscript();
  const approval = document.querySelector('.harness-card-approval[data-request-id="approval-B"]');
  assert.ok(approval?.classList.contains('approval-resolved'));
  assert.equal(approval.dataset.resolved, 'approved');
  assert.equal(document.getElementById('approval-banner').classList.contains('hidden'), true);
  assert.equal(app.pendingQuestions.size, 0);
  assert.equal(document.querySelector('.harness-card-question[data-interaction-id="question-B"]')?.dataset.status, 'answered');
  assert.equal(app.isBusy, true);
  dom.window.close();
});

test('an accepted snapshot derives busy state but cannot override a newer SSE terminal event', async () => {
  const pendingBusy = deferred();
  const { app, dom } = await makeApp({ command: async (name) => {
    if (name === 'ui.contributions') return { panels: [], record_renderers: {}, record_kinds: [], client_modules: [] };
    return pendingBusy.promise;
  } });
  const loading = app.reloadTranscript();
  pendingBusy.resolve({ records: [], busy: true });
  await loading;
  assert.equal(app.isBusy, true);

  const pendingTerminal = deferred();
  app.api.command = async (name) => name === 'ui.contributions'
    ? { panels: [{ id: 'artifact' }, { id: 'sessions' }, { id: 'economics' }, { id: 'plugins' }], record_renderers: {}, record_kinds: [], client_modules: [] }
    : pendingTerminal.promise;
  const secondLoad = app.reloadTranscript();
  event(app, 'turn.started', 'B', {}, { turn_id: 'turn-B-2' });
  assert.equal(app.isBusy, true);
  event(app, 'turn.completed', 'B', {}, { turn_id: 'turn-B-2' });
  pendingTerminal.resolve({ records: [], busy: true });
  await secondLoad;
  assert.equal(app.isBusy, false);
  dom.window.close();
});

test('late submit, question, and approval errors from A cannot paint or unlock B', async () => {
  const submit = deferred();
  const answer = deferred();
  const approval = deferred();
  const { app, dom } = await makeApp({
    submitTurn: () => submit.promise,
    answerQuestion: () => answer.promise,
    resolveApproval: () => approval.promise,
  });
  app.bindControls();
  app.activeSessionId = 'A';
  const input = document.getElementById('composer-input');
  input.value = 'A prompt';
  document.getElementById('composer-form').dispatchEvent(new dom.window.Event('submit', { cancelable: true }));

  app.showQuestion({ interaction_id: 'question-A', questions: [{ id: 'q', question: 'A?', options: [{ id: 'yes', label: 'Yes' }] }] });
  const questionForm = document.querySelector('[data-question-form]');
  questionForm.querySelector('input[type="radio"]').checked = true;
  const answering = app.submitQuestionForm(questionForm);
  const resolving = app.resolveApproval('approval-A', 'approved');

  app.resetSessionView();
  app.activeSessionId = 'B';
  app.setBusy(true);
  submit.reject(new Error('late submit A'));
  answer.reject(new Error('late question A'));
  approval.reject(new Error('late approval A'));
  await Promise.all([answering, resolving]);
  await Promise.resolve();
  const timeline = document.getElementById('timeline');
  assert.equal(app.isBusy, true);
  assert.doesNotMatch(timeline.textContent, /late (submit|question|approval) A/);
  dom.window.close();
});

test('late sessions-panel rows cannot adopt or paint over newer rows', async () => {
  const older = deferred();
  const newer = deferred();
  let calls = 0;
  const { app, dom } = await makeApp({ getSessions: () => [older, newer][calls++].promise });
  const host = document.getElementById('session-list');
  const ctx = {
    app,
    host,
    api: app.api,
    escape: (value) => String(value),
    select() {},
  };
  const oldRender = sessionsPanel.render(ctx);
  const newRender = sessionsPanel.render(ctx);
  newer.resolve([{ session_id: 'B', title: 'Current B', active: true }]);
  await newRender;
  older.resolve([{ session_id: 'A', title: 'Stale A', active: true }]);
  await oldRender;
  assert.equal(app.activeSessionId, 'B');
  assert.equal(host.querySelectorAll('.session-item').length, 1);
  assert.match(host.textContent, /Current B/);
  assert.doesNotMatch(host.textContent, /Stale A/);
  dom.window.close();
});

test('resolved approval state has a visible marker without changing its historical wording', async () => {
  const { app, dom } = await makeApp();
  app.renderRecord(record(1, 'approval.requested', 'Historical request', { approval_id: 'approval-visual', approval_kind: 'tool_execution' }));
  const card = document.querySelector('.harness-card-approval[data-request-id="approval-visual"]');
  app.resolveApprovalUI({ approval_id: 'approval-visual', approved: true, by: 'user' });
  assert.equal(card.classList.contains('approval-resolved'), true);
  assert.equal(card.dataset.resolved, 'approved');
  assert.match(card.querySelector('.card-header').textContent, /Approval Required/);
  assert.match(card.textContent, /Historical request[\s\S]*Resolved: approved by user/);
  const css = await readFile(new URL('../../harness/frontends/web/static/styles.css', import.meta.url), 'utf8');
  assert.match(css, /\.approval-resolved\[data-resolved="approved"\] \.card-header::after\s*\{[^}]*content:\s*"✓ APPROVED"/);
  dom.window.close();
});
