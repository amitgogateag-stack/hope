-- Bind every intelligence assessment disposition to its event-derived action.
-- A mismatched OBSERVE_ONLY disposition must never suppress a required PAPER entry block.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM market_intelligence_assessments
        WHERE disposition IS DISTINCT FROM CASE source_action
            WHEN 'NO_ACTION' THEN 'OBSERVE_ONLY'
            WHEN 'OBSERVE' THEN 'OBSERVE_ONLY'
            WHEN 'BLOCK_NEW_ENTRY' THEN 'ENTRY_ELIGIBILITY_REVIEW'
            WHEN 'DATA_REVIEW_REQUIRED' THEN 'ENTRY_ELIGIBILITY_REVIEW'
            WHEN 'REDUCE_RISK_CANDIDATE' THEN 'POSITION_RISK_REVIEW'
            WHEN 'EXIT_CANDIDATE' THEN 'POSITION_RISK_REVIEW'
            WHEN 'MARKET_RISK_HALT_CANDIDATE' THEN 'MARKET_RISK_REVIEW'
        END
    ) THEN
        RAISE EXCEPTION 'INTELLIGENCE_ASSESSMENT_DISPOSITION_MISMATCH'
            USING ERRCODE='23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_market_intelligence_assessment_insert()
RETURNS trigger AS $$
DECLARE
    expected_action TEXT;
    expected_disposition TEXT;
BEGIN
    SELECT recommended_action
    INTO expected_action
    FROM market_intelligence_events
    WHERE event_id = NEW.event_id;

    IF expected_action IS NULL THEN
        RAISE EXCEPTION 'INTELLIGENCE_ASSESSMENT_EVENT_REQUIRED'
            USING ERRCODE='23514';
    END IF;

    IF NEW.source_action IS DISTINCT FROM expected_action THEN
        RAISE EXCEPTION 'INTELLIGENCE_ASSESSMENT_SOURCE_ACTION_MISMATCH'
            USING ERRCODE='23514';
    END IF;

    expected_disposition := CASE expected_action
        WHEN 'NO_ACTION' THEN 'OBSERVE_ONLY'
        WHEN 'OBSERVE' THEN 'OBSERVE_ONLY'
        WHEN 'BLOCK_NEW_ENTRY' THEN 'ENTRY_ELIGIBILITY_REVIEW'
        WHEN 'DATA_REVIEW_REQUIRED' THEN 'ENTRY_ELIGIBILITY_REVIEW'
        WHEN 'REDUCE_RISK_CANDIDATE' THEN 'POSITION_RISK_REVIEW'
        WHEN 'EXIT_CANDIDATE' THEN 'POSITION_RISK_REVIEW'
        WHEN 'MARKET_RISK_HALT_CANDIDATE' THEN 'MARKET_RISK_REVIEW'
    END;

    IF NEW.disposition IS DISTINCT FROM expected_disposition THEN
        RAISE EXCEPTION 'INTELLIGENCE_ASSESSMENT_DISPOSITION_MISMATCH'
            USING ERRCODE='23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
