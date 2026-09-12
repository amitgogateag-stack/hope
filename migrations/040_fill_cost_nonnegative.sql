-- PAPER fill costs must respect the same non-negative contract as application fill validation.
ALTER TABLE fills
    ADD CONSTRAINT ck_fills_slippage_nonnegative CHECK (slippage >= 0),
    ADD CONSTRAINT ck_fills_transaction_cost_nonnegative CHECK (transaction_cost >= 0);
