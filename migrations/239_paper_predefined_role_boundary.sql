-- R1/A1: controlled PAPER workers must not inherit PostgreSQL predefined
-- capabilities which are invisible to ordinary relation/schema ACL checks.
-- Append-only replacement; migration 236 bytes remain unchanged.
CREATE OR REPLACE FUNCTION hope_require_startup_financial_role()
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user IS DISTINCT FROM session_user OR EXISTS(
        SELECT 1 FROM pg_roles r
        WHERE (r.rolname=current_user OR pg_has_role(current_user,r.oid,'MEMBER'))
          AND (left(r.rolname,3)='pg_'
            OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
            OR r.rolreplication OR r.rolbypassrls
            OR has_database_privilege(r.oid,current_database(),'TEMPORARY')
            OR has_database_privilege(r.oid,current_database(),'CREATE')
            OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n
                      ON n.oid=c.relnamespace
                      WHERE n.nspname='public' AND c.relowner=r.oid)
            OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n
                      ON n.oid=p.pronamespace
                      WHERE n.nspname='public' AND p.proowner=r.oid)
            OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n
                      ON n.oid=p.pronamespace
                      WHERE n.nspname='pg_catalog'
                        AND p.oid<>'pg_catalog.pg_control_system()'::regprocedure
                        AND has_function_privilege(r.oid,p.oid,'EXECUTE')
                        AND NOT EXISTS(
                            SELECT 1 FROM aclexplode(COALESCE(
                                p.proacl,acldefault('f',p.proowner))) a
                            WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))
            OR EXISTS(SELECT 1 FROM pg_namespace n
                      WHERE has_schema_privilege(r.oid,n.oid,'CREATE')))
    ) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_ROLE_UNSAFE'
            USING ERRCODE='23514';
    END IF;
END;
$$;

-- Recheck the controlled principal inside PostgreSQL before any one-use
-- startup/execution authority row is consumed.  The Python bootstrap performs
-- the same preflight, but privileges can drift after bootstrap and direct SQL
-- must not bypass the boundary.
CREATE FUNCTION hope_require_controlled_startup_authority_role()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF hope_admission_mode()='CONTROLLED' THEN
        PERFORM hope_require_startup_financial_role();
    END IF;
    RETURN NEW;
END;
$$;
DO $$
DECLARE target TEXT;
BEGIN
    IF current_schema()='public' THEN
        FOREACH target IN ARRAY ARRAY['paper_handoff_intents','paper_handoff_outcomes',
            'paper_startup_claims','paper_startup_bootstraps','paper_startup_executions',
            'paper_startup_commit_witnesses','paper_startup_completions'] LOOP
            EXECUTE 'CREATE TRIGGER aa_startup_authority_role BEFORE INSERT ON public.'||
                quote_ident(target)||' FOR EACH ROW EXECUTE FUNCTION public.'||
                'hope_require_controlled_startup_authority_role()';
        END LOOP;
    END IF;
END;
$$;
