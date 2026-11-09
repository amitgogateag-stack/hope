-- Startup principals are permanently scoped to independently admitted cycles.
-- Never backfill a historical witness with an invented transaction identity.
ALTER TABLE paper_startup_commit_witnesses ADD COLUMN financial_transaction_id xid8;
CREATE TABLE paper_startup_financial_transactions (
    authorization_sha256 TEXT PRIMARY KEY REFERENCES paper_startup_executions(authorization_sha256),
    transaction_id xid8 NOT NULL,
    database_principal TEXT NOT NULL DEFAULT current_user,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION hope_require_startup_financial_role()
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM pg_roles r WHERE (r.rolname=current_user OR pg_has_role(current_user,r.oid,'MEMBER'))
        AND (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls
            OR has_database_privilege(r.oid,current_database(),'TEMPORARY')
            OR has_database_privilege(r.oid,current_database(),'CREATE')
            OR EXISTS(SELECT 1 FROM pg_namespace n WHERE has_schema_privilege(r.oid,n.oid,'CREATE')))) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_ROLE_UNSAFE' USING ERRCODE='23514';
    END IF;
END;
$$;
CREATE FUNCTION hope_require_startup_financial_control()
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE state_now TEXT; history_max BIGINT; generator_last BIGINT; generator_called BOOLEAN;
BEGIN
    PERFORM hope_require_startup_financial_role();
    PERFORM hope_require_fresh_paper_control_snapshot();
    PERFORM pg_advisory_xact_lock(hashtext('hope:paper:environment-control')::bigint);
    SELECT state INTO state_now FROM paper_environment_control_events ORDER BY control_sequence DESC LIMIT 1;
    SELECT max(control_sequence) INTO history_max FROM paper_environment_control_events;
    SELECT last_value,is_called INTO generator_last,generator_called FROM paper_environment_control_events_control_sequence_seq;
    IF state_now IS DISTINCT FROM 'RUNNING' OR history_max IS NULL OR generator_last<history_max
       OR (generator_last=history_max AND generator_called IS NOT TRUE) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_CONTROL_UNVERIFIED' USING ERRCODE='23514';
    END IF;
END;
$$;
CREATE FUNCTION hope_guard_startup_financial_transaction()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE authority RECORD;
BEGIN
    PERFORM hope_require_startup_financial_control();
    SELECT * INTO authority FROM paper_startup_executions WHERE authorization_sha256=NEW.authorization_sha256;
    IF authority IS NULL OR authority.database_principal IS DISTINCT FROM current_user
       OR clock_timestamp()>=to_timestamp(authority.expires_at)
       OR EXISTS(SELECT 1 FROM paper_startup_completions WHERE authorization_sha256=NEW.authorization_sha256)
       OR NEW.transaction_id IS DISTINCT FROM pg_current_xact_id() THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_TRANSACTION_UNVERIFIED' USING ERRCODE='23514';
    END IF;
    NEW.database_principal:=current_user;NEW.recorded_at:=clock_timestamp();RETURN NEW;
END;
$$;
CREATE TRIGGER trg_startup_financial_transaction_insert BEFORE INSERT ON paper_startup_financial_transactions
FOR EACH ROW EXECUTE FUNCTION hope_guard_startup_financial_transaction();
CREATE TRIGGER trg_startup_financial_transaction_immutable BEFORE UPDATE OR DELETE ON paper_startup_financial_transactions
FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_startup_financial_transaction_truncate BEFORE TRUNCATE ON paper_startup_financial_transactions
FOR EACH STATEMENT EXECUTE FUNCTION hope_reject_audit_event_mutation();

