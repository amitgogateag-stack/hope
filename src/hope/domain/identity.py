from dataclasses import dataclass

from .enums import IdentityStatus


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    canonical_symbol: str
    exchange: str


@dataclass(frozen=True, slots=True)
class InstrumentAlias:
    alias: str
    instrument_id: str
    status: IdentityStatus


@dataclass(frozen=True, slots=True)
class IdentityMapping:
    alias: str
    instrument_id: str
    broker_instrument: str
    status: IdentityStatus


class IdentityResolutionError(ValueError):
    """Raised when identity resolution would make evaluation unsafe."""


def resolve_evaluable_mappings(mappings: list[IdentityMapping]) -> tuple[IdentityMapping, ...]:
    """Return active mappings only and enforce one-to-one broker identity."""
    evaluable = tuple(m for m in mappings if m.status is IdentityStatus.ACTIVE)
    by_broker: dict[str, IdentityMapping] = {}
    for mapping in evaluable:
        previous = by_broker.get(mapping.broker_instrument)
        if previous is not None:
            raise IdentityResolutionError(
                "multiple active mappings resolve to broker instrument "
                f"{mapping.broker_instrument!r}"
            )
        by_broker[mapping.broker_instrument] = mapping
    return evaluable
