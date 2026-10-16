-- Control locks alone do not refresh old repeatable/serializable snapshots.
-- Require statement snapshots at authorization/write boundaries, including
-- direct SQL. Keep all existing claim and append-history guards intact.
CREATE FUNCTION hope_require_fresh_paper_control_snapshot()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_REQUIRES_READ_COMMITTED'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE FUNCTION hope_guard_paper_control_snapshot()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM hope_require_fresh_paper_control_snapshot();
    RETURN NEW;
END;
$$;

CREATE FUNCTION hope_guard_paper_claim_snapshot()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF left(NEW.job_key, 6) = 'paper:' THEN
        PERFORM hope_require_fresh_paper_control_snapshot();
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_control_snapshot_isolation
BEFORE INSERT ON paper_environment_control_events
FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_control_snapshot();

CREATE TRIGGER trg_paper_claim_snapshot_isolation
BEFORE INSERT ON job_runs
FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_claim_snapshot();
