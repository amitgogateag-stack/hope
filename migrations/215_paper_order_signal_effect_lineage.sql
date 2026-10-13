-- A canonical PAPER order effect owned by a different job can only reuse
-- its source SIGNAL effect after that source job has succeeded. Preserve
-- staged order rows without effects, but never allow a complete durable pair
-- that normal repository recovery must reject.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM orders source_order
          JOIN paper_effects order_effect
            ON order_effect.effect_type = 'ORDER'
           AND order_effect.entity_id = source_order.order_id
          LEFT JOIN paper_effects signal_effect
            ON signal_effect.effect_type = 'SIGNAL'
           AND signal_effect.entity_id = source_order.signal_id
          LEFT JOIN job_runs signal_owner
            ON signal_owner.job_run_id = signal_effect.job_run_id
         WHERE source_order.environment = 'PAPER'
           AND source_order.signal_type IS NOT NULL
           AND (
               signal_effect.effect_id IS NULL
               OR (
                   signal_effect.job_run_id <> order_effect.job_run_id
                   AND signal_owner.status IS DISTINCT FROM 'SUCCEEDED'
               )
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_ORDER_SOURCE_SIGNAL_LINEAGE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_order_signal_effect_lineage()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    order_effect_owner UUID;
    signal_effect_owner UUID;
    signal_owner_status TEXT;
BEGIN
    IF NEW.environment <> 'PAPER' OR NEW.signal_type IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT job_run_id
      INTO order_effect_owner
      FROM paper_effects
     WHERE effect_type = 'ORDER'
       AND entity_id = NEW.order_id;
    -- Orders without effects remain staged; existing completeness and payload
    -- boundaries govern when they become canonical execution material.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    SELECT job_run_id
      INTO signal_effect_owner
      FROM paper_effects
     WHERE effect_type = 'SIGNAL'
       AND entity_id = NEW.signal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_ORDER_SOURCE_SIGNAL_LINEAGE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;

    IF signal_effect_owner <> order_effect_owner THEN
        SELECT status
          INTO signal_owner_status
          FROM job_runs
         WHERE job_run_id = signal_effect_owner
         FOR UPDATE;
        IF NOT FOUND OR signal_owner_status <> 'SUCCEEDED' THEN
            RAISE EXCEPTION 'PAPER_ORDER_SOURCE_SIGNAL_LINEAGE_CONFLICT'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzz_paper_order_signal_effect_lineage
AFTER INSERT ON orders
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_order_signal_effect_lineage();
