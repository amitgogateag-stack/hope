-- Append-only one-use handoff audit. This never authorizes an order, resumes
-- HALT, starts a service or substitutes for certified financial authorization.
CREATE TABLE paper_handoff_intents (
    authorization_sha256 TEXT PRIMARY KEY CHECK (authorization_sha256 ~ '^[0-9a-f]{64}$'),
    request_sha256 TEXT NOT NULL UNIQUE CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    first_cycle_key_sha256 TEXT NOT NULL UNIQUE CHECK (first_cycle_key_sha256 ~ '^[0-9a-f]{64}$'),
    release_commit TEXT NOT NULL CHECK (release_commit ~ '^[0-9a-f]{40}$' AND release_commit <> '7e9d341742818a97accce3e2301b1c7f96596795'),
    controller_process_sha256 TEXT NOT NULL CHECK (controller_process_sha256 ~ '^[0-9a-f]{64}$'),
    stage_process_sha256 TEXT NOT NULL CHECK (stage_process_sha256 ~ '^[0-9a-f]{64}$' AND stage_process_sha256 <> controller_process_sha256),
    deployment_receipt_sha256 TEXT NOT NULL CHECK (deployment_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    database_sha256 TEXT NOT NULL CHECK (database_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    authorizer_key_sha256 TEXT NOT NULL CHECK (authorizer_key_sha256 ~ '^[0-9a-f]{64}$'),
    policy_authority_sha256 TEXT NOT NULL CHECK (policy_authority_sha256 ~ '^[0-9a-f]{64}$'),
    minimum_policy_generation BIGINT NOT NULL CHECK (minimum_policy_generation > 0),
    observed_policy_generation BIGINT NOT NULL CHECK (observed_policy_generation >= minimum_policy_generation),
    issued_at BIGINT NOT NULL CHECK (issued_at >= 0),
    expires_at BIGINT NOT NULL CHECK (expires_at > issued_at AND expires_at-issued_at <= 60),
    witness_expires_at BIGINT NOT NULL CHECK (witness_expires_at > issued_at AND witness_expires_at <= expires_at),
    max_cycles INTEGER NOT NULL CHECK (max_cycles = 1),
    paper_only BOOLEAN NOT NULL CHECK (paper_only),
    live_enabled BOOLEAN NOT NULL CHECK (NOT live_enabled),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    database_principal TEXT NOT NULL DEFAULT current_user
);

CREATE TABLE paper_handoff_outcomes (
    authorization_sha256 TEXT PRIMARY KEY REFERENCES paper_handoff_intents(authorization_sha256),
    controller_process_sha256 TEXT NOT NULL CHECK (controller_process_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    policy_authority_sha256 TEXT NOT NULL CHECK (policy_authority_sha256 ~ '^[0-9a-f]{64}$'),
    observed_policy_generation BIGINT NOT NULL CHECK (observed_policy_generation > 0),
    outcome TEXT NOT NULL CHECK (outcome IN ('ACKNOWLEDGED','ABORTED')),
    witness_expires_at BIGINT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    database_principal TEXT NOT NULL DEFAULT current_user
);

CREATE FUNCTION hope_guard_paper_handoff_insert()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    control_state TEXT;
    history_max BIGINT;
    generator_last BIGINT;
    generator_called BOOLEAN;
    intent RECORD;
    instant TIMESTAMPTZ;
    observed_generation BIGINT;
BEGIN
    PERFORM hope_require_fresh_paper_control_snapshot();
    PERFORM pg_advisory_xact_lock(hashtext('hope:paper:environment-control')::bigint);
    SELECT state INTO control_state FROM paper_environment_control_events ORDER BY control_sequence DESC LIMIT 1;
    SELECT max(control_sequence) INTO history_max FROM paper_environment_control_events;
    SELECT last_value,is_called INTO generator_last,generator_called FROM paper_environment_control_events_control_sequence_seq;
    IF control_state IS DISTINCT FROM 'RUNNING' OR history_max IS NULL OR generator_last < history_max
       OR (generator_last = history_max AND generator_called IS NOT TRUE) THEN
        RAISE EXCEPTION 'PAPER_HANDOFF_CONTROL_NOT_READY' USING ERRCODE='23514';
    END IF;
    instant := clock_timestamp();
    SELECT greatest(coalesce((SELECT max(observed_policy_generation) FROM paper_handoff_intents WHERE policy_authority_sha256=NEW.policy_authority_sha256),0),
                    coalesce((SELECT max(observed_policy_generation) FROM paper_handoff_outcomes WHERE policy_authority_sha256=NEW.policy_authority_sha256),0))
      INTO observed_generation;
    IF NEW.observed_policy_generation < observed_generation THEN
        RAISE EXCEPTION 'PAPER_HANDOFF_POLICY_ROLLBACK' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME = 'paper_handoff_intents' THEN
        IF instant < to_timestamp(NEW.issued_at) OR instant >= to_timestamp(NEW.witness_expires_at) THEN
            RAISE EXCEPTION 'PAPER_HANDOFF_AUTHORITY_EXPIRED' USING ERRCODE='23514';
        END IF;
        NEW.claimed_at := instant;
    ELSE
        SELECT * INTO intent FROM paper_handoff_intents WHERE authorization_sha256=NEW.authorization_sha256;
        IF intent IS NULL OR intent.controller_process_sha256 IS DISTINCT FROM NEW.controller_process_sha256
           OR intent.evidence_sha256 IS DISTINCT FROM NEW.evidence_sha256
           OR intent.policy_authority_sha256 IS DISTINCT FROM NEW.policy_authority_sha256
           OR NEW.observed_policy_generation < intent.observed_policy_generation
           OR intent.database_principal IS DISTINCT FROM current_user
           OR NEW.witness_expires_at > intent.expires_at
           OR instant >= to_timestamp(NEW.witness_expires_at) OR instant >= to_timestamp(intent.expires_at) THEN
            RAISE EXCEPTION 'PAPER_HANDOFF_OUTCOME_UNVERIFIED' USING ERRCODE='23514';
        END IF;
        NEW.recorded_at := instant;
    END IF;
    NEW.database_principal := current_user;
    RETURN NEW;
END;
$$;

CREATE INDEX paper_handoff_intent_policy_generation ON paper_handoff_intents(policy_authority_sha256,observed_policy_generation DESC);
CREATE INDEX paper_handoff_outcome_policy_generation ON paper_handoff_outcomes(policy_authority_sha256,observed_policy_generation DESC);

CREATE TRIGGER trg_paper_handoff_intent_insert BEFORE INSERT ON paper_handoff_intents
FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_handoff_insert();
CREATE TRIGGER trg_paper_handoff_outcome_insert BEFORE INSERT ON paper_handoff_outcomes
FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_handoff_insert();
CREATE TRIGGER trg_paper_handoff_intent_immutable BEFORE UPDATE OR DELETE ON paper_handoff_intents
FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_paper_handoff_outcome_immutable BEFORE UPDATE OR DELETE ON paper_handoff_outcomes
FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_paper_handoff_intent_truncate BEFORE TRUNCATE ON paper_handoff_intents
FOR EACH STATEMENT EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_paper_handoff_outcome_truncate BEFORE TRUNCATE ON paper_handoff_outcomes
FOR EACH STATEMENT EXECUTE FUNCTION hope_reject_audit_event_mutation();
