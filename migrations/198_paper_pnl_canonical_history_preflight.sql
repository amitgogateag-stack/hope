-- Migration 197 protects newly inserted canonical position PNL.
-- Preflight immutable historical canonical rows created before that trigger
-- existed. Do not silently certify them merely because later writes are safe.
-- Legacy signals with no canonical recovery fields intentionally remain exempt.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pnl_events event
          JOIN positions position
            ON position.position_id = event.position_id
          JOIN signals signal
            ON signal.signal_id = position.opened_from_signal_id
          LEFT JOIN paper_effects source_effect
            ON source_effect.effect_type = 'SIGNAL'
           AND source_effect.entity_id = signal.signal_id
         WHERE (
             signal.strategy_version IS NOT NULL
             OR signal.signal_type IS NOT NULL
             OR signal.conviction IS NOT NULL
             OR signal.inputs_hash IS NOT NULL
         )
           AND source_effect.effect_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_CANONICAL_SOURCE_SIGNAL_UNTRACKED'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pnl_events event
          JOIN positions position
            ON position.position_id = event.position_id
          JOIN signals signal
            ON signal.signal_id = position.opened_from_signal_id
          JOIN paper_effects source_effect
            ON source_effect.effect_type = 'SIGNAL'
           AND source_effect.entity_id = signal.signal_id
          JOIN job_runs source_job
            ON source_job.job_run_id = source_effect.job_run_id
          JOIN paper_effects pnl_effect
            ON pnl_effect.effect_type = 'PNL'
           AND pnl_effect.entity_id = event.pnl_event_id
         WHERE (
             signal.strategy_version IS NOT NULL
             OR signal.signal_type IS NOT NULL
             OR signal.conviction IS NOT NULL
             OR signal.inputs_hash IS NOT NULL
         )
           AND source_effect.job_run_id <> pnl_effect.job_run_id
           AND source_job.status <> 'SUCCEEDED'
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_CANONICAL_SOURCE_SIGNAL_OWNER_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;
