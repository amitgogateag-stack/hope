-- PAPER risk reason codes feed deterministic audit hashes and must be stored canonically.
ALTER TABLE paper_risk_assessments
    ADD CONSTRAINT ck_paper_risk_reason_code_canonical
    CHECK (reason_code = btrim(reason_code));
