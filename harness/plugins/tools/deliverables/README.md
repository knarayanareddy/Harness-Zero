# Deliverables

The `present` read-only tool marks an existing workspace file or `.harness/artifacts/` artifact as the user's deliverable. It resolves symlinks before enforcing the workspace boundary, refuses Git and Harness internals, fingerprints the file with SHA-256, and appends a durable `deliverable.presented` record before returning a success notice.

A filename can be given directly for a file in the workspace root. If it is not there, a matching file directly under `.harness/artifacts/` is accepted. The Studio opens the recorded file in its Artifacts viewer; the viewer uses an isolated, script-disabled response policy.
