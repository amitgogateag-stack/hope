-- Durable PAPER portfolio projection derived only from tracked fills.
CREATE TABLE paper_portfolios (
    portfolio_id UUID PRIMARY KEY,
    initial_cash NUMERIC NOT NULL CHECK (initial_cash >= 0),
    cash NUMERIC NOT NULL,
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE paper_portfolio_positions (
    portfolio_id UUID NOT NULL REFERENCES paper_portfolios(portfolio_id) ON DELETE CASCADE,
    instrument_id UUID NOT NULL REFERENCES instruments(instrument_id),
    quantity NUMERIC NOT NULL,
    average_price NUMERIC NOT NULL,
    realized_pnl NUMERIC NOT NULL DEFAULT 0,
    total_commission NUMERIC NOT NULL DEFAULT 0 CHECK (total_commission >= 0),
    PRIMARY KEY (portfolio_id, instrument_id),
    CHECK (
        (quantity = 0 AND average_price = 0)
        OR (quantity <> 0 AND average_price > 0)
    )
);

CREATE TABLE paper_portfolio_fill_applications (
    portfolio_id UUID NOT NULL REFERENCES paper_portfolios(portfolio_id) ON DELETE CASCADE,
    fill_id UUID NOT NULL REFERENCES fills(fill_id),
    application_sequence BIGINT NOT NULL CHECK (application_sequence > 0),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (portfolio_id, fill_id),
    CONSTRAINT uq_paper_portfolio_application_sequence UNIQUE (portfolio_id, application_sequence)
);
