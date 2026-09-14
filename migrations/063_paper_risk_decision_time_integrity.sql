-- A durable PAPER risk decision cannot use information from after its job schedule.
CREATE OR REPLACE FUNCTION hope_enforce_paper_risk_decision_time()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_decision_time TIMESTAMPTZ;
    risk_job_scheduled_for TIMESTAMPTZ;
BEGIN
    SELECT s.decision_time, j.scheduled_for
    INTO source_decision_time, risk_job_scheduled_for
    FROM signals s
    JOIN paper_effects e
      ON e.effect_type = 'RISK'
     AND e.entity_id = s.signal_id
    JOIN job_runs j ON j.job_run_id = e.job_run_id
    WHERE s.signal_id = NEW.signal_id;

    IF source_decision_time > risk_job_scheduled_for THEN
        RAISE EXCEPTION
            'PAPER_RISK_DECISION_AFTER_JOB_SCHEDULE: signal % decision time % exceeds job schedule %',
            NEW.signal_id, source_decision_time, risk_job_scheduled_for
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_risk_decision_time_integrity ON paper_risk_assessments;
CREATE TRIGGER trg_paper_risk_decision_time_integrity
BEFORE INSERT ON paper_risk_assessments
FOR EACH ROW EXECUTE FUNCTION hope_enforce_paper_risk_decision_time();
