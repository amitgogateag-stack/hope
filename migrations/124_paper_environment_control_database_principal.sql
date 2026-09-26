-- Preserve both the logical operator and the authenticated database principal.
-- The database principal is derived from session_user and cannot be forged by
-- supplying an alternate value in direct SQL.
ALTER TABLE paper_environment_control_events
    ADD COLUMN database_principal TEXT NOT NULL DEFAULT session_user;

ALTER TABLE paper_environment_control_events
    ADD CONSTRAINT ck_paper_environment_control_database_principal_canonical
    CHECK (
        length(btrim(database_principal)) > 0
        AND database_principal = btrim(database_principal)
    );

CREATE OR REPLACE FUNCTION guard_paper_environment_control_database_principal()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.database_principal IS DISTINCT FROM session_user THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_DATABASE_PRINCIPAL_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_environment_control_database_principal
BEFORE INSERT ON paper_environment_control_events
FOR EACH ROW
EXECUTE FUNCTION guard_paper_environment_control_database_principal();
