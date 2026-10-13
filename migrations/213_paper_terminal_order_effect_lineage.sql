-- A terminal outcome owned by a different job can only reuse an ORDER effect
-- after the source job has succeeded. Otherwise durable cancellation/rejection
-- truth would be unrecoverable even though its payload and risk are valid.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_order_terminal_events terminal
          JOIN orders source_order ON source_order.order_id = terminal.order_id
          LEFT JOIN paper_effects source_effect
            ON source_effect.effect_type = 'ORDER'
           AND source_effect.entity_id = source_order.order_id
          LEFT JOIN paper_effects terminal_effect
            ON terminal_effect.entity_id = terminal.order_id
           AND terminal_effect.effect_type = CASE terminal.outcome
               WHEN 'CANCELLED' THEN 'CANCELLATION'
               ELSE 'REJECTION'
           END
          LEFT JOIN job_runs source_owner
            ON source_owner.job_run_id = source_effect.job_run_id
         WHERE source_order.environment = 'PAPER'
           AND source_order.signal_type IS NOT NULL
           AND (
               source_effect.effect_id IS NULL
               OR terminal_effect.effect_id IS NULL
               OR (
                   source_effect.job_run_id <> terminal_effect.job_run_id
                   AND source_owner.status <> 'SUCCEEDED'
               )
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_SOURCE_ORDER_LINEAGE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_terminal_order_effect_lineage()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_order RECORD;
    source_effect_owner UUID;
    terminal_effect_owner UUID;
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
        RAISE EXCEPTION 'PAPER_TERMINAL_SOURCE_ORDER_LINEAGE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;

    SELECT job_run_id
      INTO terminal_effect_owner
      FROM paper_effects
     WHERE entity_id = NEW.order_id
       AND effect_type = CASE NEW.outcome
           WHEN 'CANCELLED' THEN 'CANCELLATION'
           ELSE 'REJECTION'
       END;
    -- Migration 205 reports the more specific missing-terminal-effect error.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF source_effect_owner <> terminal_effect_owner THEN
        SELECT status
          INTO source_owner_status
          FROM job_runs
         WHERE job_run_id = source_effect_owner
         FOR UPDATE;
        IF NOT FOUND OR source_owner_status <> 'SUCCEEDED' THEN
            RAISE EXCEPTION 'PAPER_TERMINAL_SOURCE_ORDER_LINEAGE_CONFLICT'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzz_paper_terminal_order_effect_lineage
AFTER INSERT ON paper_order_terminal_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_terminal_order_effect_lineage();
