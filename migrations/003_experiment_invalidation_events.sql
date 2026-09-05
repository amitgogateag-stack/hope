-- HOPE v0.1: append-only experiment invalidation events.
-- Experiments remain immutable; invalidation is recorded separately.

CREATE TABLE IF NOT EXISTS experiment_invalidations (
    experiment_id TEXT PRIMARY KEY REFERENCES experiments(experiment_id),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    invalidated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
