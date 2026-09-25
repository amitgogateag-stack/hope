-- An intelligence assessment must not exist before HOPE actually ingested its source event.
-- This preserves point-in-time truth at the storage boundary and prevents future-ingested
-- intelligence from acquiring an earlier assessment timestamp through direct SQL or backfill.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM market_intelligence_assessments mia
        JOIN market_intelligence_events mie
          ON mie.event_id = mia.event_id
        WHERE mia.created_at < mie.ingestion_time
    ) THEN
        RAISE EXCEPTION 'INTELLIGENCE_ASSESSMENT_PRECEDES_EVENT_INGESTION'
            USING ERRCODE='23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_market_intelligence_assessment_insert()
RETURNS trigger AS $$
DECLARE
    expected_action TEXT;
    event_ingestion_time TIMESTAMPTZ;
BEGIN
    SELECT recommended_action, ingestion_time
    INTO expected_action, event_ingestion_time
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

    IF NEW.created_at < event_ingestion_time THEN
        RAISE EXCEPTION 'INTELLIGENCE_ASSESSMENT_PRECEDES_EVENT_INGESTION'
            USING ERRCODE='23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
