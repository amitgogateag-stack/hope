# Transaction-owned write-set prototype — production NO-GO

The isolated integration candidate combines PR96 at
71868c74b066e48f44eefcd77e1fed1f40d2fd15 (including PR95) with PR93 at
594a358c1ca8a99cebfd77ffd8ea1391aac04e3f. Migration239 is preserved from
PR93, including its ownership/catalog capability checks and seven authority
admission triggers. Migrations001–238, the kernel fence, the DENY-only verifier,
strategies and risk thresholds are unchanged. The witness SQL installer remains
an explicit disposable experiment, not a numbered migration or production path.
Both source branches remain unchanged and unmerged. Independent branch CI does
not establish combined behavior; use this candidate's exact-head evidence.

For explicitly registered experimental executors, triggers capture every
INSERT/UPDATE/DELETE with complete OLD/NEW JSON rows on seventeen real HOPE
financial/lifecycle tables. No timestamp or economic field is omitted. The
installer refuses TRUNCATE and bounds individual rows, event count and total
snapshot size. A separately committed one-use reservation binds authorization,
job, actual full xid, backend/start time, database/cluster, login, deadline and
policy generation. Financial rollback does not restore that reservation.
The modeled reservation has a separate deadline bounded to fifteen seconds and
the signed execution expiry. It does not reuse the initial execution row's
short observation deadline as if it were renewable authority. All original
fresh closing-witness checks and deadlines remain active and unchanged.

The executor cannot insert capture/approval records even following the tested
accidental INSERT grant. Ledger UPDATE/DELETE/TRUNCATE is refused. At canonical
COMMIT, deferred constraints compare the exact immutable approved event list,
seal, full transaction identity and successful core financial witness; acquire
the existing HALT serialization lock and prototype policy lock; and recheck
deadline/revocation/generation. Any additional captured write after sealing
invalidates the transaction. Read-only repeatable-read reconciliation reports
ABSENT, RECOVERY_REQUIRED or FINANCIAL_COMMIT_RECORDED, never replay permission.

The client verifies real Ed25519 signatures against a pinned prototype public
key, exact canonical context obtained from the actual current database claim,
issuer, key digest, role, scope, release/image digests and deadline. Private keys
exist only in disposable fixtures. Production approval unconditionally refuses.

## Authority limitations

The fixture owner connection explicitly MODELS external authority. Its expected
manifest comes from the trusted test harness, including the executor snapshot;
it is not independent observation of uncommitted PostgreSQL rows. The database
stores the authenticated response digest but does not independently verify its
signature. A trusted production producer, independent expected-write oracle,
key custody, signer/observer/proxy isolation and independently managed roots
are NOT implemented or established. Signed release/image values do not prove
runtime integrity. A privileged database owner remains trusted and can alter
DDL. Unregistered principals are outside this opt-in experiment. PostgreSQL
COMMIT does not atomically authenticate external kernel state or key custody.
No result resolves the twenty mandatory A1 production acceptance requirements.

## Reproduction

Use only a disposable PostgreSQL16 or17 server with the existing test database
creation permissions. Set HOPE_DATABASE_URL and HOPE_TEST_POSTGRES_MAJOR.

```powershell
python -m pip install -c constraints/ci-test.txt -e '.[test]'
python -m pytest -v tests/integration/test_transaction_write_set_witness.py tests/unit/test_write_set_witness.py
```

The dedicated workflow records the exact tested commit, SQL checksum and JUnit
results per major. Existing full HOPE and native kernel workflows remain intact.
Results must be taken from the final exact head, not this design description.
The integrated adversarial cases grant a predefined server-program role or a
privileged catalog routine only after the actual write-set seal. They invoke
neither capability, require the original financial COMMIT guard to reject the
changed principal, assert zero committed effects and preserve the consumed
reservation for recovery. Existing positive India/USA cases still exercise the
same guards. No assertion or expiry deadline is relaxed for integration.
Engineering integration and operational PAPER remain NO-GO. Human security
review, production root provisioning and independent acceptance remain required.
G7FX is NOT STARTED and deferred.
