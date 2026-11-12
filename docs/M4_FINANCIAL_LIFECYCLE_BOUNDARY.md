# M4 constrained financial integration: observation and denial only

This increment connects validation to HOPE's actual migrated PAPER ledger, not
the proxy's isolated `session_prototype.effects` table. It adds no operating
authority and is not called by the production scheduler or startup entry point.
Engineering integration and operational PAPER remain NO-GO; HIGH A1 is open.

## Implemented interface

`hope.security.financial_lifecycle.dispatch_one_cycle` always raises
`INDEPENDENT_FINANCIAL_ADMISSION_UNAVAILABLE`. It takes no engine, SQL runner,
factory, callback or configurable attestor. It does not inspect a worker object,
import a strategy, connect to a DB or treat a copied Permit/history/signature as
authority. This is a deliberately closed integration seam, not a prototype
approval wired into real financial execution.

`inspect_committed_cycle` uses fixed SQL and existing canonical read-only
repositories in one bounded READ ONLY / REPEATABLE READ transaction. It requires
an actual restricted observer LOGIN with no reachable privileged/DDL roles and
no financial DML rights. Public tables are pinned and checked for ordinary-table,
non-RLS SELECT-only access; the search path explicitly places pg_temp last.
The observer has no grant-installation path. Timeouts and a pre-read effect-count
bound prevent an unbounded history fetch. SQL errors propagate, never becoming
an affirmative report.

An immutable reference specifies the expected database digest, authorization,
job UUID, financial xid8, writer principal and portfolio. These are comparison
inputs, NOT independent identity attestations. The observer checks actual
cluster/database identity, execution/financial-transaction/COMMIT-witness/
completion bindings, successful canonical job identity, certified outcome,
strategy authorization, exact effect topology and the canonical immutable
reconciliation receipt. FILLED additionally joins actual signal, risk, order,
fill, portfolio application and portfolio PNL identities. NO_TRADE/RISK_BLOCKED
require their real no-trade decision/input linkage and remain valid outcomes.

The report always has execution_authorized=false, production_verification=false,
replay_permitted=false and independent_session_attestation=BLOCKED. Its local
SHA256 detects accidental changes only; it is neither a signature nor an
independent trust root. No report consumer dispatches anything. A consistent
committed history is not evidence that a new transaction is authorized.

## Actual proofs and limits

Disposable tests use the real certified scheduler/startup lifecycle for India
and USA FILLED, NO_TRADE and RISK_BLOCKED. Existing fixture approvals explicitly
MODEL an external authority via a separate owner connection; they are not
independent production admission. Migration238 funding protections and all
001–238 migration bytes are unchanged. Separate restricted observer LOGINs
cannot fund portfolios, rewrite initial capital or insert approvals. Borrowed
authorization/job/transaction/principal/database/portfolio references deny.

A real crash after financial COMMIT but before startup completion leaves five
effects and no completion; the observer refuses a complete-history result and
does not retry. Explicit privileged damage to disposable restored audit/effect
rows demonstrates refusal of incomplete history. This does not claim protection
against a malicious database administrator or counterfeit server.

The native kernel prototype additionally exercises genuine post-baseline fork
creation and signatures over foreign cluster/xid/issuer/generation transcripts.
These are PROTOTYPE proofs. Its signer still uses ephemeral fixture roots;
CAP_SYS_PTRACE remains broad and the observer/broker is trusted. Neither a
separate UID nor a local file hash makes a signer independent of that privilege.
Loaded code/memory, pre-baseline execution provenance, production image roots,
key custody/rotation/revocation and independent workflow control remain BLOCKED.
All twenty production acceptance requirements remain FAIL/unexecuted.

## Missing capability before any financial writer integration

A separately managed root must authenticate a constrained executor owning the
real upstream DB transaction, and a signer outside the observer's privilege
domain must independently validate that session/transaction and policy. The
reviewed protocol must bind cluster/TLS identity, login/backend incarnation,
xid8, kernel lease, immutable release/code/image, exact financial request,
issuer/key/scope, generation, expiry and single-use consumption. A worker-
supplied Permit cannot substitute for this root. No production root is invented.

The future writer must use the existing certified scheduler and migration238
funding boundary, with a reviewed connection-ownership bridge and a complete
financial write-set witness held through real COMMIT. This observer verifies a
declared committed lifecycle projection, not all possible database writes or
arbitrary trigger/function integrity against an owner adversary. It does not
close that write-set witness requirement. HALT/revocation serialization, unknown
COMMIT reconciliation and replay exclusion must hold in that integrated writer;
historical consistency and prototype locks are not substitutes. Independent
human review and genuine external deployment evidence are still required.
