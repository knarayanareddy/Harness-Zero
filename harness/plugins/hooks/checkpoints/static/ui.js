import diffsPanel from '/static/panels/diffs.js';
import renderWorkspaceChanges from '/static/renderers/workspace_changes.js';
import renderCheckpointRestored from '/static/renderers/checkpoint_restored.js';

export const panels = { diffs: diffsPanel };
export const renderers = {
  'workspace.changes': renderWorkspaceChanges,
  'checkpoint.restored': renderCheckpointRestored,
};
