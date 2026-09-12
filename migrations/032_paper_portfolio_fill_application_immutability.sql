-- Applied-fill lineage is restart/replay evidence and must remain append-only.

CREATE OR REPLACE FUNCTION hope_reject_paper_portfolio_fill_application_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'PAPER_PORTFOLIO_FILL_APPLICATION_IMMUTABLE: fill application history cannot be modified or deleted'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_portfolio_fill_applications_immutable ON paper_portfolio_fill_applications;
CREATE TRIGGER trg_paper_portfolio_fill_applications_immutable
BEFORE UPDATE OR DELETE ON paper_portfolio_fill_applications
FOR EACH ROW EXECUTE FUNCTION hope_reject_paper_portfolio_fill_application_mutation();
