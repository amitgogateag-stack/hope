-- Keep the database identity contract aligned with
-- paper_portfolio_pnl_event_id().  A non-canonical primary key makes a row
-- invisible to repository lookups while the portfolio/fill uniqueness
-- constraint prevents the canonical event from ever being written.
CREATE OR REPLACE FUNCTION hope_uuid_v5(namespace_id UUID, name TEXT)
RETURNS UUID
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    hash_bytes BYTEA;
    hash_hex TEXT;
BEGIN
    hash_bytes := digest(uuid_send(namespace_id) || convert_to(name, 'UTF8'), 'sha1');
    hash_bytes := substring(hash_bytes FROM 1 FOR 16);
    hash_bytes := set_byte(
        hash_bytes,
        6,
        (get_byte(hash_bytes, 6) & 15) | 80
    );
    hash_bytes := set_byte(
        hash_bytes,
        8,
        (get_byte(hash_bytes, 8) & 63) | 128
    );
    hash_hex := encode(hash_bytes, 'hex');

    RETURN (
        substring(hash_hex FROM 1 FOR 8) || '-' ||
        substring(hash_hex FROM 9 FOR 4) || '-' ||
        substring(hash_hex FROM 13 FOR 4) || '-' ||
        substring(hash_hex FROM 17 FOR 4) || '-' ||
        substring(hash_hex FROM 21 FOR 12)
    )::UUID;
END;
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_portfolio_pnl_events
         WHERE pnl_event_id <> hope_uuid_v5(
             '6ba7b811-9dad-11d1-80b4-00c04fd430c8'::UUID,
             'hope:paper:portfolio-pnl:' || portfolio_id || ':' || fill_id
         )
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_IDENTITY_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_portfolio_pnl_identity()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.pnl_event_id IS DISTINCT FROM hope_uuid_v5(
        '6ba7b811-9dad-11d1-80b4-00c04fd430c8'::UUID,
        'hope:paper:portfolio-pnl:' || NEW.portfolio_id || ':' || NEW.fill_id
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_IDENTITY_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

-- Run after the existing field-specific guards so they retain their precise
-- diagnostics while every insert still has to satisfy the identity contract.
CREATE TRIGGER trg_zz_paper_portfolio_pnl_identity
BEFORE INSERT ON paper_portfolio_pnl_events
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_portfolio_pnl_identity();
