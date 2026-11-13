-- R1/A1: controlled PAPER workers must not inherit PostgreSQL predefined
-- capabilities which are invisible to ordinary relation/schema ACL checks.
-- Append-only replacement; migration 236 bytes remain unchanged.
CREATE OR REPLACE FUNCTION hope_require_startup_financial_role()
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user IS DISTINCT FROM session_user OR EXISTS(
        SELECT 1 FROM pg_roles r
        WHERE (r.rolname=current_user OR pg_has_role(current_user,r.oid,'MEMBER'))
          AND (r.rolname LIKE 'pg\_%' ESCAPE '\'
            OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
            OR r.rolreplication OR r.rolbypassrls
            OR has_database_privilege(r.oid,current_database(),'TEMPORARY')
            OR has_database_privilege(r.oid,current_database(),'CREATE')
            OR EXISTS(SELECT 1 FROM pg_namespace n
                      WHERE has_schema_privilege(r.oid,n.oid,'CREATE')))
    ) THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_ROLE_UNSAFE'
            USING ERRCODE='23514';
    END IF;
END;
$$;
