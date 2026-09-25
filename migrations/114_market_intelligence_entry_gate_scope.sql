-- Keep the derived entry-block view aligned with entry-gate semantics.
-- Position-risk reviews concern existing exposure and must not block unrelated new-entry
-- eligibility. Only entry-eligibility and market-risk reviews participate in this gate.
CREATE OR REPLACE VIEW current_market_intelligence_entry_blocks AS
SELECT
    mia.assessment_id,
    mia.event_id,
    mia.policy_version,
    mia.disposition,
    mia.source_action,
    mie.scope,
    mie.instrument_id,
    mie.available_time,
    mir.outcome AS review_outcome
FROM market_intelligence_assessments mia
JOIN market_intelligence_events mie
  ON mie.event_id = mia.event_id
LEFT JOIN market_intelligence_review_resolutions mir
  ON mir.assessment_id = mia.assessment_id
 AND mir.policy_version = mia.policy_version
WHERE mia.disposition IN ('ENTRY_ELIGIBILITY_REVIEW','MARKET_RISK_REVIEW')
  AND (
      mir.resolution_id IS NULL
      OR mir.outcome = 'BLOCK_CONFIRMED'
  );
