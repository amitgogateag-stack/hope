-- Immutable explicit resolution for non-executable intelligence assessments.
CREATE TABLE market_intelligence_review_resolutions (
    resolution_id UUID PRIMARY KEY,
    assessment_id UUID NOT NULL
        REFERENCES market_intelligence_assessments(assessment_id),
    policy_version TEXT NOT NULL CHECK (
        btrim(policy_version) <> '' AND policy_version = btrim(policy_version)
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('CLEARED','BLOCK_CONFIRMED','NO_ACTION_REQUIRED')
    ),
    rationale TEXT NOT NULL CHECK (
        btrim(rationale) <> '' AND rationale = btrim(rationale)
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(assessment_id, policy_version)
);

CREATE OR REPLACE FUNCTION guard_market_intelligence_review_resolution_insert()
RETURNS trigger AS $$
DECLARE
    assessment_policy_version TEXT;
BEGIN
    SELECT policy_version
    INTO assessment_policy_version
    FROM market_intelligence_assessments
    WHERE assessment_id = NEW.assessment_id;

    IF assessment_policy_version IS NULL THEN
        RAISE EXCEPTION 'INTELLIGENCE_REVIEW_ASSESSMENT_REQUIRED'
            USING ERRCODE='23514';
    END IF;

    IF NEW.policy_version IS DISTINCT FROM assessment_policy_version THEN
        RAISE EXCEPTION 'INTELLIGENCE_REVIEW_POLICY_VERSION_MISMATCH'
            USING ERRCODE='23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_market_intelligence_review_resolution_insert_guard
BEFORE INSERT ON market_intelligence_review_resolutions
FOR EACH ROW EXECUTE FUNCTION guard_market_intelligence_review_resolution_insert();

CREATE OR REPLACE FUNCTION prevent_market_intelligence_review_resolution_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'MARKET_INTELLIGENCE_REVIEW_RESOLUTION_IMMUTABLE'
        USING ERRCODE='23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_market_intelligence_review_resolution_immutable
BEFORE UPDATE OR DELETE ON market_intelligence_review_resolutions
FOR EACH ROW EXECUTE FUNCTION prevent_market_intelligence_review_resolution_mutation();
