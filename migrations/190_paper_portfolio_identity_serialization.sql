-- A missing portfolio row cannot be protected by SELECT ... FOR UPDATE.
-- Serialize first creation with authoritative recovery by locking a stable
-- transaction-scoped identity derived from the requested portfolio UUID.
CREATE OR REPLACE FUNCTION hope_lock_paper_portfolio_identity_on_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'hope:paper:portfolio:' || NEW.portfolio_id::text,
            0
        )
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_aaa_paper_portfolio_identity_lock
BEFORE INSERT ON paper_portfolios
FOR EACH ROW
EXECUTE FUNCTION hope_lock_paper_portfolio_identity_on_insert();
