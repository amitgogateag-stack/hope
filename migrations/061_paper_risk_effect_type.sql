-- PAPER risk lineage requires RISK to be a first-class durable effect type.
-- Extend the original paper_effects domain without rewriting historical migration 011.
ALTER TABLE paper_effects
    DROP CONSTRAINT IF EXISTS paper_effects_effect_type_check;

ALTER TABLE paper_effects
    ADD CONSTRAINT paper_effects_effect_type_check
    CHECK (effect_type IN ('SIGNAL','RISK','ORDER','FILL','PNL'));
