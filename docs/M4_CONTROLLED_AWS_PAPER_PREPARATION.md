# Controlled AWS PAPER deployment preparation

This is one review procedure, not permission to execute it. No AWS/host access,
installation, production migration, service activation, operational PAPER cycle
or merge is performed by this batch. All commands below are future operator
instructions. Keep LIVE disabled; preserve NO_TRADE/HALTED and the frozen
7e9d341742818a97accce3e2301b1c7f96596795 baseline.

## Current decision

**Engineering candidate: validate the exact combined PR and independent review.
GO only for offline preparation/disposable rehearsal. NO-GO for AWS installation,
production migration/activation and the first operational PAPER cycle until all
genuine independent evidence and explicit approvals exist.**
Repository CI does not establish host operating readiness. The startup circularity is addressed only by the explicit dual-authority route
below. It never substitutes handoff intent, ACKNOWLEDGED, STAGED or HTTP200 for
running proof. Ordinary execution retains its unchanged CURRENT requirement.

The user's historical AWS observations are EC2 i-019d427e5a9b6fa87 in ap-south-1,
t3.medium / approximately 4 GiB RAM, 30 GiB EBS / approximately 27 GiB free,
PostgreSQL16.15 database hope with migrations001-008, release
f17a54929a8ecb7745a62c118893b55494aeb2f1, two dashboards returning200 on8787/8788,
no installed PAPER service/timers, and a restore-tested backup plus snapshot
snap-0b2281aaa44f88f5b. These are not current measurements or host authorization.
No claim is made that current AWS is compatible with this branch.

## Procedure in dependency order

1. Independently review the dedicated integrated candidate combining PR83 at
   cb82eb12e95a9c1b17c08c917dab3919e0b79783 and PR84 at
   3fec053a3a9358894d1699f72baaa7d023c6c76a. Both original PRs remain untouched.
   Choose the integration approach explicitly; do not blindly merge all three.
   Merge only after explicit approval. Select the accepted integrated release SHA
   from its final exact source/PR
   PostgreSQL16/17 CI. Reconcile source, build, image, dependency manifests and
   independent signatures. This candidate explicitly combines both branches and
   fixes additional transport-alias risks. Never deploy an unreviewed feature SHA
   or frozen baseline. The exact candidate SHA/CI URLs are recorded in the
   integration PR; this document does not pretend to contain its own commit SHA.
2. Obtain separate read-only host-access approval. Re-observe release/pointers,
   both environment identities, process/namespace owners, permissions, services,
   dashboards, timeouts, actual database major/history and activation journals.
   No stale journal/lease may be deleted to create readiness. Obtain separate
   approval for any eventual mutation. Historical backup/snapshot IDs alone
   cannot certify a current restore point.
3. Rehearse restoration of a newly approved complete database backup into a
   disposable PostgreSQL16 environment matching the host; also run17 CI. Keep
   credentials and the production database URL out of the rehearsal. Validate
   backup provenance, tables/data counts, logical identities and restore time.
   The CI008 fixture explicitly recreates the historical runner ledger (including
   DEFAULT now()); it is representative, not a copy of the actual host's data.
4. Read the exact migration inventory below. The chain has235 migrations;
   an exact verified008 prefix has227 pending (009 through235). Verify every
   applied checksum against approved source, prefix order and unknown records.
   NULL checksums, missing checksum columns, gaps or altered history require
   independent investigation. This runner does not backfill unverifiable history.
   Migration008's historical backfill remains unchanged and is not a new bypass.
5. In the disposable restored database, review all227 pending SQL files, data
   constraints, new functions/triggers, required privileges, locks and disk/RAM
   headroom. Apply using apply_migrations inside an explicit READ COMMITTED
   transaction. The runner supports PostgreSQL16/17, takes the schema migration
   advisory lock, binds metadata to the validated current or explicitly selected
   schema (never borrowing public history for private dashboard schemas),
   clamps unbounded/excessive statement and lock waits to30s/5s,
   checks the complete source/history before DDL, uses a savepoint and verifies
   all checksums/source after DDL. A competing runner fails rather than races.
   Do not weaken a failed constraint or edit a historical migration to proceed.
6. Validate the upgraded schema against a fresh install of the same approved
   chain on the same PostgreSQL major/extension versions. In bounded read-only
   REPEATABLE READ transactions, schema_observation hashes public relations,
   columns/defaults, constraints/validation, indexes/readiness, trigger definitions
   and enabled state, functions, sequence definitions and views. Rows are
   canonically ordered independently of server collation; duplicates are retained.
   It excludes data,
   sequence current values and OIDs. It does not certify role grants, all database
   settings, external binary integrity or data correctness. No function body or
   private data is returned. An observed digest alone remains UNVERIFIED; an
   independently reviewed expected digest is needed for a schema match. Verify
   financial constraints and HALT/reconciliation with the full disposable suites.
7. Inspect upgrade_plan(connection, approved_release/'migrations') in a bounded
   read-only transaction. A valid pending prefix is BLOCKED with an exact pending
   list. Schema-record VERIFIED is only a record comparison, not deployment GO.
   Database errors are fixed-code diagnostics. No plan function applies DDL.
8. Prepare a release-pinned service drop-in offline with:

   python -m hope.api.paper_release_plan --release <accepted-sha> --receipt <independently-approved-receipt-sha256> --factory <reviewed.module:factory> --migrations-dir <accepted-source>/migrations

   This prints JSON, exact migration names/checksums and inert drop-in text. It
   neither connects nor installs and always returns deployment_go/first_cycle_go
   false. Source identity remains explicitly UNVERIFIED until independently
   attested. Extract/review the drop-in as10-release.conf, not the whole JSON.
   WorkingDirectory and both ExecStart commands use the immutable release path
   and release-specific runtime through isolated OS-Python attestation. The base
   unit retains user/group/resource/security/watchdog/TERM settings. Its receipt
   and controller arguments are mandatory; an unset receipt fails closed. Review
   both EnvironmentFile inputs, privately, for exact SHA, LIVE=false, least-privilege
   credentials, owner/mode and absence of loader injections. Never print secrets.
9. Only after separate production-migration approval and a validated restore
   point: arrange approved maintenance/quiescence, ensure no concurrent workers or
   schema runner, apply the exact rehearsed chain in one transaction and perform
   the same read-only postconditions. Do not let service startup run migrations.
   The database may reject old data; that is NO-GO, not permission to rewrite it.
10. Only after explicit installation approval: install the reviewed base unit and
    drop-in under /etc/systemd/system/hope-paper.service.d/10-release.conf with
    root ownership and0644 mode; review using systemd-analyze verify and systemctl
    cat. A daemon reload, enable/start, timer installation or restart still needs
    explicit activation approval. None is done or implied by this document.
