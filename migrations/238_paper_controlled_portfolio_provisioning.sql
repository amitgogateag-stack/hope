-- R1: a startup-admitted worker may execute an approved trading cycle but
-- must never provision portfolio capital in the same transaction.
-- This is an incremental deny-only control; it does not create an issuer,
-- grant funding privileges, or authorize an operational PAPER cycle.
--
-- Ordinary roles retain their previous provisioning behavior for now.
-- A future independent funding authority must be separately designed.
CREATE FUNCTION hope_reject_controlled_portfolio_provisioning()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE mode_now TEXT;
BEGIN
    mode_now := hope_admission_mode();
    IF mode_now='CONTROLLED' THEN
        IF TG_OP='INSERT' THEN
            RAISE EXCEPTION 'PAPER_CONTROLLED_PORTFOLIO_PROVISIONING_FORBIDDEN'
                USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND
           (NEW.initial_cash IS DISTINCT FROM OLD.initial_cash) THEN
            RAISE EXCEPTION 'PAPER_CONTROLLED_INITIAL_CAPITAL_MUTATION_FORBIDDEN'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
DO $$
BEGIN
    IF current_schema()='public' THEN
        CREATE TRIGGER aaaa_reject_controlled_portfolio_provisioning
        BEFORE INSERT OR UPDATE ON public.paper_portfolios
        FOR EACH ROW EXECUTE FUNCTION public.hope_reject_controlled_portfolio_provisioning();
    END IF;
END;
$$;
