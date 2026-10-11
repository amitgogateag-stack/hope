-- Authenticate new canonical PAPER position-PNL at the commit boundary.
-- Historical positions whose signals lack canonical recovery material remain
-- eligible for legacy accounting compatibility; do not rewrite old evidence.
-- For new canonical signals, position PNL must be backed by a reusable SIGNAL
-- effect owned either by the PNL run or by an already successful source run.
CREATE OR REPLACE FUNCTION hope_require_canonical_paper_pnl_signal_lineage()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_signal_id UUID;
    canonical_material_present BOOLEAN;
    source_owner UUID;
    source_status TEXT;
    pnl_owner UUID;
BEGIN
    SELECT signal.signal_id,
           (
               signal.strategy_version IS NOT NULL
               OR signal.signal_type IS NOT NULL
               OR signal.conviction IS NOT NULL
               OR signal.inputs_hash IS NOT NULL
           )
      INTO source_signal_id, canonical_material_present
      FROM positions position
      JOIN signals signal
        ON signal.signal_id = position.opened_from_signal_id
     WHERE position.position_id = NEW.position_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_PNL_POSITION_SIGNAL_NOT_FOUND'
            USING ERRCODE = '23514';
    END IF;

    IF NOT canonical_material_present THEN
        RETURN NULL;
    END IF;

    SELECT effect.job_run_id, owner.status
      INTO source_owner, source_status
      FROM paper_effects effect
      JOIN job_runs owner ON owner.job_run_id = effect.job_run_id
     WHERE effect.effect_type = 'SIGNAL'
       AND effect.entity_id = source_signal_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_PNL_CANONICAL_SOURCE_SIGNAL_UNTRACKED'
            USING ERRCODE = '23514';
    END IF;

    SELECT job_run_id INTO pnl_owner
      FROM paper_effects
     WHERE effect_type = 'PNL'
       AND entity_id = NEW.pnl_event_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_PNL_EFFECT_MISSING'
            USING ERRCODE = '23514';
    END IF;

    IF source_owner <> pnl_owner AND source_status <> 'SUCCEEDED' THEN
        RAISE EXCEPTION 'PAPER_PNL_CANONICAL_SOURCE_SIGNAL_OWNER_CONFLICT'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_pnl_canonical_signal_lineage
AFTER INSERT ON pnl_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION hope_require_canonical_paper_pnl_signal_lineage();
