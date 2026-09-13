-- Durable PAPER risk-decision lineage and idempotency.
ALTER TABLE paper_effects
    DROP CONSTRAINT paper_effects_effect_type_check;

ALTER TABLE paper_effects
    ADD CONSTRAINT paper_effects_effect_type_check
    CHECK (effect_type IN ('SIGNAL','RISK','ORDER','FILL','PNL'));

CREATE TABLE paper_risk_assessments (
    signal_id UUID PRIMARY KEY REFERENCES signals(signal_id),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVE','REJECT')),
    reason_code TEXT NOT NULL CHECK (btrim(reason_code) <> ''),
    approved_quantity NUMERIC NOT NULL CHECK (
        approved_quantity >= 0
        AND approved_quantity NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_paper_risk_decision_quantity CHECK (
        (decision = 'REJECT' AND approved_quantity = 0)
        OR (decision = 'APPROVE' AND approved_quantity > 0)
    )
);
