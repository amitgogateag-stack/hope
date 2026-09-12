-- Preserve the cost-model version needed to reconstruct PAPER fill economics.
-- Existing rows remain NULL rather than inventing historical provenance.
ALTER TABLE fills
    ADD COLUMN cost_model_version TEXT;

ALTER TABLE fills
    ADD CONSTRAINT ck_fills_cost_model_version_nonblank
    CHECK (cost_model_version IS NULL OR btrim(cost_model_version) <> '');
