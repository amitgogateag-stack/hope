-- Acquire the parent portfolio lock before every other P&L INSERT trigger.
-- PostgreSQL orders same-kind triggers alphabetically; the original trigger
-- name allowed commission, timestamp, and instrument validation to read durable
-- execution/accounting evidence before entering recovery's critical section.
DROP TRIGGER trg_paper_portfolio_pnl_parent_lock
ON paper_portfolio_pnl_events;

CREATE TRIGGER trg_aaa_paper_portfolio_pnl_parent_lock
BEFORE INSERT ON paper_portfolio_pnl_events
FOR EACH ROW
EXECUTE FUNCTION hope_lock_paper_portfolio_for_pnl_insert();
