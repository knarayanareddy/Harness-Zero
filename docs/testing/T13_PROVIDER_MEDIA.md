# T13 — provider media contracts

Status: locally integrated; live-provider/model and global qualification remain
incomplete. No URLs are fetched locally and no image files are opened implicitly.

Adapters use actual ImageBlock `media_type`, `data_b64`, `url` fields, retain
base64 exactly once, and reject unsupported/ambiguous sources as non-retryable
SDK bad_request errors. Supported modes are deliberately endpoint-specific:

| Adapter endpoint | Inline declared MIME types | HTTPS image URL | Image tool result |
|---|---|---|---|
| Anthropic Messages | PNG, JPEG, GIF, WebP | URL source | Nested image content supported |
| Gemini GenerateContent | PNG, JPEG, WebP, HEIC, HEIF | fileData.fileUri for BMP/PNG/JPEG/WebP; Gemini 2.0 rejected | This adapter rejects it explicitly |
| Ollama OpenAI-compatible chat | PNG, JPEG | Remote URL rejected; inline data-URI string used | This adapter rejects it explicitly |

These are adapter serialization modes, not a claim that every model supports
vision or that every vendor size/dimension rule is enforced locally. Base64 syntax
and declared MIME are checked, not full image decoding. Tests use synthetic PNG
and JPEG; the parent independently checked all PNG chunk CRCs. No models were
downloaded and no vendor inference/account entitlement was exercised. Files API
IDs/uploads, other media and unsupported tool-result modes remain unimplemented.

Primary references, reread by parent 2026-10-22:
- https://platform.claude.com/docs/en/build-with-claude/vision
- https://ai.google.dev/gemini-api/docs/generate-content/file-input-methods
- https://ai.google.dev/gemini-api/docs/image-understanding
- https://docs.ollama.com/api/openai-compatibility
- https://docs.ollama.com/capabilities/vision

## Evidence under ignored `.venv/evidence/`

Luna's baseline-HEAD worktree recorded 24 initial media failures and two URL
failures; initial focused green runs were 52 on each Python version. Review found
malformed URL authorities escaping as ValueError. Four actual fail-first controls
then passed; worker final media/provider subsets were 56 per version. Original
artifacts remain under `luna-t13/`.

Parent composed only image-path changes with its existing T14/T15 stream/error
repairs. It preserved the one-argument private Anthropic builder call after the
original R06 exposed a required-argument regression (`t13-builder-compat-red.xml`).
A second review found empty Gemini model selection could validate a URL against
one model but send to the 2.0 fallback. Shared effective-model normalization now
drives validation and endpoint choice without mutating the request. Empty/prefixed
model controls failed first in `t13-effective-model-red.xml`.

Final combined provider/media/XML/local-peer/real-runner/ledger subset:
**105 passed, Python 3.12, 26.36s** (`t13-t16-final-py312.xml`);
**105 passed, Python 3.11, 9.45s** (`t13-t16-final-py311.xml`).
Original R06 and R10 pass (`t13-t16-original.xml`); R06 itself is a serialization
smoke, not proof its synthetic payload is an actual image or vendor-accepted.
Luna source review verified the composed candidate digest
`c2459cadee8cb50df676ad15ea7a472aff3fa3e582732ff942af5e5a69c3dade`,
closed both findings and found no further actionable issue. The parent checked
the scoped patch hash; the reviewer did not run tests or hash that patch.
See `t13-parent-candidate-v2.json` and `t13-acceptance-receipt.json`.

HTTPX captures assert exact outbound bodies, text/image order, no double encoding,
valid PNG/JPEG fixtures, supported URL modes, malformed authorities and rejected
inputs with no outbound request. This is not a fresh full-suite/artifact pass.
