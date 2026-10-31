# 01 — GrantSnap: reimbursement packets from receipts and program evidence

**Status:** Draft specification; no implementation exists.
**Inspired quality pattern:** Visual intake followed by deterministic rules and arithmetic.
**Target:** Small nonprofits and community programs preparing reimbursement claims.

## Problem and hypothesis

Program coordinators repeatedly turn receipts, attendance summaries, and grant rules into reimbursement packets. The hypothesis is that the work is error-prone because evidence is split across photos, spreadsheets, and rule documents—not because organizations need another open-ended chatbot. Validate the workflow with at least three program administrators before making impact claims. Do not invent savings or reimbursement rates.

## Product promise

Turn an approved batch of expense evidence into a traceable draft packet: every amount reconciles, every eligibility decision cites a versioned rule, and unresolved items are held for a human. GrantSnap never submits a claim or initiates payment in v1.

## Actors and workflow

- **Coordinator:** creates a grant period, uploads receipts and a program export, reviews extracted fields and exceptions, approves an export.
- **Reviewer:** verifies policy version and exceptional decisions; may reject or return an item.
- **GrantSnap agent:** extracts candidate facts, asks one targeted clarification, explains deterministic rule results, and drafts a packet summary.

Flow: create a period → select a locally configured grant/rulebook version → upload evidence → extract vendor/date/currency/line items → reconcile arithmetic → classify each item as `eligible`, `ineligible`, or `needs_review` → resolve questions → preview packet and evidence → approve exact packet snapshot → export CSV/PDF → read the exported totals back and verify them.

## Scope and non-goals

**MVP:** one nonprofit, one grant program, one currency, receipt image/PDF upload, CSV attendance/expense import, deterministic category caps and date windows, duplicate detection, line-level provenance, exception queue, approved export, append-only audit events, synthetic demo data.

**Not in v1:** tax/accounting advice, grant discovery, bank access, reimbursement submission, payment initiation, generated policy interpretation, automatic exclusion of ambiguous expenses, multiple currencies, live OCR provider requirement, or claiming that “eligible” means the funder will reimburse it.

## Domain model and policy

Persist `GrantPeriod(id, rulebook_id, start, end, budget_cents)`, `EvidenceFile(id, sha256, media_type, uploaded_by, retention_state)`, `Expense(id, vendor, date, currency, total_cents, line_items, source_refs, extraction_status)`, `Rulebook(id, version, source_uri, reviewed_by, effective_at, rules)`, `Classification(expense_id, verdict, rule_ids, explanation, engine_version)`, and `Packet(id, version, content_hash, approval, export_hash)`.

Use integer minor units for all money. Only deterministic code calculates sums, caps, duplicates, date eligibility, and remaining budget. Rules must have an owner, source, effective date, and review status. Unreviewed rules are disabled. OCR/model output is a proposal; any missing/uncertain amount or unsupported category routes to review. A single ineligible or uncertain line cannot silently disappear from a packet.

## Agent and authority design

The Strands agent may call read-only tools for period metadata, parsed evidence, rule explanations, and clarification drafts. It cannot edit a rulebook, change a verdict, approve an export, or call an external grant portal. A deterministic service applies coordinator edits and creates exports only after approval. Approval digest covers all line items, evidence hashes, rulebook version, totals, and recipient/export destination; changes invalidate approval.

## Acceptance criteria (each is required)

- **GS-01 Setup:** `make setup && make verify` succeeds from a clean clone offline; absence of credentials is not a skip of core tests.
- **GS-02 Active agent path:** `make demo-e2e` proves a Strands agent call uses the same tool/policy/state path with the scripted model as the optional real model path.
- **GS-03 Arithmetic:** property/unit tests prove totals, caps, remaining budgets, and rounding are correct in integer cents; float arithmetic is not used for money.
- **GS-04 Provenance:** every extracted amount/date/vendor and every decision shown in a packet links to an evidence file/span and rule version; missing source means `needs_review`.
- **GS-05 No silent drops:** a packet includes every uploaded expense with a final state or an explicit exclusion reason; unresolved lines block export.
- **GS-06 Duplicate control:** identical evidence hash plus matching normalized vendor/date/amount is flagged; it is never auto-deleted. Distinct positive-control receipts remain separate.
- **GS-07 Stale approval:** mutate any line, attachment, total, rule version, or destination after approval; export is refused until reapproval. Test exact payload binding, including concurrent double approval.
- **GS-08 Injection:** prompt-like text inside receipt OCR cannot alter rules, approve, or send. Assert zero state writes from the injected text.
- **GS-09 Uncertainty:** unreadable totals, conflicting currency, partial pages, or model timeout result in a visible hold and preserved evidence—not guessed values.
- **GS-10 Eval:** `make eval` reports exact-field extraction, auto-classification precision/coverage, abstention, and false inclusion separately on versioned synthetic receipts. A false inclusion threshold is zero on the safety corpus; every miss stays visible.
- **GS-11 Audit:** create/edit/classify/approve/export events are append-only and tamper-evident; verify integrity after a restart and reject update/delete attempts.
- **GS-12 UI:** coordinator can complete the seeded happy path and resolve one exception by keyboard at mobile and desktop widths; automated accessibility checks plus a manual screen-reader/zoom check are documented.

## Evaluation and demo

Build at least 60 synthetic receipts: clear eligible, clear ineligible, split receipt, duplicate, unreadable, mismatched totals, date boundary, OCR injection, and same-vendor legitimate repeat. The eval labels are independent of the implementation. Show a clean expense, an uncertain line held, a tampered-after-approval packet refused, then a verified export. State explicitly that the sandbox is not a funder’s portal and synthetic results are not field impact.

## Delivery slices

1. Money/rule kernel and fixtures → property tests.
2. Upload/hash/parse and evidence UI → extraction and uncertainty tests.
3. Strands read-only agent path → tool/schema/injection tests.
4. Reviewer queue, exact approval, export and read-back → stale/duplicate/idempotency tests.
5. Accessibility, eval report, sandbox deployment, runbook and demo.

## Open questions

- Which funder/program is first, and who verifies the rulebook version and source evidence?
- What retention period and reviewer roles does the partner approve?
- Do target coordinators confirm the evidence-to-packet workflow in discovery interviews?

Until these are answered, this is an offline prototype, not accounting guidance.
