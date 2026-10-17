# Certified nested backtest integrity

A matching SHA-256 identifies stored content; it does not validate that content.
The v3 certified verifier checked execution/research provenance and the nested
backtest schema label, but accepted a nested object with no result at all or only
`result: {metrics: {}}`. Both cases were reproduced through the independent
verified-source resolver with freshly matching content hashes.

Certified evidence now requires a complete canonical projection of the existing
`BacktestResult` domain model. The verifier parses nested result material with
Pydantic's existing dataclass/model support, projects it again using the existing
v1 projector, and requires equality of the entire envelope. This reuses the
published schema without another parser, backtesting engine or field hierarchy.
Missing/extra fields, invalid nested models, non-finite decimals and noncanonical
wire representations fail closed. The stored evidence is never rewritten.

This is a structure/canonicalization check. It does not independently recompute
performance, prove that a strategy has an edge, approve friction assumptions,
invent a scientific threshold or replace the existing evaluation/reviewer
decision process. The schemas and valid producer bytes remain unchanged. Generic
research results retain their existing contract; data claiming v3 certification
must meet the stronger certified contract.

The certified PAPER binding uses the verified-source resolver before its claim
and implementation factories. Real PostgreSQL negative tests persist counterfeit
nested fragments with matching fingerprints, full research/comparison lineage and
explicit synthetic reviewer verdicts under all existing database guards. Admission
then rejects them before strategy factories, job claims, authorization receipts,
orders, fills or accounting. Genuine engine outputs and existing execution and
recovery paths remain regression controls. No test data is production evidence.

Operational service errors map this validation failure to the existing fixed
`PAPER_CERTIFIED_INPUT_UNAVAILABLE` code; payloads and internal exception text are
not exposed. Existing malformed evidence remains immutable and must not authorize
new work. Use the existing reviewed research process for corrected evidence;
there is no automatic reclassification, historical mutation, data migration or
promotion. Already committed PAPER history remains governed by its separate
durable accounting/reconciliation contract.

No AWS deployment, role/network change, credentials, risk/strategy behavior or
LIVE activation is part of this engineering batch. AWS operational acceptance
and real feed/candidate/configuration prerequisites remain pending.
