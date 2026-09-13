-- Persisted PAPER risk decisions are execution audit evidence and must never be rewritten.

CREATE OR REPLACE FUNCTION hope_reject_paper_risk_assessment_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'PAPER_RISK_ASSESSMENT_IMMUTABLE: risk decision history cannot be modified or deleted'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_risk_assessments_immutable ON paper_risk_assessments;
CREATE TRIGGER trg_paper_risk_assessments_immutable
BEFORE UPDATE OR DELETE ON paper_risk_assessments
FOR EACH ROW EXECUTE FUNCTION hope_reject_paper_risk_assessment_mutation();
