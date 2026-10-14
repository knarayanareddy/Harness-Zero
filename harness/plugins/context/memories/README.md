# Memories Context Plugin (PLG-14)

Provides dynamic session prompt context from persistent memories:
- Queries `<workspace>/.harness/memory.db` for remembered facts.
- Emits a `SESSION` stability context block under block id `"memories"`.
- Surfaces user preferences, key architectural patterns, and saved facts across agent sessions.
