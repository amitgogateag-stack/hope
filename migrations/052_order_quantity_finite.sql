-- Order quantity is a finite execution-domain value in every supported environment.
-- PostgreSQL NUMERIC accepts NaN/Infinity, so positivity alone is insufficient.
ALTER TABLE orders
    ADD CONSTRAINT ck_orders_quantity_finite
    CHECK (quantity::text NOT IN ('NaN', 'Infinity', '-Infinity'));
