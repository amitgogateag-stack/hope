-- A canonical PAPER terminal outcome may consume durable risk authorization
-- owned by another job only after that risk owner has succeeded. Decision and
-- quantity checks alone do not prove that the immutable RISK effect is
-- recoverable during restart.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_order_terminal_events terminal
          JOIN orders source_order ON source_order.order_id = terminal.order_id
          LEFT JOIN paper_effects risk_effect
            ON risk_effect.effect_type = 'RISK'
           AND risk_effect.entity_id = source_order.signal_id
          LEFT JOIN paper_effects terminal_effect
            ON terminal_effect.entity_id = terminal.order_id
           AND terminal_effect.effect_type = CASE terminal.outcome
               WHEN 'CANCELLED' THEN 'CANCELLATION'
               ELSE 'REJECTION'
           END
          LEFT JOIN job_runs risk_owner
            ON risk_owner.job_run_id = risk_effect.job_run_id
         WHERE source_order.environment = 'PAPER'
           AND source_order.signal_type IS NOT NULL
           AND (
               risk_effect.effect_id IS NULL
               OR terminal_effect.effect_id IS NULL
               OR (
                   risk_effect.job_run_id <> terminal_effect.job_run_id
                   AND risk_owner.status IS DISTINCT FROM 'SUCCEEDED'
               )
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_SOURCE_RISK_LINEAGE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_terminal_risk_effect_lineage()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_order RECORD;
    risk_effect_owner UUID;
    terminal_effect_owner UUID;
    risk_owner_status TEXT;
BEGIN
    SELECT signal_id, signal_type, environment
      INTO source_order
      FROM orders
     WHERE order_id = NEW.order_id;

    IF NOT FOUND
       OR source_order.environment <> 'PAPER'
       OR source_order.signal_type IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT job_run_id
      INTO risk_effect_owner
      FROM paper_effects
     WHERE effect_type = 'RISK'
       AND entity_id = source_order.signal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_SOURCE_RISK_LINEAGE_CONFLICT'
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
    -- Existing terminal/effect completeness guards retain their specific
    -- missing-effect diagnostic when transaction ordering has not supplied it.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF risk_effect_owner <> terminal_effect_owner THEN
        SELECT status
          INTO risk_owner_status
          FROM job_runs
         WHERE job_run_id = risk_effect_owner
         FOR UPDATE;
        IF NOT FOUND OR risk_owner_status <> 'SUCCEEDED' THEN
            RAISE EXCEPTION 'PAPER_TERMINAL_SOURCE_RISK_LINEAGE_CONFLICT'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzzz_paper_terminal_risk_effect_lineage
AFTER INSERT ON paper_order_terminal_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_terminal_risk_effect_lineage();
