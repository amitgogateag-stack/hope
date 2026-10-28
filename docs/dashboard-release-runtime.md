# Dashboard release environments and inactive PAPER prerequisites

This is a reviewed deployment procedure, not deployment authorization. No host
commands in this document were executed as part of the engineering change.
PAPER remains inactive; LIVE remains disabled. Keep the frozen baseline at
`7e9d341742818a97accce3e2301b1c7f96596795` unchanged.

## Explicit runtime contract

The default `--runtime shared` preserves `/opt/hope-operations/venv/bin/python`.
For an isolated environment select `--runtime release` on both **preflight and
activate**. It requires `/opt/hope-operations/release-venvs/<target>/bin/python`;
missing or untrusted storage fails rather than falling back to the shared venv.
No arbitrary interpreter pathname or automatic discovery is accepted.

Preflight authenticates release code and exact integration-head CI, checks the
target and prior runtimes, dependencies, Python 3.13, executable and venv prefix,
and read-only database role/schema compatibility. Runtime directories and link
targets must be trusted. Standard venv interpreter links are permitted only with
trusted ownership and destination ancestors. The launcher scrubs ambient Python
and loader variables before adding the reviewed source path.

The v2 durable activation journal records target and previous runtime modes.
Systemd commands, startup/request gates and listener verification use that same
selection. A common base Python executable does not establish venv identity:
the gate checks `sys.executable` and `sys.prefix`, and the listener checks argv.
Shared-only activations continue to write v1 records. Rollback restores the
previous configuration bytes, pointer and journal. Before restarting a legacy
shared-runtime release, its rollback journal is converted to the v1 contract it
understands. Keep **both** code releases and environments until rollback is no
longer required. Never remove an environment to force a fallback.

After separate deployment approval, execute the authenticated release's module
with the existing hardened operator launcher and these arguments (replace all
placeholders with verified evidence):

```text
preflight --target <SHA> --expected-current <SHARED_SHA> --runtime release --ci-run-id <RUN> --ci-run-attempt <ATTEMPT>
activate --target <SHA> --expected-current <SHARED_SHA> --runtime release --ci-run-id <RUN> --ci-run-attempt <ATTEMPT> --approve-dashboard-change
status
rollback --target <SHA> --expected-current <SHARED_SHA> --approve-dashboard-change
```

Rollback obtains runtime selection from the durable record, not a new CLI choice.
Interrupted or failed verification remains fenced until explicit recovery.
An unmerged PR is not an approved integration release: activation still requires
exact `p0-pit-context` push CI and its bot status marker. Do not loosen that gate.

## Missing PAPER unit

An absent unit continues to block ordinary dashboard activation. First run
`paper-audit` read-only. It intentionally exits blocked, even when every bounded
scan completes. Review actual host process identities, alternative/transient
units, timers/sockets/paths, user schedulers, cron/at jobs, deployment pointers,
and restart enforcement. Counts alone never establish host-wide absence. An
UNVERIFIED diagnostic must be resolved before proceeding.

After that review and separate host-change approval, `paper-mask
--approve-reviewed-paper-mask` creates only
`/etc/systemd/system/hope-paper.service -> /dev/null`. It does not install a
worker, environment, credentials, database records or executable service. It
never starts, stops, enables or unmasks PAPER. The helper requires complete
diagnostics, rechecks canonical absence under the activation guard, rejects
existing units/drop-ins, exclusively creates the mask, reloads systemd and
verifies exact inactive/masked state and zero PID. The mask is retained on
reload or verification failure. Reconcile it manually; do not automatically
remove it as error recovery. Other execution paths still require host review.

Dashboard preflight remains a separate action with all original safety gates.
A mask is not worker health, fresh data, execution authorization or readiness.
Provisioning or starting a PAPER worker requires a separate reviewed procedure.

## Release reconciliation

Use the existing read-only AWS release/readiness audit and record GitHub branch,
commit/tree, exact CI run/attempt and checksum alongside deployed/runtime/
snapshot identities. A deployment may intentionally remain behind GitHub.
Do not present historical AWS observations as a current deployed SHA. This
engineering batch performed no AWS inspection or mutation. Stage only a merged,
approved SHA with successful exact-head integration CI, then obtain separate
deployment approval. Never activate the historical staged artifact as latest.
