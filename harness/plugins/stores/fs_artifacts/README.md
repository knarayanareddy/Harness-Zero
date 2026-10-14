# Filesystem Artifact Store (`stores.fs_artifacts`)

Provides persistent artifact storage in `<workspace>/.harness/artifacts` with strict path traversal validation and metadata tracking in `<workspace>/.harness/artifacts_manifest.json`.

Implements the `ArtifactStore` protocol (`write`, `read`, `list`).
