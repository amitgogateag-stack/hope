-- Strategy version and source commit are durable execution provenance identities.
-- Reject alternate textual representations at the storage boundary.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM strategy_versions
        WHERE version = ''
           OR version <> btrim(version)
           OR code_commit = ''
           OR code_commit <> btrim(code_commit)
    ) THEN
        RAISE EXCEPTION 'STRATEGY_VERSION_TEXT_NOT_CANONICAL: existing strategy version provenance is blank or padded'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE strategy_versions
    ADD CONSTRAINT ck_strategy_versions_version_canonical
    CHECK (version <> '' AND version = btrim(version));

ALTER TABLE strategy_versions
    ADD CONSTRAINT ck_strategy_versions_code_commit_canonical
    CHECK (code_commit <> '' AND code_commit = btrim(code_commit));
