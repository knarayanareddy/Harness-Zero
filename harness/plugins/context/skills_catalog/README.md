# Skills Catalog Context Plugin (PLG-15)

Progressive skills disclosure context source:
- Discovers skills from `builtin/skills` and `<workspace>/.harness/skills`.
- Injects a compact summary of available skills into the model's system context.
- Instructs the agent to invoke `view_skill` to load the full instructions for any skill on demand.
