/**
 * The fixture plugin's client module, served at `/static/plugins/probe-vault/probe.js` by the web front end.
 *
 * It replaces the generic table that `[ui].panels` alone would get, which is the difference WS-05 promises:
 * a feature ships its own rendering, and the kernel serves the file. `render` is called by `PanelRegistry`, so
 * the text below is what a scenario asserts — it can only appear if the URL `ui.contributions` advertised was
 * imported by the page.
 */
export const panels = {
  'probe-vault': {
    async render(ctx) {
      const rows = (await ctx.api.command('plugins.list', {})) || [];
      const vault = rows.filter((row) => String(row.name).startsWith('probe'));
      ctx.host.innerHTML = `
        <div class="panel-item">Vault rows from the plugin module: ${ctx.escape(String(vault.length))}</div>
        <ul class="panel-tree">${vault
          .map((row) => `<li class="panel-node">${ctx.escape(row.name)} — ${ctx.escape(row.summary || '')}</li>`)
          .join('')}</ul>`;
    },
  },
};

export const renderers = {};
