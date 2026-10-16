/**
 * A plugin's rich card. `metadata.card` holds exactly the object the live `card` event carried, so the card
 * renderer — and the plugin that contributed it — needs no special case here.
 */
export default function renderUiCard(app, rec) {
  const meta = rec.metadata || {};
  if (meta.card) app.appendCard(meta.card);
}
