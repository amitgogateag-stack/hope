-- Enforce canonical universe-version metadata at the PostgreSQL boundary.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM universe_versions
        WHERE version = '' OR version <> btrim(version)
    ) THEN
        RAISE EXCEPTION 'UNIVERSE_VERSION_NOT_CANONICAL: existing universe version metadata is invalid'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE universe_versions
    ADD CONSTRAINT ck_universe_versions_version_canonical
    CHECK (version <> '' AND version = btrim(version));
