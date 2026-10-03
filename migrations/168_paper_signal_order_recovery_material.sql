-- Persist the canonical PAPER signal/order material required to authenticate
-- restart recovery without regenerating or inventing execution inputs.
--
-- These columns are intentionally nullable for pre-existing historical rows.
-- New PAPER writers populate them. Recovery must fail closed when a historical
-- row lacks enough canonical material to reproduce its immutable effect hash.
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS strategy_version TEXT,
    ADD COLUMN IF NOT EXISTS signal_type TEXT,
    ADD COLUMN IF NOT EXISTS conviction NUMERIC,
    ADD COLUMN IF NOT EXISTS inputs_hash CHAR(64);

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS signal_type TEXT;

ALTER TABLE signals
    DROP CONSTRAINT IF EXISTS ck_signals_recovery_signal_type,
    ADD CONSTRAINT ck_signals_recovery_signal_type
        CHECK (signal_type IS NULL OR signal_type IN ('ENTRY','EXIT')),
    DROP CONSTRAINT IF EXISTS ck_signals_recovery_conviction,
    ADD CONSTRAINT ck_signals_recovery_conviction
        CHECK (conviction IS NULL OR (conviction >= 0 AND conviction <= 1)),
    DROP CONSTRAINT IF EXISTS ck_signals_recovery_inputs_hash,
    ADD CONSTRAINT ck_signals_recovery_inputs_hash
        CHECK (inputs_hash IS NULL OR inputs_hash ~ '^[0-9a-f]{64}$'),
    DROP CONSTRAINT IF EXISTS ck_signals_recovery_strategy_version,
    ADD CONSTRAINT ck_signals_recovery_strategy_version
        CHECK (
            strategy_version IS NULL
            OR (
                length(strategy_version) > 0
                AND strategy_version = btrim(strategy_version)
            )
        );

ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS ck_orders_recovery_signal_type,
    ADD CONSTRAINT ck_orders_recovery_signal_type
        CHECK (signal_type IS NULL OR signal_type IN ('ENTRY','EXIT'));
