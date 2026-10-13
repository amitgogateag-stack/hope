-- A canonical PAPER fill owned by a different job can only reuse its
-- source ORDER effect after that source job has succeeded. Otherwise direct
-- durable writes can create execution truth that repository recovery rejects.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM fills fill
          JOIN orders source_order ON source_order.order_id = fill.order_id
          LEFT JOIN paper_effects source_effect
            ON source_effect.effect_type = 'ORDER'
           AND source_effect.entity_id = source_order.order_id
          LEFT JOIN paper_effects fill_effect
            ON fill_effect.effect_type = 'FILL'
           AND fill_effect.entity_id = fill.fill_id
          LEFT JOIN job_runs source_owner
            ON source_owner.job_run_id = source_effect.job_run_id
         WHERE source_order.environment = 'PAPER'
           AND source_order.signal_type IS NOT NULL
           AND (
               source_effect.effect_id IS NULL
               OR fill_effect.effect_id IS NULL
               OR (
                   source_effect.job_run_id <> fill_effect.job_run_id
                   AND source_owner.status IS DISTINCT FROM 'SUCCEEDED'
               )
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_FILL_SOURCE_ORDER_LINEAGE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_fill_order_effect_lineage()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_order RECORD;
    source_effect_owner UUID;
    fill_effect_owner UUID;
    source_owner_status TEXT;
BEGIN
    SELECT signal_type, environment
      INTO source_order
      FROM orders
     WHERE order_id = NEW.order_id;

    IF NOT FOUND
       OR source_order.environment <> 'PAPER'
       OR source_order.signal_type IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT job_run_id
      INTO source_effect_owner
      FROM paper_effects
     WHERE effect_type = 'ORDER'
       AND entity_id = NEW.order_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_FILL_SOURCE_ORDER_LINEAGE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;

    SELECT job_run_id
      INTO fill_effect_owner
      FROM paper_effects
     WHERE effect_type = 'FILL'
       AND entity_id = NEW.fill_id;
    -- Existing fill/effect completeness guards report the specific missing
    -- FILL-effect error when transaction ordering has not supplied it.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF source_effect_owner <> fill_effect_owner THEN
        SELECT status
          INTO source_owner_status
          FROM job_runs
         WHERE job_run_id = source_effect_owner
         FOR UPDATE;
        IF NOT FOUND OR source_owner_status <> 'SUCCEEDED' THEN
            RAISE EXCEPTION 'PAPER_FILL_SOURCE_ORDER_LINEAGE_CONFLICT'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzz_paper_fill_order_effect_lineage
AFTER INSERT ON fills
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_fill_order_effect_lineage();
