-- Keep HALTED -> RUNNING safety checks bounded as job history grows.
-- The partial index covers only incomplete autonomous operational PAPER claims.
CREATE INDEX idx_job_runs_claimed_operational_paper
    ON job_runs(job_key)
    WHERE status = 'CLAIMED'
      AND left(job_key, 6) = 'paper:';
