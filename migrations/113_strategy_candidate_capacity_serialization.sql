-- Serialize all append-only strategy candidate state transitions so concurrent
-- promotions/demotions cannot observe the same stale capacity snapshot.

CREATE OR REPLACE FUNCTION guard_strategy_candidate_capacity()
RETURNS trigger AS $$
DECLARE
    occupied INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtext('hope:strategy-candidate-capacity')::bigint
    );

    IF NEW.state = 'OPERATIONAL_CANDIDATE' THEN
        SELECT count(*) INTO occupied
        FROM current_strategy_candidate_classifications current
        WHERE current.state = 'OPERATIONAL_CANDIDATE'
          AND current.strategy_version_id <> NEW.strategy_version_id;

        IF occupied >= 3 THEN
            RAISE EXCEPTION 'STRATEGY_CANDIDATE_OPERATIONAL_CAPACITY_EXCEEDED'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.state = 'BACKUP_CANDIDATE' THEN
        SELECT count(*) INTO occupied
        FROM current_strategy_candidate_classifications current
        WHERE current.state = 'BACKUP_CANDIDATE'
          AND current.strategy_version_id <> NEW.strategy_version_id;

        IF occupied >= 2 THEN
            RAISE EXCEPTION 'STRATEGY_CANDIDATE_BACKUP_CAPACITY_EXCEEDED'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
