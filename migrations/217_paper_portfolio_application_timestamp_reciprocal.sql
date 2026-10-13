-- Application timestamp authentication must be independent of whether the
-- immutable FILL effect is written before or after the portfolio application.
-- Migration 153 validates effect-first writes; add the reciprocal effect guard
-- and a deferred application check for concurrent visibility at commit.
CREATE OR REPLACE FUNCTION
hope_require_paper_portfolio_application_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_effects
         WHERE effect_type = 'FILL'
           AND entity_id = NEW.fill_id
    ) AND NEW.applied_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION
            'PAPER_PORTFOLIO_APPLICATION_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER
trg_paper_portfolio_application_timestamp_deferred
AFTER INSERT ON paper_portfolio_fill_applications
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_portfolio_application_timestamp();

CREATE OR REPLACE FUNCTION
hope_guard_paper_fill_effect_application_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.effect_type <> 'FILL' THEN
        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM paper_portfolio_fill_applications application
         WHERE application.fill_id = NEW.entity_id
           AND application.applied_at IS DISTINCT FROM
               transaction_timestamp()
    ) THEN
        RAISE EXCEPTION
            'PAPER_PORTFOLIO_APPLICATION_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzzzz_paper_effect_application_timestamp
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_fill_effect_application_timestamp();
