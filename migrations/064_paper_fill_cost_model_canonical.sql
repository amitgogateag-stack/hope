-- PAPER fill provenance must have one canonical representation for hashing and storage.
CREATE OR REPLACE FUNCTION hope_require_paper_fill_cost_model_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    order_environment TEXT;
BEGIN
    SELECT environment INTO order_environment
    FROM orders
    WHERE order_id = NEW.order_id;

    IF order_environment = 'PAPER'
       AND (NEW.cost_model_version IS NULL OR btrim(NEW.cost_model_version) = '') THEN
        RAISE EXCEPTION 'PAPER_FILL_COST_MODEL_VERSION_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    IF order_environment = 'PAPER'
       AND NEW.cost_model_version IS DISTINCT FROM btrim(NEW.cost_model_version) THEN
        RAISE EXCEPTION 'PAPER_FILL_COST_MODEL_VERSION_NOT_CANONICAL'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;
