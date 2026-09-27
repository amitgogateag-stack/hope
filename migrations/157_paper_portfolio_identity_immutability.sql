-- PAPER portfolio and position keys are durable projection identities.  The
-- authoritative repository mutates accounting values in place and never re-keys
-- these rows, so identity changes must fail before they can obscure replay state.
CREATE OR REPLACE FUNCTION hope_freeze_paper_portfolio_identity()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.portfolio_id IS DISTINCT FROM OLD.portfolio_id THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_ID_IMMUTABLE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION hope_freeze_paper_portfolio_position_identity()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.portfolio_id IS DISTINCT FROM OLD.portfolio_id
       OR NEW.instrument_id IS DISTINCT FROM OLD.instrument_id THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_POSITION_IDENTITY_IMMUTABLE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_portfolio_identity_immutable
BEFORE UPDATE OF portfolio_id ON paper_portfolios
FOR EACH ROW
EXECUTE FUNCTION hope_freeze_paper_portfolio_identity();

CREATE TRIGGER trg_paper_portfolio_position_identity_immutable
BEFORE UPDATE OF portfolio_id, instrument_id ON paper_portfolio_positions
FOR EACH ROW
EXECUTE FUNCTION hope_freeze_paper_portfolio_position_identity();
