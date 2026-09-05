from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable
from uuid import UUID

from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import Fill


@dataclass(frozen=True)
class CompletedTrade:
    instrument_id: UUID
    entry_fill_id: UUID
    exit_fill_id: UUID
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    commission: Decimal
    net_pnl: Decimal


@dataclass(frozen=True)
class TradeMetrics:
    completed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal
    expectancy: Decimal
    total_commission: Decimal
    total_slippage: Decimal
    turnover: Decimal


def _signed(fill: Fill) -> Decimal:
    return fill.quantity if fill.side is OrderSide.BUY else -fill.quantity


def reconstruct_completed_trades(fills: Iterable[Fill]) -> tuple[CompletedTrade, ...]:
    """Reconstruct average-cost completed trades from an ordered fill stream.

    Lots are signed: positive quantities are long entries and negative quantities
    are short entries. A fill may close part of a position and reverse through
    zero; only the quantity that offsets an opposite-signed lot creates a
    completed trade. The supplied fill order is treated as authoritative.
    """
    ordered = tuple(fills)
    open_lots: dict[UUID, list[tuple[Decimal, Decimal, UUID, Decimal]]] = {}
    completed: list[CompletedTrade] = []
    seen: set[UUID] = set()

    for fill in ordered:
        if fill.fill_id in seen:
            raise ValueError("DUPLICATE_FILL")
        seen.add(fill.fill_id)
        signed_qty = _signed(fill)
        lots = open_lots.setdefault(fill.instrument_id, [])
        remaining = abs(signed_qty)

        while remaining > 0 and lots and (lots[0][0] > 0) != (signed_qty > 0):
            lot_qty, lot_price, entry_id, entry_commission = lots[0]
            lot_abs = abs(lot_qty)
            matched = min(lot_abs, remaining)
            if lot_qty > 0:  # selling a long
                gross = matched * (fill.price - lot_price)
            else:  # buying back a short
                gross = matched * (lot_price - fill.price)
            commission = (entry_commission * (matched / lot_abs)) + (fill.commission * (matched / fill.quantity))
            completed.append(
                CompletedTrade(
                    instrument_id=fill.instrument_id,
                    entry_fill_id=entry_id,
                    exit_fill_id=fill.fill_id,
                    quantity=matched,
                    entry_price=lot_price,
                    exit_price=fill.price,
                    gross_pnl=gross,
                    commission=commission,
                    net_pnl=gross - commission,
                )
            )
            remaining -= matched
            if matched == lot_abs:
                lots.pop(0)
            else:
                residual = lot_qty - (matched if lot_qty > 0 else -matched)
                residual_commission = entry_commission * (abs(residual) / lot_abs)
                lots[0] = (residual, lot_price, entry_id, residual_commission)

        if remaining > 0:
            residual_signed_qty = remaining if signed_qty > 0 else -remaining
            lots.append(
                (residual_signed_qty, fill.price, fill.fill_id,
                 fill.commission * (remaining / fill.quantity))
            )

    return tuple(completed)


def calculate_trade_metrics(fills: Iterable[Fill]) -> TradeMetrics:
    ordered = tuple(fills)
    trades = reconstruct_completed_trades(ordered)
    winners = [trade for trade in trades if trade.net_pnl > 0]
    losers = [trade for trade in trades if trade.net_pnl < 0]
    gross_profit = sum((trade.gross_pnl for trade in winners), Decimal("0"))
    gross_loss = -sum((trade.gross_pnl for trade in losers), Decimal("0"))
    profit_factor = Decimal("0") if gross_loss == 0 else gross_profit / gross_loss
    expectancy = Decimal("0") if not trades else sum((t.net_pnl for t in trades), Decimal("0")) / Decimal(len(trades))
    total_commission = sum((fill.commission for fill in ordered), Decimal("0"))
    total_slippage = sum((fill.slippage for fill in ordered), Decimal("0"))
    turnover = sum((fill.quantity * fill.price for fill in ordered), Decimal("0"))
    count = len(trades)
    return TradeMetrics(
        completed_trades=count,
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=Decimal("0") if count == 0 else Decimal(len(winners)) / Decimal(count),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        total_commission=total_commission,
        total_slippage=total_slippage,
        turnover=turnover,
    )
