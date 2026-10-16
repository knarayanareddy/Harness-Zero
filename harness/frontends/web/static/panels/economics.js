/**
 * The Spend pane (formerly `renderEconomicsTab` in `app.js`).
 *
 * EconomicsSnapshot (harness/sdk/types.py): {turn_usd, session_usd, lifetime_usd, cap_usd, unknown_pricing,
 * usage: Usage.to_dict()} — the same shape `cost.updated` carries, and the keys `test_static_contract.py`
 * pins against the dataclass so this cannot drift again.
 */
export default {
  host: 'economics-view',
  async render(ctx) {
    const container = ctx.host;
    if (!container) return;
    try {
      const data = await ctx.api.economics(ctx.app.activeSessionId);
      const usage = data.usage || {};
      const capUsd = data.cap_usd != null ? `$${Number(data.cap_usd).toFixed(2)}` : '—';
      const inputTok = usage.input_tokens || 0;
      const outputTok = usage.output_tokens || 0;
      const totalTok = inputTok + outputTok;
      const sessionUsd = `$${Number(data.session_usd || 0).toFixed(4)}`;
      const totalSpend = `$${Number(data.lifetime_usd || 0).toFixed(4)}`;
      const unknown = data.unknown_pricing
        ? '<div class="economics-stat-label mb-3">Some calls used a model without known pricing — spend is a lower bound.</div>'
        : '';

      container.innerHTML = `
        <div class="economics-card">
          <h3 class="economics-title">Spend &amp; Token Economics</h3>
          <div class="economics-grid">
            <div class="economics-stat">
              <div class="economics-stat-label">Session Spend / Turn Cap</div>
              <div class="economics-stat-value" data-accent="${data.unknown_pricing ? 'false' : 'true'}" data-warn="${data.unknown_pricing ? 'true' : 'false'}">${ctx.escape(sessionUsd)} / ${ctx.escape(capUsd)}</div>
            </div>
            <div class="economics-stat">
              <div class="economics-stat-label">Workspace Lifetime Cost</div>
              <div class="economics-stat-value">${ctx.escape(totalSpend)}</div>
            </div>
          </div>
          ${unknown}
          <div class="economics-stat-label mb-2">Session Token Consumption</div>
          <div class="economics-rows">
            <div class="economics-row"><span>Input Tokens</span><span class="economics-row-value">${ctx.escape(String(inputTok))}</span></div>
            <div class="economics-row"><span>Output Tokens</span><span class="economics-row-value">${ctx.escape(String(outputTok))}</span></div>
            <div class="economics-row" data-total="true"><span>Total Tokens</span><span class="economics-row-value">${ctx.escape(String(totalTok))}</span></div>
          </div>
        </div>
      `;
    } catch (err) {
      container.innerHTML = `<div class="economics-card text-red-400">Failed to load economics: ${ctx.escape(err.message)}</div>`;
    }
  },
};
