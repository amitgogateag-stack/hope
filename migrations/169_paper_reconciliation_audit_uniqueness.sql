-- A reconciled PAPER job must have at most one durable no-replay audit receipt,
-- independent of application-generated audit_event_id values.
CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_reconciliation_audit_job_run
ON audit_events (entity_id)
WHERE event_type = 'PAPER_RUN_RECONCILED'
  AND entity_type = 'JOB_RUN';
