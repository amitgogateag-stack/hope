# Windows dashboard comparison (M5 continuation)

The complete dashboard-server test module was executed twice on this Windows
workstation with the same Python 3.13 environment and disposable loopback HTTP
servers. The prior PR source was exported from Git into an isolated ignored
folder and used via pytest's pythonpath override; production code was not
modified for the baseline experiment.

| Source | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| PR #82 before continuation, dee2df18f9008f35ef312fe44157eb8391e992bb | 68 | 14 | 5 |
| Current continuation implementation | 70 | 14 | 5 |

The failure names match exactly. Two new diagnostic endpoint cases account for
the additional passes. The 14 failures are:

- test_server_is_loopback_only_and_serves_private_preview
- test_activation_view_is_held_across_local_file_read_and_response
- test_concurrent_client_limit_rejects_excess_and_recovers_capacity
- test_total_client_deadline_rejects_slow_drip_and_recovers_capacity
- test_m2_html_budget_is_independent_of_json_snapshot_budget[40000-200]
- test_server_missing_snapshot_or_unknown_paths_fail_closed[/index.html]
- test_server_serves_whitelisted_recovery_observations_only
- test_server_snapshot_schema_and_size_boundary[valid]
- test_server_reports_current_snapshot_and_never_exposes_db_details
- test_server_marks_old_snapshot_stale
- test_managed_server_serves_only_snapshot_from_exact_release
- test_legacy_inventory_is_independent_of_missing_paper_snapshot
- test_legacy_inventory_whitelists_fields
- test_snapshot_endpoint_whitelists_all_fields_even_if_file_contains_secrets

Windows-created writable fixture files report st_mode 0100666. The unchanged
POSIX safety reader rejects group/world writable bits with
BOUNDED_READ_UNTRUSTED_FILE, so tests expecting valid files to return HTTP 200
instead receive 503/UNAVAILABLE. A separate bounded experiment showed the same
reader accepting the identical regular-file bytes after Windows marked the
fixture read-only (st_mode 0100444). No reader assertion or production permission
check was bypassed or relaxed. This is evidence about fixture metadata, not a
claim that POSIX mode bits authenticate Windows ACLs.

The socket capacity/deadline cases also expose Winsock connection-abort behavior
(WinError 10053) where a Unix socket can yield EOF. On runs where that branch
returns EOF, those cases still reach the same read-only file-policy failure on
recovered-capacity HTTP requests. The tests and server retain their native Linux
security behavior. Five explicitly native socket/inode cases remain deferred
on Windows, as before.

These are pre-existing platform assumptions, not introduced concurrency or
readiness regressions. Supporting a Windows production dashboard would require
an independently designed native ACL/reparse-point and socket contract. This
Linux/AWS engineering batch does not claim that support. Final Linux CI and
PostgreSQL 16/17 results must be recorded separately after the exact proposed
commit completes.
