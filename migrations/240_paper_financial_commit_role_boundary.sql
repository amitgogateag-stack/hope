-- Recheck the controlled financial principal at the actual COMMIT boundary.
-- Admission/write-time checks cannot cover privileges granted after sealing.
-- Preserve all existing transaction/witness/expiry/HALT/financial constraints.
CREATE FUNCTION hope_verify_financial_commit_role()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF lower(btrim(pg_catalog.current_query())) NOT IN ('commit','commit;') THEN
        RAISE EXCEPTION 'PAPER_STARTUP_FINANCIAL_COMMIT_CONTEXT_UNVERIFIED'
            USING ERRCODE='23514';
    END IF;
    PERFORM hope_require_startup_financial_role();
    RETURN NULL;
END;
$$;
DO $$
BEGIN
    IF current_schema()='public' THEN
        CREATE CONSTRAINT TRIGGER aa_financial_commit_role
        AFTER INSERT ON public.paper_startup_financial_transactions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.hope_verify_financial_commit_role();
    END IF;
END;
$$;
