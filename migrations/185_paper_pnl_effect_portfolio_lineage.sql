-- Preserve the portfolio PNL lineage contract when legacy accounting evidence
-- exists before its effects. Migration 182 guards PNL-row insertion; this
-- reciprocal guard prevents late FILL/PNL effects from assembling missing or
-- conflicting ownership after the row-side guard has already run.
DO $$
BEGIN
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

CREATE OR REPLACE FUNCTION hope_guard_paper_effect_portfolio_pnl_lineage()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    accounting_event RECORD;
    fill_owner_id UUID;
    fill_owner_status TEXT;
    pnl_owner_id UUID;
BEGIN
    IF NEW.effect_type = 'FILL' THEN
        SELECT event.pnl_event_id
          INTO accounting_event
          FROM paper_portfolio_pnl_events event
         WHERE event.fill_id = NEW.entity_id;

        IF NOT FOUND THEN
            RETURN NEW;
        END IF;

        SELECT effect.job_run_id
          INTO pnl_owner_id
          FROM paper_effects effect
         WHERE effect.effect_type = 'PNL'
           AND effect.entity_id = accounting_event.pnl_event_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_UNTRACKED'
                USING ERRCODE = '23514';
        END IF;

        -- A newly inserted FILL effect must belong to a CLAIMED job, so it
        -- cannot yet authorize reuse by a different PNL owner.
        IF pnl_owner_id <> NEW.job_run_id THEN
            RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_OWNER_CONFLICT'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.effect_type = 'PNL' THEN
        SELECT fill_effect.job_run_id, fill_owner.status
          INTO fill_owner_id, fill_owner_status
          FROM paper_portfolio_pnl_events event
          JOIN paper_effects fill_effect
            ON fill_effect.effect_type = 'FILL'
           AND fill_effect.entity_id = event.fill_id
          JOIN job_runs fill_owner
            ON fill_owner.job_run_id = fill_effect.job_run_id
         WHERE event.pnl_event_id = NEW.entity_id;

        IF NOT FOUND THEN
            RETURN NEW;
        END IF;

        IF NEW.job_run_id <> fill_owner_id
           AND fill_owner_status <> 'SUCCEEDED'
        THEN
            RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_OWNER_CONFLICT'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

-- Run after claimed-job, canonical identity, and reciprocal payload guards.
CREATE TRIGGER trg_zzzzz_paper_effect_portfolio_pnl_lineage
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_effect_portfolio_pnl_lineage();
