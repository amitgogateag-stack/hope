-- Enforce the same temporal validity contract at the PostgreSQL boundary as UniverseMember.
ALTER TABLE universe_members
    ADD CONSTRAINT ck_universe_members_valid_interval
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from);
