from __future__ import annotations
from sqlalchemy import Column,Connection,DateTime,MetaData,Numeric,String,Table,Uuid,select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from hope.application.paper.effects import PaperEffect,PaperEffectType
from hope.application.paper.terminals import PaperTerminalOutcome,paper_terminal_payload_hash
from hope.domain.execution.models import Environment,ExecutionCancellation,ExecutionRejection
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
class SqlAlchemyPaperTerminalRepository:
    def __init__(self,connection:Connection)->None:
        self._connection=connection; m=MetaData()
        self._events=Table("paper_order_terminal_events",m,Column("order_id",Uuid,primary_key=True),Column("outcome",String),Column("reason_code",String),Column("event_time",DateTime(timezone=True)),Column("cancelled_quantity",Numeric),Column("created_at",DateTime(timezone=True)))
        self._orders=Table("orders",m,Column("order_id",Uuid,primary_key=True),Column("signal_id",Uuid),Column("instrument_id",Uuid),Column("environment",String))
        self._effects=SqlAlchemyPaperEffectRepository(connection)
    def persist(self,effect:PaperEffect,outcome:PaperTerminalOutcome)->bool:
        kind=PaperEffectType.CANCELLATION if isinstance(outcome,ExecutionCancellation) else PaperEffectType.REJECTION
        if effect.effect_type is not kind or effect.entity_id!=outcome.order_id: raise ValueError("PAPER_TERMINAL_EFFECT_MISMATCH")
        if effect.payload_hash!=paper_terminal_payload_hash(outcome): raise ValueError("PAPER_TERMINAL_EFFECT_PAYLOAD_MISMATCH")
        with self._connection.begin_nested():
            existing=self.get(outcome.order_id); recorded=self._effects.record(effect)
            if not recorded:
                if existing is None or paper_terminal_payload_hash(existing)!=effect.payload_hash: raise RuntimeError("PAPER_TERMINAL_EFFECT_WITHOUT_MATCHING_EVENT")
                return False
            t=outcome.cancellation_time if isinstance(outcome,ExecutionCancellation) else outcome.rejection_time
            q=outcome.cancelled_quantity if isinstance(outcome,ExecutionCancellation) else None
            inserted=self._connection.execute(pg_insert(self._events).values(order_id=outcome.order_id,outcome="CANCELLED" if isinstance(outcome,ExecutionCancellation) else "REJECTED",reason_code=outcome.reason_code,event_time=t,cancelled_quantity=q).on_conflict_do_nothing(index_elements=["order_id"]).returning(self._events.c.order_id)).scalar_one_or_none()
            if inserted is None: raise ValueError("PAPER_ORDER_TERMINAL_CONFLICT")
            return True
    def get(self,order_id):
        row=self._connection.execute(select(self._events,self._orders.c.signal_id,self._orders.c.instrument_id,self._orders.c.environment).join(self._orders,self._events.c.order_id==self._orders.c.order_id).where(self._events.c.order_id==order_id)).mappings().one_or_none()
        if row is None:return None
        if row["environment"]!=Environment.PAPER.value: raise RuntimeError("PAPER_TERMINAL_NON_PAPER_ORDER")
        if row["outcome"]=="CANCELLED": return ExecutionCancellation(row["order_id"],row["signal_id"],row["instrument_id"],Environment.PAPER,row["reason_code"],row["event_time"],row["cancelled_quantity"])
        if row["outcome"]=="REJECTED": return ExecutionRejection(row["order_id"],row["signal_id"],row["instrument_id"],Environment.PAPER,row["reason_code"],row["event_time"])
        raise RuntimeError("PAPER_TERMINAL_OUTCOME_INVALID")
