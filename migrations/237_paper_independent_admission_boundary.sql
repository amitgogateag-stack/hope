-- Fail closed until a separate trusted admission verifier supplies exact rows.
-- This migration does not create an issuer, login, grant or production approval.
CREATE TABLE paper_admission_principals (
    principal_oid OID PRIMARY KEY,
    principal_name TEXT NOT NULL UNIQUE,
    admission_mode TEXT NOT NULL CHECK (admission_mode IN ('CONTROLLED','ORDINARY')),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paper_admission_approvals (
    target_table TEXT NOT NULL CHECK (target_table IN ('paper_handoff_intents','paper_handoff_outcomes',
        'paper_startup_claims','paper_startup_bootstraps','paper_startup_executions',
        'paper_startup_commit_witnesses','paper_startup_completions')),
    authorization_sha256 TEXT NOT NULL CHECK (authorization_sha256 ~ '^[0-9a-f]{64}$'),
    principal_oid OID NOT NULL REFERENCES paper_admission_principals(principal_oid),
    approved_row JSONB NOT NULL CHECK (jsonb_typeof(approved_row)='object' AND octet_length(approved_row::text)<=16384),
    valid_until BIGINT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(target_table,authorization_sha256)
);

CREATE FUNCTION hope_guard_admission_provisioning()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE owner_name TEXT; principal RECORD; mode_now TEXT;
BEGIN
    SELECT pg_get_userbyid(c.relowner) INTO owner_name FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=TG_TABLE_SCHEMA AND c.relname=TG_TABLE_NAME;
    IF current_user IS DISTINCT FROM owner_name OR session_user IS DISTINCT FROM owner_name THEN
        RAISE EXCEPTION 'PAPER_ADMISSION_TRUSTED_WRITER_REQUIRED' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='paper_admission_principals' THEN
        SELECT * INTO principal FROM pg_roles WHERE oid=NEW.principal_oid AND rolname=NEW.principal_name;
        IF principal IS NULL OR NOT principal.rolcanlogin OR principal.rolsuper OR principal.rolcreatedb
           OR principal.rolcreaterole OR principal.rolreplication OR principal.rolbypassrls
           OR pg_has_role(principal.oid,(SELECT c.relowner FROM pg_class c WHERE c.oid=TG_RELID),'MEMBER') THEN
            RAISE EXCEPTION 'PAPER_ADMISSION_PRINCIPAL_UNSAFE' USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT admission_mode INTO mode_now FROM paper_admission_principals WHERE principal_oid=NEW.principal_oid;
        IF mode_now IS DISTINCT FROM 'CONTROLLED' OR NEW.valid_until<=extract(epoch FROM clock_timestamp())
           OR NEW.valid_until>extract(epoch FROM clock_timestamp())+60
           OR NEW.approved_row->>'authorization_sha256' IS DISTINCT FROM NEW.authorization_sha256
           OR NEW.approved_row->>'database_principal' IS DISTINCT FROM
                (SELECT principal_name FROM paper_admission_principals WHERE principal_oid=NEW.principal_oid) THEN
            RAISE EXCEPTION 'PAPER_ADMISSION_APPROVAL_UNVERIFIED' USING ERRCODE='23514';
        END IF;
    END IF;
    NEW.recorded_at:=clock_timestamp();RETURN NEW;
END;
$$;
CREATE TRIGGER trg_admission_principal_insert BEFORE INSERT ON paper_admission_principals
FOR EACH ROW EXECUTE FUNCTION hope_guard_admission_provisioning();
CREATE TRIGGER trg_admission_approval_insert BEFORE INSERT ON paper_admission_approvals
FOR EACH ROW EXECUTE FUNCTION hope_guard_admission_provisioning();
CREATE TRIGGER trg_admission_principal_immutable BEFORE UPDATE OR DELETE ON paper_admission_principals
FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_admission_principal_truncate BEFORE TRUNCATE ON paper_admission_principals
FOR EACH STATEMENT EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_admission_approval_immutable BEFORE UPDATE OR DELETE ON paper_admission_approvals
FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
CREATE TRIGGER trg_admission_approval_truncate BEFORE TRUNCATE ON paper_admission_approvals
FOR EACH STATEMENT EXECUTE FUNCTION hope_reject_audit_event_mutation();

-- Read-only metadata is not a bearer capability; INSERT remains owner-only even
-- if an operator mistakenly grants the worker INSERT ON ALL TABLES.
REVOKE ALL ON paper_admission_principals,paper_admission_approvals FROM PUBLIC;
GRANT SELECT ON paper_admission_principals,paper_admission_approvals TO PUBLIC;

CREATE FUNCTION hope_admission_mode()
RETURNS TEXT LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE principal RECORD; owner_name TEXT; role_oid OID;
BEGIN
    SELECT pg_get_userbyid(c.relowner) INTO owner_name FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='paper_admission_principals';
    -- The migration owner remains the explicit trusted data/DDL administrator.
    -- Runtime preflight already forbids it as an operational worker.
    IF current_user=owner_name AND session_user=owner_name THEN RETURN 'ADMINISTRATOR'; END IF;
    IF current_user IS DISTINCT FROM session_user THEN
        RAISE EXCEPTION 'PAPER_ADMISSION_PRINCIPAL_SUBSTITUTION' USING ERRCODE='23514';
    END IF;
    SELECT oid INTO role_oid FROM pg_roles WHERE rolname=session_user;
    SELECT * INTO principal FROM paper_admission_principals
      WHERE principal_oid=role_oid AND principal_name=session_user;
    IF principal IS NULL THEN
        RAISE EXCEPTION 'PAPER_ADMISSION_PRINCIPAL_UNCLASSIFIED' USING ERRCODE='23514';
    END IF;
    RETURN principal.admission_mode;
END;
$$;

CREATE FUNCTION hope_require_independent_admission()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE mode_now TEXT; role_oid OID; approved RECORD;
BEGIN
    mode_now:=hope_admission_mode();
    IF mode_now='ADMINISTRATOR' AND TG_TABLE_NAME IN ('paper_handoff_intents','paper_handoff_outcomes') THEN
        RETURN NEW;
    END IF;
    IF mode_now IS DISTINCT FROM 'CONTROLLED' THEN
        RAISE EXCEPTION 'PAPER_ADMISSION_CONTROLLED_PRINCIPAL_REQUIRED' USING ERRCODE='23514';
    END IF;
    SELECT oid INTO role_oid FROM pg_roles WHERE rolname=session_user;
    SELECT * INTO approved FROM paper_admission_approvals
      WHERE target_table=TG_TABLE_NAME AND authorization_sha256=NEW.authorization_sha256 AND principal_oid=role_oid;
    IF approved IS NULL OR approved.approved_row IS DISTINCT FROM (to_jsonb(NEW)-'recorded_at')
       OR clock_timestamp()>=to_timestamp(approved.valid_until) THEN
        RAISE EXCEPTION 'PAPER_ADMISSION_INDEPENDENT_APPROVAL_REQUIRED' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION hope_require_classified_financial_principal()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE mode_now TEXT;
BEGIN
    mode_now:=hope_admission_mode();
    IF mode_now='CONTROLLED' AND NOT EXISTS(SELECT 1 FROM paper_startup_claims WHERE database_principal=session_user) THEN
        RAISE EXCEPTION 'PAPER_ADMISSION_STARTUP_NOT_CONSUMED' USING ERRCODE='23514';
    END IF;
    IF mode_now='ORDINARY' AND EXISTS(SELECT 1 FROM paper_startup_claims WHERE database_principal=session_user) THEN
        RAISE EXCEPTION 'PAPER_ADMISSION_PRINCIPAL_MODE_CONFLICT' USING ERRCODE='23514';
    END IF;
    IF TG_OP='TRUNCATE' THEN RETURN NULL;
    ELSIF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;
CREATE FUNCTION hope_verify_independent_admission_commit()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE deadline BIGINT;
BEGIN
    IF hope_admission_mode()='ADMINISTRATOR' AND TG_TABLE_NAME IN ('paper_handoff_intents','paper_handoff_outcomes') THEN RETURN NULL; END IF;
    SELECT a.valid_until INTO deadline FROM paper_admission_approvals a JOIN pg_roles r ON r.oid=a.principal_oid
      WHERE a.target_table=TG_TABLE_NAME AND a.authorization_sha256=NEW.authorization_sha256 AND r.rolname=session_user;
    IF deadline IS NULL OR clock_timestamp()>=to_timestamp(deadline) THEN
        RAISE EXCEPTION 'PAPER_ADMISSION_COMMIT_APPROVAL_EXPIRED' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
DO $$
DECLARE target TEXT;
BEGIN
    IF current_schema()='public' THEN
        FOREACH target IN ARRAY ARRAY['paper_handoff_intents','paper_handoff_outcomes','paper_startup_claims',
            'paper_startup_bootstraps','paper_startup_executions','paper_startup_commit_witnesses','paper_startup_completions'] LOOP
            EXECUTE 'CREATE TRIGGER aaa_independent_admission BEFORE INSERT ON public.'||quote_ident(target)||
                ' FOR EACH ROW EXECUTE FUNCTION public.hope_require_independent_admission()';
            EXECUTE 'CREATE CONSTRAINT TRIGGER zzzz_independent_admission_commit AFTER INSERT ON public.'||quote_ident(target)||
                ' DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.hope_verify_independent_admission_commit()';
        END LOOP;
        FOREACH target IN ARRAY ARRAY['signals','orders','fills','paper_effects','paper_portfolios',
            'paper_portfolio_positions','paper_portfolio_fill_applications','paper_portfolio_pnl_events',
            'paper_risk_assessments','paper_order_terminal_events','paper_no_trade_decisions',
            'paper_strategy_authorizations','paper_certified_outcomes'] LOOP
            EXECUTE 'CREATE TRIGGER aaa_classified_financial_principal BEFORE INSERT OR UPDATE OR DELETE ON public.'||quote_ident(target)||
                ' FOR EACH ROW EXECUTE FUNCTION public.hope_require_classified_financial_principal()';
            EXECUTE 'CREATE TRIGGER aaa_classified_financial_truncate BEFORE TRUNCATE ON public.'||quote_ident(target)||
                ' FOR EACH STATEMENT EXECUTE FUNCTION public.hope_require_classified_financial_principal()';
        END LOOP;
    END IF;
END;
$$;
