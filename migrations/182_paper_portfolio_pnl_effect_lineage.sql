-- A lineage-backed portfolio P&L row must be paired with the canonical PNL
-- effect before it becomes durable.  Otherwise recovery can only discover the
-- incomplete accounting write after the fact.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_effects
         WHERE effect_type = 'PNL'
           AND effect_id <> hope_uuid_v5(
               '6ba7b811-9dad-11d1-80b4-00c04fd430c8'::UUID,
               'hope:paper:effect:PNL:' || entity_id
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_EFFECT_IDENTITY_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM paper_portfolio_pnl_events event
          JOIN paper_effects fill_effect
            ON fill_effect.effect_type = 'FILL'
           AND fill_effect.entity_id = event.fill_id
          LEFT JOIN paper_effects pnl_effect
            ON pnl_effect.effect_type = 'PNL'
           AND pnl_effect.entity_id = event.pnl_event_id
         WHERE pnl_effect.effect_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_UNTRACKED'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM paper_portfolio_pnl_events event
          JOIN paper_effects fill_effect
            ON fill_effect.effect_type = 'FILL'
           AND fill_effect.entity_id = event.fill_id
          JOIN job_runs fill_owner
            ON fill_owner.job_run_id = fill_effect.job_run_id
          JOIN paper_effects pnl_effect
            ON pnl_effect.effect_type = 'PNL'
           AND pnl_effect.entity_id = event.pnl_event_id
         WHERE pnl_effect.job_run_id <> fill_effect.job_run_id
           AND fill_owner.status <> 'SUCCEEDED'
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_OWNER_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_pnl_effect_identity()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.effect_type = 'PNL'
       AND NEW.effect_id IS DISTINCT FROM hope_uuid_v5(
           '6ba7b811-9dad-11d1-80b4-00c04fd430c8'::UUID,
           'hope:paper:effect:PNL:' || NEW.entity_id
       )
    THEN
        RAISE EXCEPTION 'PAPER_PNL_EFFECT_IDENTITY_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_effect_pnl_identity
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_pnl_effect_identity();

CREATE OR REPLACE FUNCTION hope_guard_paper_portfolio_pnl_effect_lineage()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    fill_owner_id UUID;
    fill_owner_status TEXT;
    pnl_owner_id UUID;
    pnl_owner_status TEXT;
BEGIN
    SELECT effect.job_run_id, owner.status
      INTO fill_owner_id, fill_owner_status
      FROM paper_effects effect
      JOIN job_runs owner ON owner.job_run_id = effect.job_run_id
     WHERE effect.effect_type = 'FILL'
       AND effect.entity_id = NEW.fill_id;

    -- Historical corruption probes without effect lineage remain insertable so
    -- repository recovery can continue proving that they fail closed.  Once a
    -- durable FILL effect exists, the production lineage contract is mandatory.
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    SELECT effect.job_run_id, owner.status
      INTO pnl_owner_id, pnl_owner_status
      FROM paper_effects effect
      JOIN job_runs owner ON owner.job_run_id = effect.job_run_id
     WHERE effect.effect_type = 'PNL'
       AND effect.entity_id = NEW.pnl_event_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_UNTRACKED'
            USING ERRCODE = '23514';
    END IF;

    IF pnl_owner_status <> 'CLAIMED' THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_OWNER_NOT_CLAIMED'
            USING ERRCODE = '23514';
    END IF;

    IF pnl_owner_id <> fill_owner_id AND fill_owner_status <> 'SUCCEEDED' THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_OWNER_CONFLICT'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzz_paper_portfolio_pnl_effect_lineage
BEFORE INSERT ON paper_portfolio_pnl_events
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_portfolio_pnl_effect_lineage();
