-- Durable PAPER side-effect lineage is idempotency evidence and must never be rewritten.

CREATE OR REPLACE FUNCTION hope_reject_paper_effect_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'PAPER_EFFECT_IMMUTABLE: paper effect history cannot be modified or deleted'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_effects_immutable ON paper_effects;
CREATE TRIGGER trg_paper_effects_immutable
BEFORE UPDATE OR DELETE ON paper_effects
FOR EACH ROW EXECUTE FUNCTION hope_reject_paper_effect_mutation();