CREATE FUNCTION hope_require_startup_financial_write()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE authority RECORD; durable_transaction xid8; active_count BIGINT;
BEGIN
    -- Ordinary principals retain their established financial contracts. A login
    -- which has consumed startup authority cannot become an ordinary worker.
    IF NOT EXISTS(SELECT 1 FROM paper_startup_claims WHERE database_principal=current_user) THEN
        IF TG_OP='TRUNCATE' THEN RETURN NULL; END IF;
        IF TG_TABLE_NAME='paper_effects' AND TG_OP<>'DELETE' THEN
            IF EXISTS(SELECT 1 FROM paper_startup_executions WHERE job_run_id=NEW.job_run_id) THEN
                RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_OWNER_UNVERIFIED' USING ERRCODE='23514';
            END IF;
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;
    IF TG_OP='TRUNCATE' THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_TRUNCATE_FORBIDDEN' USING ERRCODE='23514';
    END IF;
    PERFORM hope_require_startup_financial_control();
    SELECT count(*) INTO active_count FROM paper_startup_executions e WHERE e.database_principal=current_user AND NOT EXISTS(SELECT 1 FROM paper_startup_completions c WHERE c.authorization_sha256=e.authorization_sha256);
    SELECT e.* INTO authority FROM paper_startup_executions e
      WHERE e.database_principal=current_user AND NOT EXISTS(
          SELECT 1 FROM paper_startup_completions c WHERE c.authorization_sha256=e.authorization_sha256);
    IF authority IS NULL OR active_count<>1
       OR clock_timestamp()>=to_timestamp(authority.expires_at) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_AUTHORITY_UNVERIFIED' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='paper_effects' THEN
        IF TG_OP='DELETE' OR NEW.job_run_id IS DISTINCT FROM authority.job_run_id THEN
            RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_JOB_UNVERIFIED' USING ERRCODE='23514';
        END IF;
    END IF;
    INSERT INTO paper_startup_financial_transactions(authorization_sha256,transaction_id)
      VALUES(authority.authorization_sha256,pg_current_xact_id()) ON CONFLICT DO NOTHING;
    SELECT transaction_id INTO durable_transaction FROM paper_startup_financial_transactions
      WHERE authorization_sha256=authority.authorization_sha256;
    IF durable_transaction IS DISTINCT FROM pg_current_xact_id() THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_TRANSACTION_REPLAY' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;

CREATE FUNCTION hope_bind_startup_financial_witness()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF (NEW.financial_transaction_id IS NOT NULL AND NEW.financial_transaction_id<>pg_current_xact_id())
       OR NOT EXISTS(SELECT 1 FROM paper_startup_financial_transactions t
          WHERE t.authorization_sha256=NEW.authorization_sha256 AND t.transaction_id=pg_current_xact_id()
            AND t.database_principal=current_user) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_WITNESS_TRANSACTION_UNVERIFIED' USING ERRCODE='23514';
    END IF;
    NEW.financial_transaction_id:=pg_current_xact_id();RETURN NEW;
END;
$$;
CREATE TRIGGER trg_startup_financial_witness_transaction BEFORE INSERT ON paper_startup_commit_witnesses
FOR EACH ROW EXECUTE FUNCTION hope_bind_startup_financial_witness();

CREATE FUNCTION hope_verify_startup_financial_transaction()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE authority RECORD; witness RECORD;
BEGIN
    -- SET CONSTRAINTS must not consume the only expiry check early. Reject
    -- immediate constraints, function/procedure commits and prepared commits.
    IF lower(btrim(pg_catalog.current_query())) NOT IN ('commit','commit;') THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_COMMIT_CONTEXT_UNVERIFIED' USING ERRCODE='23514';
    END IF;
    SELECT * INTO authority FROM paper_startup_executions WHERE authorization_sha256=NEW.authorization_sha256;
    SELECT * INTO witness FROM paper_startup_commit_witnesses WHERE authorization_sha256=NEW.authorization_sha256;
    IF authority IS NULL OR witness IS NULL OR NEW.transaction_id<>pg_current_xact_id()
       OR NEW.database_principal IS DISTINCT FROM current_user
       OR authority.database_principal IS DISTINCT FROM current_user
       OR witness.financial_transaction_id IS DISTINCT FROM NEW.transaction_id
       OR witness.evidence_sha256 IS DISTINCT FROM authority.evidence_sha256
       OR witness.controller_process_sha256 IS DISTINCT FROM authority.controller_process_sha256
       OR clock_timestamp()>=least(to_timestamp(witness.witness_expires_at),to_timestamp(authority.expires_at))
       OR NOT EXISTS(SELECT 1 FROM job_runs j JOIN paper_certified_outcomes o USING(job_run_id)
          WHERE j.job_run_id=authority.job_run_id AND j.job_key=authority.job_key
            AND j.scheduled_for=authority.scheduled_for AND j.status='SUCCEEDED') THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_COMMIT_UNVERIFIED' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER trg_startup_financial_transaction_commit
