/**
 * The Artifacts pane (WS-05).
 *
 * Its content is *pushed*, not polled: an `artifact` card arrives while the turn runs and `showArtifact`
 * prepends it, and after a reload the same painter runs from the `ui.card` records in the log. So `render`
 * has nothing to fetch — the declaration still names `sessions.get`, because that is the command whose records
 * this pane projects, and a panel with no data source behind it is exactly what the contract test rejects.
 */
export default {
  // The shell's existing host, kept as the id the stylesheet and `showArtifact` already use.
  host: 'studio-content-area',
  async render() {
    return;
  },
};