11. Verify both dashboards from genuine persisted evidence: exact deployed SHA,
    schema compatibility, market-feed freshness, PAPER process state, controller
    HALT, last attempted/completed cycle, NO_TRADE reasons and reconciliation/
    recovery. Existing snapshots and /runtime.json,/controller.json,/staging.json
    remain distinct; missing/stale snapshots are blocked/stale. HTTP200 is not
    readiness. New single-cycle refusal codes remain bounded diagnostics and
    cannot produce CURRENT or READY. No health/history may be seeded to bootstrap.
12. Controlled first-cycle startup requires THREE linked artifacts: the existing
    one-use handoff intent, independent bootstrap-only authority and a distinct
    one-certified-cycle execution authority. No issuer/key generator is provided.
    Approvers provision separately reviewed startup-ed25519.pub and
    first-cycle-execution-ed25519.pub, distinct from each other and every existing
    signing role. Current signed policy/revocation/generation checks apply.
    Content-addressed JSON plus detached signatures and request refs live under
    startup-authorizations/ and execution-authorizations/. Documents last at most
    60seconds and bind the immutable handoff digest, exact controller incarnation,
    logical cycle/database identity and PAPER=true/LIVE=false. Startup max_cycles
    is0 and additionally binds the reviewed public schema-catalog digest and the
    actual controller namespace digest. Execution max_cycles is1 and also binds
    startup digest plus exact deterministic job UUID/key/UTC schedule. Expiry may
    not extend the parent authority. Existing handoff binds release/runtime/image/
    receipt/factory/provider/data/calendar through established independent proof.
13. Launch only the existing attested release entry in the independently approved
    image namespace as its non-root identity. The controller must share the real
    stage mount/PID/user/network namespaces and root inode; its independently
    attested release runtime manifest must equal the image-bound stage runtime.
    The stage PID remains non-executing and distinct. A host worker cannot borrow
    another container's image proof. A reviewed read-only container observer must
    be available; do NOT give the worker unrestricted Docker/Podman daemon access
    or mount an unrestricted engine socket to make this check pass. Missing
    observer/namespace evidence stays BLOCKED. Verify the launch/observer design
    in disposable acceptance before requesting any host action.
14. The future operator invocation, inside that approved namespace, is the normal
    paper_release.py entry with --runtime-mode release, --factory, --migrations-dir,
    --status-file, --deployment-receipt, --controller-directory, --once,
    --handoff-request, --expected-cycle-key, --startup-request and (only for actual
    execution) --first-cycle-execution-request. --authority-wait-seconds is an
    explicit0-60second non-trading wait for independently supplied documents.
    Missing/forged/revoked documents never import the factory or dispatch work.
    Startup-only mode can verify schema/credentials and record BOOTSTRAPPED, then
    exits without creating normal worker health or execution. It cannot trade.
15. A separately reviewed first-cycle service must be one-shot with Restart=no,
    WatchdogSec=0 and no inherited NOTIFY_SOCKET/WATCHDOG_PID/WATCHDOG_USEC. It
    must launch in the exact approved namespace, not merely reuse the ordinary
    host unit. Do not enable/start it or convert --stage-only into execution.
    Source bootstrap, least-privilege DB login, schema235, restore proof, signed
    authorities and provider/data/calendar acceptance must all be current.
    Namespace/launcher/observer facts and production installation/activation each
    need separate explicit approval. The normal long-running unit remains
    CURRENT-gated and is not implicitly promoted by a first-cycle success.
16. Startup atomically consumes the intent and bootstrap authority, then performs
    read-only exact migration/catalog checks and records BOOTSTRAPPED. Execution
    consumes the distinct execution authority before factory import/dispatch.
    Public schema resolution is mandatory; private/temp shadows are rejected.
    The existing certified scheduler enforces the exact one-run plan and retains
    HALT, eligibility, risk/sizing, sessions/PIT/freshness and reconciliation.
    Authority is revalidated inside the financial transaction and a fresh immutable
    commit witness is required by a deferred database constraint at actual COMMIT.
    An expired/missing witness rolls back the entire financial attempt. Successful
    NO_TRADE and RISK_BLOCKED are valid; no bearish or other implementation is
    enabled without its own certified persisted evidence. CYCLE_COMPLETED requires
    the real SUCCEEDED job, certified outcome, commit witness and reconciliation
    audit. It cannot be inferred from ACKNOWLEDGED or a missing record.
17. /startup.json reports only fresh, owner-bound startup phase evidence and always
    execution_authorized=false. Missing/dead/stale publishers and contradictory
    fields never imply readiness. A successful one-cycle worker publishes genuine
    cycle evidence then STOPPED; it is not an autonomous READY worker. New database
    audit states are ABSENT, BOOTSTRAPPED, RECOVERY_REQUIRED and CYCLE_COMPLETED.
    A pending execution without completion blocks a new startup in that database;
    do not clear it, retry it or invent a completion. Obtain independent review of
    durable financial proof and explicit recovery authority first.

## Crash, rollback and recovery

Before a migration transaction commits, a failure rolls DDL/history back to its
savepoint even if the caller catches the error. A connection loss before known
commit is uncertain: reconnect read-only and compare exact records/catalog/data
before retry. Never blindly rerun after an unknown commit outcome. A successful
idempotent replay is only allowed after proving the exact complete prefix.

After a committed upgrade, this chain has no supported down migrations. Recovery
is **restore-only** unless a separately reviewed forward repair exists. Restore
the approved pre-upgrade database/volume as a coherent unit, then revalidate the
matching old release/runtime/schema/receipts before any activation. Switching
only the code pointer does not roll back235 database migrations. Do not truncate
audit tables, clear HALT, rewrite checksums or disable triggers as recovery.
Existing atomic release/journal recovery must prove either completed activation
or verified restoration; interrupted or corrupt records stay blocked.

Handoff consumption commits before dispatch. Failure/timeout/crash after that
commit leaves RECOVERY_REQUIRED and cannot replay the same authorization,
request or logical cycle key. A financial commit followed by a missing audit
acknowledgment must be reconciled from genuine job/effect/outcome records; it is
not permission for another execution. ACKNOWLEDGED only records the handoff
boundary returned successfully; it never proves a trading cycle ran. The caller
still owns existing certified authentication, idempotency and reconciliation.

## Remaining genuine prerequisites

Current authorized host observations; approved final merged release and fresh
exact-head CI; independent build/package/image/receipt/runtime approvals; real
provider/factory/dataset/PIT/session/calendar acceptance and credentials; approved
schema/data/role rehearsal on a restored host snapshot; current backup/restore
proof; explicit migration/install/activation approvals; and separately reviewed namespace/observer/launch configuration plus genuine
startup/execution authorities. Independent human security review of the combined
candidate remains required before merge or deployment. Missing evidence stays BLOCKED.

## Exact migration sequence and source checksums

Normalized UTF-8/universal-newline checksums match the runner and existing records.
This is235 steps; a verified008 prefix has227 pending. Actual fresh/008 upgrade
rehearsals establish SQL dependency compatibility for the fixture, not unseen host
data. These hashes are source inventory, never independent deployment approval.