AFTER INSERT ON paper_startup_financial_transactions DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION hope_verify_startup_financial_transaction();

CREATE FUNCTION hope_verify_startup_financial_row_commit()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE target RECORD; authority RECORD; witness RECORD;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM paper_startup_claims WHERE database_principal=current_user) THEN RETURN NULL; END IF;
    SELECT t.* INTO target FROM paper_startup_financial_transactions t
        WHERE t.database_principal=current_user AND t.transaction_id=pg_current_xact_id();
    SELECT * INTO authority FROM paper_startup_executions WHERE authorization_sha256=target.authorization_sha256;
    SELECT * INTO witness FROM paper_startup_commit_witnesses WHERE authorization_sha256=target.authorization_sha256;
    IF target IS NULL OR authority IS NULL OR witness IS NULL
       OR lower(btrim(pg_catalog.current_query())) NOT IN ('commit','commit;')
       OR witness.financial_transaction_id IS DISTINCT FROM target.transaction_id
       OR clock_timestamp()>=least(to_timestamp(witness.witness_expires_at),to_timestamp(authority.expires_at)) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_COMMIT_UNVERIFIED' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE FUNCTION hope_require_startup_committed_financial_transaction()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM paper_startup_financial_transactions t
        JOIN paper_startup_commit_witnesses w USING(authorization_sha256)
        WHERE t.authorization_sha256=NEW.authorization_sha256
          AND t.transaction_id=w.financial_transaction_id AND t.transaction_id<>pg_current_xact_id()
          AND t.database_principal=current_user) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_COMMITTED_FINANCIAL_TRANSACTION_REQUIRED' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_startup_completion_committed_transaction BEFORE INSERT ON paper_startup_completions
FOR EACH ROW EXECUTE FUNCTION hope_require_startup_committed_financial_transaction();

-- Private dashboard copies never use public startup authority or these hooks.
DO $$
DECLARE target TEXT;
BEGIN
    IF current_schema()='public' THEN
        FOREACH target IN ARRAY ARRAY['signals','orders','fills','paper_effects','paper_portfolios',
            'paper_portfolio_positions','paper_portfolio_fill_applications','paper_portfolio_pnl_events',
            'paper_risk_assessments','paper_order_terminal_events','paper_no_trade_decisions',
            'paper_strategy_authorizations','paper_certified_outcomes'] LOOP
            EXECUTE 'CREATE TRIGGER trg_startup_financial_write BEFORE INSERT OR UPDATE OR DELETE ON public.'||quote_ident(target)||' FOR EACH ROW EXECUTE FUNCTION public.hope_require_startup_financial_write()';
            EXECUTE 'CREATE TRIGGER trg_startup_financial_truncate BEFORE TRUNCATE ON public.'||quote_ident(target)||' FOR EACH STATEMENT EXECUTE FUNCTION public.hope_require_startup_financial_write()';
            EXECUTE 'CREATE CONSTRAINT TRIGGER zzzz_startup_financial_commit AFTER INSERT OR UPDATE OR DELETE ON public.'||quote_ident(target)||' DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.hope_verify_startup_financial_row_commit()';
        END LOOP;
    END IF;
END;
$$;
