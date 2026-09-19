-- Immutable non-executable assessment of provider-neutral intelligence.
CREATE TABLE market_intelligence_assessments (
    assessment_id UUID PRIMARY KEY,
    event_id UUID NOT NULL REFERENCES market_intelligence_events(event_id),
    policy_version TEXT NOT NULL CHECK (
        btrim(policy_version) <> '' AND policy_version = btrim(policy_version)
    ),
    disposition TEXT NOT NULL CHECK (disposition IN (
        'OBSERVE_ONLY',
        'ENTRY_ELIGIBILITY_REVIEW',
        'POSITION_RISK_REVIEW',
        'MARKET_RISK_REVIEW'
    )),
    source_action TEXT NOT NULL CHECK (source_action IN (
        'NO_ACTION','OBSERVE','BLOCK_NEW_ENTRY','REDUCE_RISK_CANDIDATE',
        'EXIT_CANDIDATE','DATA_REVIEW_REQUIRED','MARKET_RISK_HALT_CANDIDATE'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(event_id, policy_version)
);

CREATE OR REPLACE FUNCTION guard_market_intelligence_assessment_insert()
RETURNS trigger AS $$
DECLARE
    expected_action TEXT;
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

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_market_intelligence_assessment_insert_guard
BEFORE INSERT ON market_intelligence_assessments
FOR EACH ROW EXECUTE FUNCTION guard_market_intelligence_assessment_insert();

CREATE OR REPLACE FUNCTION prevent_market_intelligence_assessment_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'MARKET_INTELLIGENCE_ASSESSMENT_IMMUTABLE'
        USING ERRCODE='23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_market_intelligence_assessment_immutable
BEFORE UPDATE OR DELETE ON market_intelligence_assessments
FOR EACH ROW EXECUTE FUNCTION prevent_market_intelligence_assessment_mutation();
