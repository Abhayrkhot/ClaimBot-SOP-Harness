<!--
  Data-contract guide for the synthetic JSON fixtures. JSON stays comment-free
  so standard parsers and schema validators can consume it without extensions.
-->
# Fixture data model

These files provide a small, synthetic insurance dataset for deterministic
workflow tests. They contain no real customer information.

`policyholders.json` is the identity and account boundary. Each record has a
stable `party_id`, five supported verification fields, and policy metadata. The
repository normalizes submitted fields and requires three matching PII values
for one party. A policy number may help identify the account but does not count
as one of the three required PII values.

`claims.json` contains claim records linked to policyholders by `party_id`.
`case_id` identifies a claim, while type, status, filing date, payment fields,
deadlines, missing documents, and denial text support grounded responses. The
repository filters by `party_id` before applying any claim hint, preserving the
ownership boundary.

`claim_schema.json` describes supported claim types, statuses, required fields,
and fields that may legitimately be absent. It makes unavailable-data behavior
explicit instead of encouraging the response layer to infer missing facts.

`required_document_guideline.json` maps claim types and document names to
submission and replacement guidance. The repository turns these entries into
approved statements only when the customer asks a related question.

Production adapters can replace these files with authenticated services as long
as they preserve the same separation: identity lookup, ownership-filtered claim
lookup, and an allowlist of grounded response facts.
