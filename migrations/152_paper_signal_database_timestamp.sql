-- The signals table is shared across research and execution.  A durable SIGNAL
-- effect identifies a PAPER signal before its row is inserted, allowing
-- PostgreSQL to authenticate PAPER persistence time without changing non-PAPER
-- signal behavior or the logical decision_time.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM signals signal
          JOIN paper_effects effect
            ON effect.effect_type = 'SIGNAL'
           AND effect.entity_id = signal.signal_id
         WHERE signal.created_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER_SIGNAL_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_authenticate_paper_signal_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM paper_effects
         WHERE effect_type = 'SIGNAL'
           AND entity_id = NEW.signal_id
    ) THEN
        RETURN NEW;
    END IF;

    IF NEW.created_at > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_SIGNAL_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_SIGNAL_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_signal_database_timestamp
BEFORE INSERT ON signals
FOR EACH ROW
EXECUTE FUNCTION hope_authenticate_paper_signal_timestamp();
