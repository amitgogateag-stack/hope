-- Authoritative PAPER accounting events derived from portfolio fill transitions.
CREATE TABLE paper_portfolio_pnl_events (
    pnl_event_id UUID PRIMARY KEY,
    portfolio_id UUID NOT NULL REFERENCES paper_portfolios(portfolio_id) ON DELETE CASCADE,
    fill_id UUID NOT NULL REFERENCES fills(fill_id),
    instrument_id UUID NOT NULL REFERENCES instruments(instrument_id),
    realized_pnl_delta NUMERIC NOT NULL,
    commission_delta NUMERIC NOT NULL CHECK (commission_delta >= 0),
    event_time TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_paper_portfolio_pnl_fill UNIQUE (portfolio_id, fill_id)
);
