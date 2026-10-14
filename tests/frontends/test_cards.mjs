import test from 'node:test';
import assert from 'node:assert';
import { CardRenderer, cardRenderer } from '../../harness/frontends/web/static/cards.js';

test('CardRenderer renders every built-in kind and unknown fallback without throwing', () => {
  const cards = [
    { kind: 'diff', payload: { file: 'test.py', diff: '--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new' } },
    { kind: 'table', payload: { title: 'Results', headers: ['Col 1', 'Col 2'], rows: [['A', 'B']] } },
    { kind: 'plan', payload: { title: 'Refactor Plan', steps: [{ text: 'Step 1', status: 'done' }, 'Step 2'] } },
    { kind: 'form', payload: { title: 'Feedback', form_id: 'f1', fields: [{ name: 'reason', type: 'text' }] } },
    { kind: 'artifact', payload: { filename: 'report.html', type: 'text/html' } },
    { kind: 'notice', payload: { level: 'warning', message: 'Rate limit approaching' } },
    { kind: 'approval', payload: { request_id: 'req_1', action: 'rm -rf /', risk_class: 'DESTRUCTIVE' } },
    { kind: 'completely_unknown_card_kind_xyz', payload: { foo: 'bar', answer: 42 } },
    null,
    undefined,
    {},
  ];

  for (const c of cards) {
    const rendered = cardRenderer.render(c);
    assert.strictEqual(typeof rendered, 'string');
    assert.ok(rendered.length > 0);
  }
});
