-- New PAPER fills must carry explicit cost-model provenance.
-- Historical fills remain untouched; migration 042 intentionally preserved unknown provenance as NULL.
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

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_fill_cost_model_required
BEFORE INSERT ON fills
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_fill_cost_model_version();
