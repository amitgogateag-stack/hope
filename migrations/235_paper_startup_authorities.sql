-- Separate one-use startup bootstrap and execution authority; never clears HALT.
-- All effects still belong to the existing certified scheduler and financial DB guards.
CREATE TABLE paper_startup_claims (
    authorization_sha256 TEXT PRIMARY KEY CHECK (authorization_sha256 ~ '^[0-9a-f]{64}$'),
    release_commit TEXT NOT NULL CHECK (release_commit ~ '^[0-9a-f]{40}$' AND release_commit<>'7e9d341742818a97accce3e2301b1c7f96596795'),
    deployment_receipt_sha256 TEXT NOT NULL UNIQUE CHECK (deployment_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    schema_catalog_sha256 TEXT NOT NULL CHECK (schema_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    max_cycles SMALLINT NOT NULL CHECK (max_cycles=0),
    request_sha256 TEXT NOT NULL UNIQUE CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    handoff_authorization_sha256 TEXT NOT NULL UNIQUE REFERENCES paper_handoff_intents(authorization_sha256),
    first_cycle_key_sha256 TEXT NOT NULL UNIQUE CHECK (first_cycle_key_sha256 ~ '^[0-9a-f]{64}$'),
    database_sha256 TEXT NOT NULL CHECK (database_sha256 ~ '^[0-9a-f]{64}$'),
    authorizer_key_sha256 TEXT NOT NULL CHECK (authorizer_key_sha256 ~ '^[0-9a-f]{64}$'),
    issued_at BIGINT NOT NULL CHECK (issued_at>=0),
    expires_at BIGINT NOT NULL CHECK (expires_at>issued_at AND expires_at-issued_at<=60),
    paper_only BOOLEAN NOT NULL CHECK (paper_only),
    live_enabled BOOLEAN NOT NULL CHECK (NOT live_enabled),
    evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    controller_process_sha256 TEXT NOT NULL CHECK (controller_process_sha256 ~ '^[0-9a-f]{64}$'),
    policy_authority_sha256 TEXT NOT NULL CHECK (policy_authority_sha256 ~ '^[0-9a-f]{64}$'),
    observed_policy_generation BIGINT NOT NULL CHECK (observed_policy_generation>0),
    witness_expires_at BIGINT NOT NULL,
    database_principal TEXT NOT NULL DEFAULT current_user,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paper_startup_bootstraps (
    authorization_sha256 TEXT PRIMARY KEY REFERENCES paper_startup_claims(authorization_sha256),
    schema_catalog_sha256 TEXT NOT NULL CHECK (schema_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    controller_process_sha256 TEXT NOT NULL CHECK (controller_process_sha256 ~ '^[0-9a-f]{64}$'),
    policy_authority_sha256 TEXT NOT NULL CHECK (policy_authority_sha256 ~ '^[0-9a-f]{64}$'),
    observed_policy_generation BIGINT NOT NULL CHECK (observed_policy_generation>0),
    witness_expires_at BIGINT NOT NULL,
    database_principal TEXT NOT NULL DEFAULT current_user,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paper_startup_executions (
    authorization_sha256 TEXT PRIMARY KEY CHECK (authorization_sha256 ~ '^[0-9a-f]{64}$'),
    startup_authorization_sha256 TEXT NOT NULL UNIQUE REFERENCES paper_startup_bootstraps(authorization_sha256),
    job_run_id UUID NOT NULL UNIQUE,
    job_key TEXT NOT NULL CHECK (length(job_key) BETWEEN 1 AND 256 AND job_key=btrim(job_key)),
    scheduled_for TIMESTAMPTZ NOT NULL,
    max_cycles SMALLINT NOT NULL CHECK (max_cycles=1),
    request_sha256 TEXT NOT NULL UNIQUE CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    handoff_authorization_sha256 TEXT NOT NULL UNIQUE REFERENCES paper_handoff_intents(authorization_sha256),
    first_cycle_key_sha256 TEXT NOT NULL UNIQUE CHECK (first_cycle_key_sha256 ~ '^[0-9a-f]{64}$'),
    database_sha256 TEXT NOT NULL CHECK (database_sha256 ~ '^[0-9a-f]{64}$'),
    authorizer_key_sha256 TEXT NOT NULL CHECK (authorizer_key_sha256 ~ '^[0-9a-f]{64}$'),
    issued_at BIGINT NOT NULL CHECK (issued_at>=0),
    expires_at BIGINT NOT NULL CHECK (expires_at>issued_at AND expires_at-issued_at<=60),
    paper_only BOOLEAN NOT NULL CHECK (paper_only),
    live_enabled BOOLEAN NOT NULL CHECK (NOT live_enabled),
    evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    controller_process_sha256 TEXT NOT NULL CHECK (controller_process_sha256 ~ '^[0-9a-f]{64}$'),
    policy_authority_sha256 TEXT NOT NULL CHECK (policy_authority_sha256 ~ '^[0-9a-f]{64}$'),
    observed_policy_generation BIGINT NOT NULL CHECK (observed_policy_generation>0),
    witness_expires_at BIGINT NOT NULL,
    database_principal TEXT NOT NULL DEFAULT current_user,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paper_startup_completions (
    authorization_sha256 TEXT PRIMARY KEY REFERENCES paper_startup_executions(authorization_sha256),
    job_run_id UUID NOT NULL UNIQUE REFERENCES job_runs(job_run_id),
    evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    controller_process_sha256 TEXT NOT NULL CHECK (controller_process_sha256 ~ '^[0-9a-f]{64}$'),
    policy_authority_sha256 TEXT NOT NULL CHECK (policy_authority_sha256 ~ '^[0-9a-f]{64}$'),
    observed_policy_generation BIGINT NOT NULL CHECK (observed_policy_generation>0),
    witness_expires_at BIGINT NOT NULL,
    database_principal TEXT NOT NULL DEFAULT current_user,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE paper_startup_commit_witnesses (
    authorization_sha256 TEXT PRIMARY KEY REFERENCES paper_startup_executions(authorization_sha256),
    job_run_id UUID NOT NULL UNIQUE REFERENCES job_runs(job_run_id),
    evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    controller_process_sha256 TEXT NOT NULL CHECK (controller_process_sha256 ~ '^[0-9a-f]{64}$'),
    policy_authority_sha256 TEXT NOT NULL CHECK (policy_authority_sha256 ~ '^[0-9a-f]{64}$'),
    observed_policy_generation BIGINT NOT NULL CHECK (observed_policy_generation>0),
    witness_expires_at BIGINT NOT NULL,
    database_principal TEXT NOT NULL DEFAULT current_user,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION hope_guard_paper_startup_write()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    control_state TEXT;
    history_max BIGINT;
    generator_last BIGINT;
    generator_called BOOLEAN;
    parent RECORD;
    instant TIMESTAMPTZ;
    generation_floor BIGINT;
BEGIN
    PERFORM hope_require_fresh_paper_control_snapshot();
    PERFORM pg_advisory_xact_lock(hashtext('hope:paper:environment-control')::bigint);
    SELECT state INTO control_state FROM paper_environment_control_events ORDER BY control_sequence DESC LIMIT 1;
    SELECT max(control_sequence) INTO history_max FROM paper_environment_control_events;
    SELECT last_value,is_called INTO generator_last,generator_called FROM paper_environment_control_events_control_sequence_seq;
    IF control_state IS DISTINCT FROM 'RUNNING' OR history_max IS NULL OR generator_last<history_max
       OR (generator_last=history_max AND generator_called IS NOT TRUE) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_CONTROL_NOT_READY' USING ERRCODE='23514';
    END IF;
    instant:=clock_timestamp();
    SELECT greatest(
        coalesce((SELECT max(observed_policy_generation) FROM paper_handoff_intents WHERE policy_authority_sha256=NEW.policy_authority_sha256),0),
        coalesce((SELECT max(observed_policy_generation) FROM paper_handoff_outcomes WHERE policy_authority_sha256=NEW.policy_authority_sha256),0),
        coalesce((SELECT max(observed_policy_generation) FROM paper_startup_claims WHERE policy_authority_sha256=NEW.policy_authority_sha256),0),
        coalesce((SELECT max(observed_policy_generation) FROM paper_startup_bootstraps WHERE policy_authority_sha256=NEW.policy_authority_sha256),0),
        coalesce((SELECT max(observed_policy_generation) FROM paper_startup_executions WHERE policy_authority_sha256=NEW.policy_authority_sha256),0),
        coalesce((SELECT max(observed_policy_generation) FROM paper_startup_completions WHERE policy_authority_sha256=NEW.policy_authority_sha256),0),
        coalesce((SELECT max(observed_policy_generation) FROM paper_startup_commit_witnesses WHERE policy_authority_sha256=NEW.policy_authority_sha256),0)) INTO generation_floor;
    IF NEW.observed_policy_generation<generation_floor OR instant>=to_timestamp(NEW.witness_expires_at) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_POLICY_OR_EXPIRY_UNVERIFIED' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='paper_startup_claims' THEN
        SELECT * INTO parent FROM paper_handoff_intents WHERE authorization_sha256=NEW.handoff_authorization_sha256;
        IF parent IS NULL OR parent.release_commit IS DISTINCT FROM NEW.release_commit
           OR parent.first_cycle_key_sha256 IS DISTINCT FROM NEW.first_cycle_key_sha256
           OR parent.database_sha256 IS DISTINCT FROM NEW.database_sha256
           OR parent.deployment_receipt_sha256 IS DISTINCT FROM NEW.deployment_receipt_sha256
           OR parent.authorizer_key_sha256=NEW.authorizer_key_sha256
           OR EXISTS (SELECT 1 FROM paper_startup_executions e LEFT JOIN paper_startup_completions c USING(authorization_sha256)
                      WHERE e.database_sha256=NEW.database_sha256 AND c.authorization_sha256 IS NULL) THEN
            RAISE EXCEPTION 'PAPER_STARTUP_INTENT_UNVERIFIED' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='paper_startup_bootstraps' THEN
        SELECT * INTO parent FROM paper_startup_claims WHERE authorization_sha256=NEW.authorization_sha256;
        IF parent IS NULL OR parent.schema_catalog_sha256 IS DISTINCT FROM NEW.schema_catalog_sha256 OR parent.evidence_sha256 IS DISTINCT FROM NEW.evidence_sha256 THEN
            RAISE EXCEPTION 'PAPER_STARTUP_BOOTSTRAP_UNVERIFIED' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='paper_startup_executions' THEN
        SELECT * INTO parent FROM paper_startup_claims WHERE authorization_sha256=NEW.startup_authorization_sha256;
        IF parent IS NULL OR parent.handoff_authorization_sha256 IS DISTINCT FROM NEW.handoff_authorization_sha256
           OR parent.first_cycle_key_sha256 IS DISTINCT FROM NEW.first_cycle_key_sha256
           OR parent.database_sha256 IS DISTINCT FROM NEW.database_sha256
           OR parent.authorizer_key_sha256=NEW.authorizer_key_sha256
           OR EXISTS (SELECT 1 FROM job_runs WHERE job_run_id=NEW.job_run_id) THEN
            RAISE EXCEPTION 'PAPER_STARTUP_EXECUTION_UNVERIFIED' USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT * INTO parent FROM paper_startup_executions WHERE authorization_sha256=NEW.authorization_sha256;
        IF parent IS NULL OR parent.job_run_id IS DISTINCT FROM NEW.job_run_id OR parent.evidence_sha256 IS DISTINCT FROM NEW.evidence_sha256 OR NOT EXISTS (
            SELECT 1 FROM job_runs j JOIN paper_certified_outcomes o USING(job_run_id)
            WHERE j.job_run_id=NEW.job_run_id AND j.status='SUCCEEDED' AND j.job_key=parent.job_key
              AND j.scheduled_for=parent.scheduled_for AND (TG_TABLE_NAME='paper_startup_commit_witnesses' OR EXISTS (
                SELECT 1 FROM audit_events a WHERE a.event_type='PAPER_RUN_RECONCILED' AND a.entity_id=NEW.job_run_id::text))) THEN
            RAISE EXCEPTION 'PAPER_STARTUP_COMPLETION_UNVERIFIED' USING ERRCODE='23514';
        END IF;
    END IF;
    IF parent.controller_process_sha256 IS DISTINCT FROM NEW.controller_process_sha256
       OR parent.policy_authority_sha256 IS DISTINCT FROM NEW.policy_authority_sha256
       OR parent.database_principal IS DISTINCT FROM current_user
       OR NEW.observed_policy_generation<parent.observed_policy_generation THEN
        RAISE EXCEPTION 'PAPER_STARTUP_OWNER_UNVERIFIED' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME IN ('paper_startup_claims','paper_startup_executions') THEN
        IF instant<to_timestamp(NEW.issued_at) OR NEW.witness_expires_at>NEW.expires_at
           OR NEW.expires_at>parent.expires_at THEN
            RAISE EXCEPTION 'PAPER_STARTUP_TIME_UNVERIFIED' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.witness_expires_at>parent.expires_at THEN
        RAISE EXCEPTION 'PAPER_STARTUP_TIME_UNVERIFIED' USING ERRCODE='23514';
    END IF;
    NEW.database_principal:=current_user;NEW.recorded_at:=instant;
    RETURN NEW;
END;
$$;

CREATE INDEX paper_startup_claims_policy_generation ON paper_startup_claims(policy_authority_sha256,observed_policy_generation DESC);
CREATE TRIGGER trg_paper_startup_claims_insert BEFORE INSERT ON paper_startup_claims FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_startup_write();
CREATE TRIGGER trg_paper_startup_claims_immutable BEFORE UPDATE OR DELETE ON paper_startup_claims FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_paper_startup_claims_truncate BEFORE TRUNCATE ON paper_startup_claims FOR EACH STATEMENT EXECUTE FUNCTION hope_reject_audit_event_mutation();

CREATE INDEX paper_startup_bootstraps_policy_generation ON paper_startup_bootstraps(policy_authority_sha256,observed_policy_generation DESC);
CREATE TRIGGER trg_paper_startup_bootstraps_insert BEFORE INSERT ON paper_startup_bootstraps FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_startup_write();
CREATE TRIGGER trg_paper_startup_bootstraps_immutable BEFORE UPDATE OR DELETE ON paper_startup_bootstraps FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_paper_startup_bootstraps_truncate BEFORE TRUNCATE ON paper_startup_bootstraps FOR EACH STATEMENT EXECUTE FUNCTION hope_reject_audit_event_mutation();

CREATE INDEX paper_startup_executions_policy_generation ON paper_startup_executions(policy_authority_sha256,observed_policy_generation DESC);
CREATE TRIGGER trg_paper_startup_executions_insert BEFORE INSERT ON paper_startup_executions FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_startup_write();
CREATE TRIGGER trg_paper_startup_executions_immutable BEFORE UPDATE OR DELETE ON paper_startup_executions FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_paper_startup_executions_truncate BEFORE TRUNCATE ON paper_startup_executions FOR EACH STATEMENT EXECUTE FUNCTION hope_reject_audit_event_mutation();

CREATE INDEX paper_startup_completions_policy_generation ON paper_startup_completions(policy_authority_sha256,observed_policy_generation DESC);
CREATE TRIGGER trg_paper_startup_completions_insert BEFORE INSERT ON paper_startup_completions FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_startup_write();
CREATE TRIGGER trg_paper_startup_completions_immutable BEFORE UPDATE OR DELETE ON paper_startup_completions FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_paper_startup_completions_truncate BEFORE TRUNCATE ON paper_startup_completions FOR EACH STATEMENT EXECUTE FUNCTION hope_reject_audit_event_mutation();

CREATE INDEX paper_startup_commit_witnesses_policy_generation ON paper_startup_commit_witnesses(policy_authority_sha256,observed_policy_generation DESC);
CREATE TRIGGER trg_paper_startup_commit_witnesses_insert BEFORE INSERT ON paper_startup_commit_witnesses FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_startup_write();
CREATE TRIGGER trg_paper_startup_commit_witnesses_immutable BEFORE UPDATE OR DELETE ON paper_startup_commit_witnesses FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_paper_startup_commit_witnesses_truncate BEFORE TRUNCATE ON paper_startup_commit_witnesses FOR EACH STATEMENT EXECUTE FUNCTION hope_reject_audit_event_mutation();

-- The final transaction, not a health acknowledgment, owns the financial expiry gate.
CREATE FUNCTION hope_verify_startup_financial_commit()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    authority RECORD;
    witness RECORD;
    final_status TEXT;
BEGIN
    SELECT * INTO authority FROM paper_startup_executions WHERE job_run_id=NEW.job_run_id;
    IF authority IS NULL THEN RETURN NULL; END IF;
    SELECT status INTO final_status FROM job_runs WHERE job_run_id=NEW.job_run_id;
    IF final_status IS DISTINCT FROM 'SUCCEEDED' THEN RETURN NULL; END IF;
    SELECT * INTO witness FROM paper_startup_commit_witnesses WHERE authorization_sha256=authority.authorization_sha256;
    IF witness IS NULL OR witness.job_run_id IS DISTINCT FROM NEW.job_run_id
       OR witness.evidence_sha256 IS DISTINCT FROM authority.evidence_sha256
       OR witness.controller_process_sha256 IS DISTINCT FROM authority.controller_process_sha256
       OR authority.database_principal IS DISTINCT FROM current_user
       OR clock_timestamp()>=least(to_timestamp(witness.witness_expires_at),to_timestamp(authority.expires_at)) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_COMMIT_UNVERIFIED' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
-- Controlled startup is deliberately public-schema-only. Private dashboard
-- copies retain their existing financial guards and never borrow public startup.
DO $$
BEGIN
    IF current_schema()='public' THEN
        CREATE CONSTRAINT TRIGGER trg_startup_financial_commit
        AFTER INSERT OR UPDATE ON public.job_runs DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.hope_verify_startup_financial_commit();
    END IF;
END;
$$;
