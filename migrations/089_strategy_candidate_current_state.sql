-- Make append-only candidate history produce a deterministic current state,
-- and protect the small operational/backup candidate pools from over-allocation.
ALTER TABLE strategy_candidate_classifications
    ADD COLUMN classification_sequence BIGSERIAL UNIQUE;

CREATE VIEW current_strategy_candidate_classifications AS
SELECT DISTINCT ON (scc.strategy_version_id)
    scc.classification_id,
    scc.classification_sequence,
    scc.strategy_version_id,
    s.family,
    scc.markets,
    scc.state,
    scc.research_decision_id,
    scc.rationale,
    scc.created_at
FROM strategy_candidate_classifications scc
JOIN strategy_versions sv
  ON sv.strategy_version_id = scc.strategy_version_id
JOIN strategies s
  ON s.strategy_id = sv.strategy_id
ORDER BY scc.strategy_version_id, scc.classification_sequence DESC;

CREATE OR REPLACE FUNCTION guard_strategy_candidate_capacity()
RETURNS trigger AS $$
DECLARE
    occupied INTEGER;
BEGIN
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

CREATE TRIGGER trg_strategy_candidate_capacity
BEFORE INSERT ON strategy_candidate_classifications
FOR EACH ROW EXECUTE FUNCTION guard_strategy_candidate_capacity();