| Order | Migration | SHA256 |
|---|---|---|
| 001 | 001_initial.sql | `275ea702c90877ba5ba4c7f01973e59c7728e7bd5cb78bf5ab50b23962f2e0f4` |
| 002 | 002_integrity_constraints.sql | `2d8110be17b80573a3da26cc091137b829960546abb731121189a1cc904eb7b5` |
| 003 | 003_experiment_invalidation_events.sql | `8edcf0b8955f91f44dbf75d02f1f36f0c7d417b8de70ae16c9fbf676f87b8996` |
| 004 | 004_identity_universe_integrity.sql | `fb4378dc1f5fae61da0ae40f6c18da7cdf0174c430403dd6f9ff3808aaacaebd` |
| 005 | 005_trading_integrity_guards.sql | `2baf8e20f134a8a4ff0246dd66adcb111e5e5d8bb8986fc919e77c3a4e2e2819` |
| 006 | 006_cross_entity_trading_integrity.sql | `bb671c9d599e45206dce074858c725dd1f17239adefeebcfe32f75280f4679d1` |
| 007 | 007_identity_namespace_integrity.sql | `8688dae0f7e71e16daf62aa5a30065c0c0374ad5d5afb1d7d53f86beba937e02` |
| 008 | 008_migration_checksums.sql | `fca6fce0f3f74c9d2f780c01b28c52f9efaa44cf19aea01acd7ee0ae1fe56e56` |
| 009 | 009_job_run_identity.sql | `8b3ca4a7a2953f01677b79b1031297d05e97d1c0be0cf4710d53014591259d73` |
| 010 | 010_job_run_lifecycle.sql | `602a827629f618715d20a63deca2bf17afb3da670808e3eef52c0f2f75677d68` |
| 011 | 011_paper_effect_idempotency.sql | `3cb1c397b277b920d66ef19e866cae759265842047335b9fb5f8c206a88d8593` |
| 012 | 012_paper_portfolio_state.sql | `863e5a21e7b3fcc9851fdc382f1e5cb9a5c147b2f0ef6291ccdeb396ab978ae8` |
| 013 | 013_paper_portfolio_pnl_events.sql | `80f6e75babf823119ce7ec6a9699a00a4d34bd9ef559f8e27fe76a012e3c1e73` |
| 014 | 014_market_bars.sql | `7790ea224ff2e75fbb28178a728f3f2bf518c3cee4296183a030c45814f64e90` |
| 015 | 015_market_data_immutability.sql | `d11e93bb1c2b9f71fb143941e0036a36ee4c4d28a578d7d115bd37bc04f1e9e7` |
| 016 | 016_dataset_version_identity_immutability.sql | `be56adc195b2850014f1702ce93877816d2a7d7493aa1353d5b3138a139bb806` |
| 017 | 017_dataset_metadata_immutability.sql | `c1b981226294b0f5e20b52780994e26a2c36489df439f3e237e2daf062b62572` |
| 018 | 018_experiment_dataset_freeze.sql | `ac81d3e36ff0fea2fe3f189df04eaeb8266dc0ce237a48ed637ab73f174f7ebe` |
| 019 | 019_configuration_snapshot_immutability.sql | `c049ddad53d62d1d9686e9c3f91eb650e0962025bb999f9ab81f18b417c3da98` |
| 020 | 020_strategy_version_immutability.sql | `c3d337dde45f8e6fcee908dacbd21fd0f7b741a0e6701cbf91fc48dc6c0e5e0d` |
| 021 | 021_experiment_universe_freeze.sql | `5c8572b418aee322eedb5da1cb4e2eda8bddbf9d6f919a13ca53d9fae2be978e` |
| 022 | 022_experiment_provenance_immutability.sql | `549ee4d9e0b428eb83079d8aacacc0b91dcf07c50d8f66a48873abce795ad7c4` |
| 023 | 023_strategy_metadata_immutability.sql | `614ef2f594e744583eb1abbd46d0b5049eb2b9ed330c9433548b0d321e4059aa` |
| 024 | 024_experiment_universe_cardinality.sql | `4a91667a39de13973d1b47b223af5ade7a457f2ba7f766474ba3a1aca1fc85d9` |
| 025 | 025_experiment_invalidation_immutability.sql | `11a3fcc7f6ad4a79f30eb070ac2e5cde9b82977221b0133061eacc486e45a526` |
| 026 | 026_universe_metadata_immutability.sql | `9cb452b21d9bfb7a12211306aefb385f16f8d33be50e6e2c91b504b3365a243f` |
| 027 | 027_audit_event_immutability.sql | `468624c70db76eae865d33c7f14481893cfc3a6bf1b913ef4cb958e1e3f0f3bd` |
| 028 | 028_signal_immutability.sql | `b45d113a65b94b0f0696c7e1008f5a775864f23591a0507db11bf85ec2516416` |
| 029 | 029_fill_immutability.sql | `b1535163348806a8fbc2dd7806a0ca349426f2f267e361281ba81e7d35fdb1ef` |
| 030 | 030_order_immutability.sql | `b64d3905ba26b22f04e204958f03a834f4fb86cffc24d49de388e13a89cd46b9` |
| 031 | 031_paper_effect_immutability.sql | `49440f775e65f217aa898f1d3dac4414e0a2a4daf8a818d24ac60c2ae6f107fa` |
| 032 | 032_paper_portfolio_fill_application_immutability.sql | `eb09bf2ce665bda3109490e5d87940992021ed6df936d3c166efc5c18d4bd934` |
| 033 | 033_paper_portfolio_pnl_event_immutability.sql | `8f8200501dc1bad021fc17ebd84321fbb44cc7b268b3b63eff752ab46cb4315f` |
| 034 | 034_job_run_terminal_immutability.sql | `77123e956141aac87adaca3d54df9d633c9075b089f4f75ff1a12a63485d433c` |
| 035 | 035_job_run_identity_immutability.sql | `43cc73e3b9fd23ad41de10629d545f7777c16e6d1a7b036075238ddbb0d114cc` |
| 036 | 036_paper_portfolio_pnl_application_integrity.sql | `75207333df5a2f76b10eaa70b6ba4d82ed3c6654a36cdbf3faeedeeeebb2c842` |
| 037 | 037_paper_portfolio_pnl_instrument_integrity.sql | `4daf44e3b72e36c63fdbf519770d0a6d0160a51c702564e7ad8ea4a650a0d6d7` |
| 038 | 038_paper_portfolio_pnl_time_integrity.sql | `ea31007b8c056f1bb5d64c097e5727f1d569a1a30b0c7e124ca9e3d4e379d83e` |
| 039 | 039_paper_portfolio_pnl_commission_integrity.sql | `527556b4df940dc448e4726ab91db37d5bdcef32879cf1ab73ffaa68ed987795` |
| 040 | 040_fill_cost_nonnegative.sql | `fb60d95677df14de0fd1b53d00b1d177a46adf99428af4903e081c10e961b81b` |
| 041 | 041_fill_cumulative_quantity_integrity.sql | `f4af70521c3fc107b4fbf84ec9f1536e47687fb8165820a836bca8d0e4b1fea6` |
| 042 | 042_fill_cost_model_provenance.sql | `3c2970aeff84676c5fb067c1eac736f05c7459ef0237215fe5e31f269899c15e` |
| 043 | 043_paper_fill_cost_model_required.sql | `9c946f7b39d4e6a378d8c76d5ef5e1fd18a80d5f557bfe8fd29edffa14424eac` |
| 044 | 044_paper_portfolio_fill_environment_integrity.sql | `2e44de25b26b2087c5cb8638029b7e32de92bf4232adf40deacbf653f1638bab` |
| 045 | 045_paper_portfolio_application_sequence_integrity.sql | `043b3dd3fdfeb3cd57e958eb69fa92e289c4b6bc2b065cc51a59dfc62300aec1` |
| 046 | 046_paper_portfolio_application_time_integrity.sql | `2fe97edb8bcc091bb990ede7418f2d767cbd021786ed50a8e0dbda826d444076` |
| 047 | 047_paper_fill_decision_time_integrity.sql | `beec19c727371534ca66f5975e1e31bb35eb535753b413e8e76d17f76a9cff02` |
| 048 | 048_paper_portfolio_initial_cash_integrity.sql | `717ae3b655cb27954cea4fafb53c3cc01e40f03fc5dce2133c87e1edb96ec07c` |
| 049 | 049_paper_portfolio_cash_integrity.sql | `7465d71fe261e1649f5fe9907fd4660e5dbc28640446f6db6c0c522c7f7f75c6` |
| 050 | 050_paper_portfolio_position_numeric_integrity.sql | `3125db13e2fbc6a9a13ff338ddb5eccb02a93386ebeb9806560c9585fb334de5` |
| 051 | 051_paper_portfolio_initial_cash_immutability.sql | `fced12f4ed548b5b17af5938d2eb7b17d0f0c3aa47c7a5c40e8df7ee54aa7074` |
| 052 | 052_order_quantity_finite.sql | `4b18b16408fe6a8f998c93bc27b02994b8f39c3cae615c918dffd1352c78775e` |
| 053 | 053_fill_numeric_finite.sql | `d9b72e27eb988cdd54b0aa7b740a25b2f7d2a231e3bd3a441709e047cd8b21a5` |
| 054 | 054_paper_portfolio_pnl_numeric_integrity.sql | `875dafe97bcc471f372d63b296802daaecb83b5d1f1f6ba8719d711f47a963b9` |
| 055 | 055_market_bar_numeric_finite.sql | `a075596f1ec4f32337bc193c97b1978dda40e183b654f96e8a3624fbf0ccdcf3` |
| 056 | 056_experiment_definition_integrity.sql | `ab1ba360de8aa6ccd09d86828a1bca488ad96c61d6b8dd49ec3e6d4f7857e663` |
| 057 | 057_universe_member_valid_interval.sql | `e45264984686a1a6b3b1b612c3e18a161202630f3fbd2820aa44738f06aaa09d` |
| 058 | 058_paper_risk_assessments.sql | `b972c986452108cf10442c7b3e586bf84035bb318369e19909ab6ad9b80e19e5` |
| 059 | 059_paper_risk_assessment_immutability.sql | `cb0045f17bbe85d96bebe3a06a9bd91c3519f73bed8bf87789c1c7ee52917cad` |
| 060 | 060_paper_risk_effect_lineage.sql | `9e48ab76168fd719157ff89f1450a1b9d5dc7ae7b9987631777a77125c42093b` |
| 061 | 061_paper_risk_effect_type.sql | `4a671b67789c517ea51793de43205fc9cff9de34ea4a2a08fb1a70bc17ac111d` |
| 062 | 062_paper_risk_reason_code_canonical.sql | `483b3ecdba1d3d7117b486f402dc164e5a147aacdefe7d97cd42ef6e11c52dd4` |
| 063 | 063_paper_risk_decision_time_integrity.sql | `c5899542a854fd3c88f4b1aa9e6356e69327d1d46bdb890a9402f9afac72504e` |
| 064 | 064_paper_fill_cost_model_canonical.sql | `f40a12621173f992cf5bbc181da797f8d9b60282aa56dbdf44ace45f31694b35` |
| 065 | 065_market_data_coverage_manifest.sql | `17aa304cf475ca43496bc5f04d5573029b64a6899065763f7d055f50733ca9a9` |
| 066 | 066_market_data_manifest_universe.sql | `3968abc719c29a3e4858f925d98add2db629ed1417257062940e2e777f56d846` |
| 067 | 067_market_bar_seal_lock.sql | `22fe79969914c758b74b4974c9fd69c592907106336045997d451d37d023ea4b` |
| 068 | 068_market_data_finalization_integrity.sql | `aafdb244e8b8771d7dbf01068f68587eb4c23e904a98b7d808d30ee4e3ead9a6` |
| 069 | 069_market_data_finalization_membership.sql | `b8764933ee5cc5d4978dc20ee9e578451ec0c8c0a3ffe00caf06b0b90e544ef8` |
| 070 | 070_market_data_manifest_unique_coverage.sql | `5fb53d42acf952696389c999b37775b979bf7093d255ec6180181234ad89a865` |
| 071 | 071_market_data_manifest_identity_continuity.sql | `db2aa68b74d88bdb227a423d6b7f29259772d6793187d6d3ccb5ab35307c5d74` |
| 072 | 072_market_data_manifest_identity_canonical.sql | `50eb7ac0b2733335d81be0c75d725a3dde94a06857b5fadb868f3ef302de5e15` |
| 073 | 073_market_data_manifest_window_contract.sql | `0525e2a4515dddbe29ff124dbaa2f5f1195c35cbc548bdb92ba3df56c1cad3fb` |
| 074 | 074_market_data_manifest_structure.sql | `094fcf419b6b1afc665125b643a30e3a18c2ae70d737215598b247df6d075484` |
| 075 | 075_experiment_market_data_provenance.sql | `cff7c799be8aeb23296db42881c8222947854eacab650ecfc33e2a324cda4f5d` |
| 076 | 076_research_run_provenance.sql | `f9dd303d2d8649f318cc45ab0170756d6de2525bba7b4ea00e3d3c8ec0e0bd27` |
| 077 | 077_research_run_evidence.sql | `2a6c83e065eeb8a9935a6abbee032f6209c12f69f11a639b3526682d4122a8c1` |
| 078 | 078_research_run_evidence_identity.sql | `2f1a3d8535f800c5639954a985a0b709d2724b5dd4f20e726fa28d795a3478df` |
| 079 | 079_research_run_market_data_provenance.sql | `cc08471e5e55664403f64bb74a6db8035931bd92ed4649998e06045c2738b8c8` |
| 080 | 080_research_run_evidence_market_data_identity.sql | `d2c0618ef086f39dd23866136b37c15cdcc1dd8a8acd5be2dfca070bfc6a2c22` |
| 081 | 081_experiment_variant_predeclaration.sql | `d30e7172285bc68836ef4c689c5751fd970156e4f9cda4e2001a73f182f75e1a` |
| 082 | 082_research_decisions.sql | `2aa18d8ade014d654c6a4164d938ddcdd47264db8baf8ca682a6d2f9275774bd` |
| 083 | 083_research_comparisons.sql | `4ed0f30b09d632b571f157cd4d5a601d07d0bfcc42f2abdc08fec542c2f7d98a` |
| 084 | 084_research_evaluation_plans.sql | `4b7823b61dce0b852592da2aaf76110e269bb7abf9ab1dd4e883853499f1a770` |
| 085 | 085_research_evaluation_results.sql | `10ef9d05a897015583eda23277259235b612788efe3247d2fd9fe251c94b7ceb` |
| 086 | 086_canonical_research_comparison.sql | `896f78d99d18f7a7b4a67ce724e5c1f249d824ebd81f53f8d051ea3a7ae7e276` |
| 087 | 087_research_decision_comparison_binding.sql | `bb40ee6e0b7654b5b4c0cb2f78baeaf56d30342ef1df561fe07a51dbbaaa5700` |
| 088 | 088_strategy_candidate_registry.sql | `49b6f545352b12f3e6dcd15268df74f38e66617b6b951ed6fcb499d7fc135cd7` |
| 089 | 089_strategy_candidate_current_state.sql | `e23b22aa037cd79493d6d51bfe400bf7bebaf36f699390d50acd85e8e5a8c0a9` |
| 090 | 090_strategy_family_catalog.sql | `c219dca397affe2d272b5418a98fa991fc9ba4982ba921158bd1dbd9044e102c` |
| 091 | 091_market_intelligence_events.sql | `524f5a7dbfd3c4aa5da8a9f37edf5ac60b43fa4981ede0d8e53b258a725b32b2` |
| 092 | 092_market_intelligence_assessments.sql | `2750848db0698ba0ea58436de49e1cbe72ef9921bc45bf96326afc2f0f66896c` |
| 093 | 093_market_intelligence_review_resolutions.sql | `89d49450ea1af1af1ebce223bdc45630f0f3cb289e09fe94ea768daebdba319e` |
| 094 | 094_current_market_intelligence_entry_blocks.sql | `b9b186999e178f8d5aec2b5a7d0201a48f9fa61fb673e467e04f2cd50813e3de` |
| 095 | 095_research_invariant_runs.sql | `3dee269c7852184288741289c56fe866183f29773a3442559c75801f5e605460` |
| 096 | 096_research_stage_evidence.sql | `10565264e3d7f37803d1ab0273b6160a1d015b026faadb344275fa97de8d8832` |
| 097 | 097_market_intelligence_assessment_disposition.sql | `c315a2a7d1580a8b0c1fb1084f578940351f94e86a673921c1a1f7c8ce5fb1dc` |
| 098 | 098_identity_mapping_contract.sql | `03de3ec43e56ded8727fb2622194c4b94c69b83a47e9b5377279408aaef0d901` |
| 099 | 099_universe_version_contract.sql | `aa21aebc54109736bfbe8c25d29693f431bdf8cdd7f7445880ef45e0464d577d` |
| 100 | 100_experiment_universe_membership_immutability.sql | `e10227e46257747112236c74eadbc9320a690b8c41bb0bef97ad4c0f604663cd` |
| 101 | 101_experiment_universe_version_immutability.sql | `44b4707695079db5ee0769952accd0fd73beca7d63bb412d643510c698dbec48` |
| 102 | 102_restore_draft_universe_version_updates.sql | `0a6dea49b0a40f1e18ffba0c2d35d8c2c9052e370993222623b604d6f18f304e` |
| 103 | 103_restore_unsealed_universe_version_updates.sql | `f3903727190b5f9fd48ad19ce06a5ecff85b9bca745b8d833bb39e80f361a489` |
| 104 | 104_paper_portfolio_application_chronology.sql | `a6d61c7798718794371bd36ffa3249eff399ad494f7ba9489043cda55a4435f2` |
| 105 | 105_job_run_text_canonical.sql | `0339d44dc1524ea1cd84ec2d7e9af7bb8951ddbb328c7c96e845319b6da45170` |
| 106 | 106_job_run_claim_time_immutability.sql | `93a72edc04c1244675c7b900dffd7445eb7da71940832c4f47ec93b2c11596bd` |
| 107 | 107_preserve_job_run_terminal_contract.sql | `d74f72a2818ed9a30f644e84b67759d2427872c33f2f6841500a8567204f67fc` |
| 108 | 108_paper_effect_claim_chronology.sql | `45c08354cbc5866232a2210bfbebc2636134c838c96f8a16f512cd678d0d7128` |
| 109 | 109_strategy_version_text_canonical.sql | `30fd21ed510cc9417352801edbadf9f38436c2e235210c764ae078dee54b3036` |
| 110 | 110_dataset_version_text_canonical.sql | `31904059feb7ab86b58ea1a4978ae7e6a46cf0c503b95339d1c40825a5d9f07e` |
| 111 | 111_configuration_hash_canonical.sql | `9e3208ed081a121c82c812a695299fba8a815694f3b8644f26373611649fc54a` |
| 112 | 112_strategy_candidate_supported_decision.sql | `e36807dee98f853b9f3ad0733d68f0101914aecaa643d8674109a14f12e07c87` |
| 113 | 113_strategy_candidate_capacity_serialization.sql | `0ee2ea7aee3cd97a3d8c75b64a47e9088c3051f82c1aec66f53c53cb934aa25d` |
| 114 | 114_market_intelligence_entry_gate_scope.sql | `c7880ea02efd84253ee422c16bb60ff11724985c28342ee87ff3866b4a8a4ac4` |
| 115 | 115_market_intelligence_assessment_ingestion_chronology.sql | `56eaa3dcebd2520b86689197e0f63f8ab5298d1d2d6c993d677de2d3606c7d0b` |
| 116 | 116_schema_migration_checksum_not_null.sql | `a074dc2f2a7b580e510d6628f1b9ee746970aca7a9947b537be83b2a34eaab83` |
| 117 | 117_schema_migration_checksum_canonical.sql | `2920f6e745518dfd32a28764f92f85a2869e71536b93a9bbdf4078e2960abb78` |
| 118 | 118_schema_migration_history_immutability.sql | `d7698e21a7cc5cf2e779cb69fbff343c3bcc92c066eb02cfe759656303abdaf6` |
| 119 | 119_paper_environment_control.sql | `ed884db25427309f59361f59ca32b640f6317f7a4b3283e426807a04abd58c03` |
| 120 | 120_paper_environment_control_transition_guard.sql | `ab2ef093c8810fcb6079ed91abf3da3034d3df1e337d27c42a0e4354d20dd10a` |
| 121 | 121_paper_environment_control_chronology.sql | `68b4828c941d1bbc24ac9dbe092f42562b87ed560b9f358efc8b4ff77649a741` |
| 122 | 122_paper_environment_control_future_timestamp.sql | `3bf53f098238ec68a70a711995c5f31ba319ceb90c8de674efd5aded58858f0c` |
| 123 | 123_paper_environment_control_actor.sql | `d89af6eaa1917a2abc9ab881bc950273c3b4fd7385f11c3af3ea088e2386e063` |
| 124 | 124_paper_environment_control_database_principal.sql | `9bfcc880d2ab11c234499c8a2543812b20835b7ee92c06ec3332589c993417a5` |
| 125 | 125_paper_environment_resume_claim_guard.sql | `97a333154ee32bfae5ccffee59c06f884b87d396dded2566c3febda01261327a` |
| 126 | 126_paper_claim_resume_guard_index.sql | `c3a28ec725114561e622010d933b8ced5b35580178e80bc8d374a3cc1a2414de` |
| 127 | 127_paper_job_claim_environment_guard.sql | `daae3f0144da0616b5a511edf316e1f13d3f76e45eeb59369e51a1ebd9c173cc` |
| 128 | 128_paper_resume_guard_index_alignment.sql | `79f04d3a70201d841d1eecd8c1f3e261287787cbad8f78d6a6b7cdbc398d01af` |
| 129 | 129_paper_job_claim_due_time_guard.sql | `9bc592408cd5b6de01d780459a0104739a70ebc412825e5ce9797d5cc800579f` |
| 130 | 130_paper_job_claim_timestamp_guard.sql | `269a254d9a9a4a76fca404b32320af97a809b3b990d6f4d805c227755799b555` |
| 131 | 131_paper_job_completion_future_guard.sql | `1e2ef2db193b0ca423f18830762d1c0960a545aaff92448f8417f5a13e8b357a` |
| 132 | 132_paper_job_insert_claimed_state_guard.sql | `26598edc8545d3d5d83177f68627a105eadc03e20025da65aa2b538b759a61ad` |
| 133 | 133_paper_job_key_structure.sql | `2591aa08d377f00acf0744bf8be2c8c97b6c50f2313f517c2e7383dcab60812a` |
| 134 | 134_paper_job_claim_database_timestamp.sql | `1ceb107054c4da8b5c37f4b38d9f31af6ec46079935e00f3f97ec91d89b8fcfd` |
| 135 | 135_paper_job_key_exact_structure.sql | `3f8a7859517b9ab57bcb34562c658282e8158cdb1a23b610112a7a366b85ffec` |
| 136 | 136_paper_registered_job_key_canonical.sql | `f31897f918990ed237f28610f52802f8960b0b58bd6e7154c5c5e41faecc1390` |
| 137 | 137_paper_job_strategy_version_identity.sql | `f8d9c8c51d2f40592c8c7e31673695237ea145a780ad052714939779201c7693` |
| 138 | 138_paper_job_completion_claim_chronology.sql | `b6ba553504d14cdc0a40bb5d4b1cd72ab1a23ca5984de525965585d07a436516` |
| 139 | 139_restore_paper_logical_completion_time.sql | `06293c85fef5ac83e068375014378add3025159c8ddcceab37d4ea5e74a73d3e` |
| 140 | 140_paper_environment_control_database_timestamp.sql | `30ec9ea7f76b6985e997bbe004cb7b9105fb4d1e628883622620158478c5d1c2` |
| 141 | 141_paper_environment_control_sequence_order.sql | `b88e6a5d9555fc976373c7eeef72a2ed5ec43352df9a7057a3d895e9f640113c` |
| 142 | 142_paper_environment_control_sequence_generator_guard.sql | `06c26bf3dc34239454cb4038baaf13c94bdb06d8d32adf1997de386186b55dca` |
| 143 | 143_paper_effect_database_timestamp.sql | `bb83125544e719fc10b77ddc803703eb585a8a875396f1b92e5f60c4b8f5604d` |
| 144 | 144_paper_environment_control_sequence_readiness.sql | `6757027c554481b726f181eb154b819816298559859f24a0b5d6fc8de09cfcaf` |
| 145 | 145_paper_job_claim_control_sequence_readiness.sql | `45d3227bf79ba775607c4af3a39c3cc93cf7e51f9e524454082dfa61ae769a56` |
| 146 | 146_restore_complete_paper_job_claim_guard.sql | `0479bd502ea31104385cc27690c31248db57b83a8780566d684be5bd6fba5a11` |
| 147 | 147_paper_pnl_database_timestamp.sql | `6e0bd491d42ea33943648f213fa486de0e81661d09206cf7c70f2be03613fbaa` |
| 148 | 148_paper_portfolio_pnl_database_timestamp.sql | `edae9546baeab9e144811636c9579fd192e2100380c05609267c45d2b743db06` |
| 149 | 149_paper_risk_database_timestamp.sql | `e8d5fd3bb978262b434a9dee6ccbc6008954a04992fe74921cf61e7a6fc070ef` |
| 150 | 150_paper_order_database_timestamp.sql | `ddff0a9f6e0288d0d75b816401cab6cfbd6a2bb6290bf76ad4091ec1d533b74a` |
| 151 | 151_paper_fill_database_timestamp.sql | `0419540f2d5d54bd230040fe1eb0d360545cced500f57f990c390d488867973a` |
| 152 | 152_paper_signal_database_timestamp.sql | `bd92773db575886207f0ea83fc66491c7ecb9738ef2a0b5d52a99f25ed5bbd8b` |
| 153 | 153_paper_portfolio_application_database_timestamp.sql | `80788914a7618ab3ba4d763e3848aec10819780f3297fd1d9b1310981cfe207f` |
| 154 | 154_paper_portfolio_created_at_integrity.sql | `a527dbfdd7fadf4ee8b87b0c9bf92788e9a95f8ca3095f79a0c1444498307192` |
| 155 | 155_paper_portfolio_updated_at_integrity.sql | `2a0a12a2c089d3955bc24915731051fa237eec99b9019706c8de4616de385df0` |
| 156 | 156_paper_portfolio_deletion_integrity.sql | `12d97ab80e4a8146de565a61c6bbf1a742fec3478018543d8295a957030f1d54` |
| 157 | 157_paper_portfolio_identity_immutability.sql | `fa2bc7b84b921dea4933a3c69b2a62fa9407698dd0f33eef945f84f0964c71e2` |
| 158 | 158_paper_claim_delete_integrity.sql | `125f6b73494ea7c097d9ad0f5089f98a65774a49062c19edf4c90d58909b228f` |
| 159 | 159_paper_order_terminal_execution_truth.sql | `a2e5d6dd56b4cd611b5fadb462300e22fa5437f75ddc5511f1b2f6a398e8d810` |
| 160 | 160_paper_fill_terminal_integrity.sql | `b697891950034df011fcc7a2f0f6e4b45a5781209f577400e4b8af21b3531ff2` |
| 161 | 161_paper_terminal_event_time_integrity.sql | `c46ed3bba24390cd21870d548013e009e4124edb5c8b25e26e3b22ed60f91368` |
| 162 | 162_paper_fill_terminal_serialization.sql | `e28bc706f2dc092246bfe74d07e6a609d53bbc7dceccd6b52f692b38153fc4fa` |
| 163 | 163_paper_terminal_event_future_guard.sql | `d27016ae1ca01d1f5886aa6c36cdcb15011ea8bc67dead1df16aed6b9ffec5e9` |
| 164 | 164_paper_fill_future_event_guard.sql | `d6df5f98e989dbccb1935fc7e1e41d66cd948f1cd50f3561820a13a1de1b6786` |
| 165 | 165_paper_fill_future_history_preflight.sql | `f82ad9df664f697187449c31bb0136c6c14846b7209463f03522f4abd364d2c7` |
| 166 | 166_paper_effect_reconciliation_serialization.sql | `23313f117198eaf531d2cfcba6fddcb7325d966ddc835db777b12bc4d22d9b97` |
| 167 | 167_restore_paper_effect_database_timestamp.sql | `4ddef3098e000e596aa107868012a0c1d52638049c8450ec35ca18bb676f11fa` |
| 168 | 168_paper_signal_order_recovery_material.sql | `cb561274070ddac2a09eaaf61b8bd816d6c2705317f4c78e78c6786e235a9852` |
| 169 | 169_paper_reconciliation_audit_uniqueness.sql | `e5d14ea72cb5a060ff8333a52c3160663d1716e4d2c804e20158314fc94f69d8` |
| 170 | 170_paper_reconciliation_audit_job_identity.sql | `2fe23c646d055287fddc8a2cad03fe4c86394f4e20b5a16f15e8516dab3bbb86` |
| 171 | 171_paper_reconciliation_audit_terminal_truth.sql | `ba503f4fe9f83c9475821435efbed671647a6a44dc3fad984b7f30ea93d4a49e` |
| 172 | 172_paper_reconciliation_audit_entity_type.sql | `b85841ba4efd1c8518937cc207c47f1464bd77360c4ead2b6b8677bb55878d3a` |
| 173 | 173_paper_reconciliation_audit_payload.sql | `3374b7786f9295bb0de64cf4d43ff94b1e20df0160981a97003c700b21e187dc` |
| 174 | 174_paper_reconciliation_audit_effects.sql | `9c8b63857031a415b213cfe5cc4ba43f65cb36f1518b3d3e8ccb57c14ed3eadf` |
| 175 | 175_paper_reconciliation_audit_effect_shape.sql | `2323b25bf355a1ba6e6095c1b53b55b5b92b3f25bfe5773dddc1ab4766d4c075` |
| 176 | 176_paper_reconciliation_audit_effect_order.sql | `4a303ef3bda0ac86cdb9a16fc551ec7f416127c207bf6f679721994412af5d8e` |
| 177 | 177_paper_reconciliation_audit_database_timestamp.sql | `bbf037025c7d069867b6346eab48a12d12fcbb9a7c6d6fac33bdcd3ab49be355` |
| 178 | 178_paper_fill_single_portfolio_owner.sql | `f40173cd52ba2b4afd26d78780e9d8165d6ed796ab1be489c747d3f0999c108d` |
| 179 | 179_paper_portfolio_position_serialization.sql | `6790dd34872c6a8565beb2c4fec414c28d82025bc18aeba33fa6145e98612a59` |
| 180 | 180_paper_portfolio_pnl_serialization.sql | `94a02c042355951ae7e4a4ab4fb26269bc008884717cc2e6a692243785dc0d5b` |
| 181 | 181_paper_portfolio_pnl_identity.sql | `528b87ffcf5ddcb08ec0df135728aad2e942c57879c7d008c48a6e06d69b779f` |
| 182 | 182_paper_portfolio_pnl_effect_lineage.sql | `22c91814fbeb3298e5b5646fb691da614a4303c438bf252f56e1aef6ec74fee1` |
| 183 | 183_paper_portfolio_pnl_payload.sql | `db7bd3b96bd23f07294436ee3f701bd10c6f8a49104fd524c14483761a1deec7` |
| 184 | 184_paper_pnl_effect_portfolio_payload.sql | `17fd9652def4754bb86c15b142cb433300e71a5355c630d82ed2937401759911` |
| 185 | 185_paper_pnl_effect_portfolio_lineage.sql | `db1c72feab14662987f2bf259a76aaa16769030a5d8dc261925644e8ee2bd02a` |
| 186 | 186_paper_effect_portfolio_serialization.sql | `adb1e7120b5eea779b78ce964126a53ea2595f8699bdefae2fa025f2123c493d` |
| 187 | 187_paper_portfolio_application_serialization.sql | `c3479439f4693b6172ad9cbc82cf8622476a7c1e072faf759919a750bbe50bd1` |
| 188 | 188_paper_portfolio_pnl_validation_serialization.sql | `9442ff9394d47d02e9bca2400dc50fb9ac501e5692cf8d154667c10fd3ec2925` |
| 189 | 189_paper_portfolio_position_validation_serialization.sql | `ec9debf38d35c9ee6213319182b0907466969810d8de720fe44a77f646b751d0` |
| 190 | 190_paper_portfolio_identity_serialization.sql | `7caac921e12450d5ddd51f8812e06b747041181c1dc1fba4dd42b0a0be176b4b` |
| 191 | 191_paper_portfolio_application_cardinality.sql | `5139acd63edf2b670cde071305d8bffbf42dc79805eb1f7f4ec28d076b57ca85` |
| 192 | 192_paper_portfolio_application_pnl_completeness.sql | `6d80c5832c1b300ea00336710d2a7c85d8b79a96ff19c52d95ce3659a0f1e39f` |
| 193 | 193_paper_portfolio_effect_completeness.sql | `06100ddd4b4f51fe1cada535449abedd8c7c8ce9f5bba1bf34b187329a48757b` |
| 194 | 194_paper_pnl_effect_event_completeness.sql | `038b4d1d3b7960f1e70fb814feb30160377b7d30911431e0abf3384f3ce4f029` |
| 195 | 195_paper_pnl_event_effect_completeness.sql | `7e3c9269854c2f0cfd29d9f7856f43fa21a55dba9da879192fe3b542bba9123f` |
| 196 | 196_paper_pnl_event_payload.sql | `d1c6c3c08689e7cb1d632a177eb11391264381b9ec3958ff54474e0c8bee4878` |
| 197 | 197_paper_pnl_canonical_signal_lineage.sql | `53fbc2c79daa006df8dc87cead9f8b861d1da3f95ce530b5921530664c3e40f8` |
| 198 | 198_paper_pnl_canonical_history_preflight.sql | `40ecc5bb3a27f31b9cda9b529929447c3d53a59802c7ed1a7a3f9fc16216d5cb` |
| 199 | 199_paper_pnl_actionable_source_signal.sql | `2eff082bae52a76e30d4e3c52e7332a55efe07f15e62432233088736278044eb` |
| 200 | 200_paper_fill_effect_payload.sql | `440cf79742d1af73c51925f1369199f18be706fa75a04b53d200e033d43b8129` |
| 201 | 201_paper_portfolio_cash_projection.sql | `4214939556c2dc0c40eaf8fc000deea5ee1203ec9925a57a935dd53c23d7650a` |
| 202 | 202_paper_portfolio_position_projection.sql | `1232fd2b831dea27f669d7a91916a9c4c99c1fff1747da464be0728980fcb58f` |
| 203 | 203_paper_portfolio_pnl_transition_projection.sql | `b6dccfeae20000ee1b901962dc1f8c53487ecf7e659692bd1db97a90e5ab3f48` |
| 204 | 204_paper_partial_fill_cancellation_reconciliation.sql | `3c1e389a8df0d8893d1fbd77479b048465d894181435e2a87ba0a6e7ce5504e6` |
| 205 | 205_paper_terminal_effect_payload.sql | `4bfe960ae54e0515f947037ed313a065de4c7e3a0ee09885be030db19a98c58f` |
| 206 | 206_paper_risk_effect_payload.sql | `1d1b225621d1c8231103077723dee481a30dd33483a417497f9a2bdbe03f247b` |
| 207 | 207_paper_order_effect_payload.sql | `5d5b59eea92b7413a8261bf5f4aa5e3cdfe6661bd83e36726446a8d7f814b9c5` |
| 208 | 208_paper_signal_effect_payload.sql | `c52312f5fbb5d68e3040efa186eacabfbf7cf8bea91c97dd496a07dcaddc22aa` |
| 209 | 209_paper_order_signal_authorization.sql | `9c41b6e1235b42b5ed299af132ad54c9c8e12ff471e33bfc3fd0519dd11bd24a` |
| 210 | 210_paper_order_risk_authorization.sql | `8598e3b4fb672b72ca859029a3087e2082a3c0b7a6da58fa0bb26b7c71496a54` |
| 211 | 211_paper_fill_risk_authorization.sql | `b88e6137801ecdcd2263d35589f62a7c475210844527f30207c696b9f025e5dc` |
| 212 | 212_paper_terminal_risk_authorization.sql | `2b796e7ae30bc26b82231e92b0b6810d99d2afb53d84e56287e3aeb6988b3b60` |
| 213 | 213_paper_terminal_order_effect_lineage.sql | `f6a7c467c4ccb1c46fab50d5ecb00b500bd6d9b9abafce3f0f3a1743ced5edd2` |
| 214 | 214_paper_fill_order_effect_lineage.sql | `3f0b94efd1fe195a6d5cad80338a192dc8bc2a6e2fed5f0bc2ce6892a9f6d286` |
| 215 | 215_paper_order_signal_effect_lineage.sql | `13ef92b4fcba522bbff7bac33e431ea172fbfadaf3c8d69e5d2e432ce28b8bf0` |
| 216 | 216_paper_risk_signal_effect_lineage.sql | `95e4ecf39219e6b3b69f8c966b846c1ccbdeefd03b06548946eeaa788b69a331` |
| 217 | 217_paper_portfolio_application_timestamp_reciprocal.sql | `28f63b63c5009a0716d6d0164dea18768f89233c8fa9235aefc64c55d6c040d3` |
| 218 | 218_paper_fill_risk_effect_lineage.sql | `c18a17fdb10249821fb20e32f277b68d8081da14d3a9e98c82c2dd0b3b8d5e28` |
| 219 | 219_paper_terminal_risk_effect_lineage.sql | `5e3ccb46802ee60dd7aa6cd576b22f03a8113a7f092ea0e6b97e7c5dfe40b320` |
| 220 | 220_pit_market_bar_visibility.sql | `6ae42c472ba31f6a665a1f97ec91023b43841cf4c2757f6b31478f51d8324ebd` |
| 221 | 221_paper_signal_job_schedule.sql | `63898e8eb32e9f6a3f5888886d705a80b7b54cbd020e48e2d15c8f37a2a459c6` |
| 222 | 222_paper_fill_job_schedule.sql | `38e27b80cfd5cebcf7e5b6eb7b63a646150634f9d817453e771207849bae6d23` |
| 223 | 223_paper_terminal_job_schedule.sql | `e8ea37e0d2c20476203d02021f4ce570e09c6a42d7b0aa0c14e7f34d6d9bfcdf` |
| 224 | 224_paper_staged_fill_reconciliation.sql | `9aa123e6de84d6303743f5043a2a4eb356e3992abbcf8081ef201d00e9531967` |
| 225 | 225_paper_staged_entry_reconciliation.sql | `469823fab1f5d6b056c73700208d971ab896958c19c4332294aebbb9be213e0f` |
| 226 | 226_paper_staged_signal_reconciliation.sql | `aed4aceb9f6c92d1782eced8008a746b7fefaadf9cb8d00a32a2e017d82f8c91` |
| 227 | 227_paper_no_trade_decision_recovery.sql | `bde132d34a98d04bc5e243aed411eb3c4c07bdb5b7fa01bad0cadb039af5bd8c` |
| 228 | 228_paper_no_trade_effect_exclusivity.sql | `6924a25340f3f0d8a6127c1eabd569015d9843b279954f3d6ce7c36c2014c88a` |
| 229 | 229_paper_quarantine_evidence_guard.sql | `f1ea4bcb7e8288bdef8e6e4deff0f16660ada97673d65269538a58d64ffa0279` |
| 230 | 230_paper_control_snapshot_isolation.sql | `e8b9157a24265417e50e14ecc9746169989524d811e8549d0089400394f0e926` |
| 231 | 231_strategy_candidate_certified_evidence.sql | `f28cc208def200c55170ae611031bf7de8caa6b45d2d726113562106dcaa1856` |
| 232 | 232_paper_strategy_authorization.sql | `123d1b6e1c925560421e2419ad768946a6c9af52637da1a343de3996f2eba82c` |
| 233 | 233_paper_certified_outcomes.sql | `950733ceeb97e26da2e9f902807b759cf24319b2586dd1baa0548d93b94cd211` |
| 234 | 234_paper_handoff_intents.sql | `09ac97d2010058e2fba036eb3058c3567d1b38eb474fce3ead3160a30b014ecc` |
| 235 | 235_paper_startup_authorities.sql | `f855d24ccdb999e495bc82c13f2ec87a895f391b70951b6621c5f1d2511975bc` |
