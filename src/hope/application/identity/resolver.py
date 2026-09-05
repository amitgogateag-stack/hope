from dataclasses import dataclass
from collections import defaultdict

from hope.domain.identity.models import IdentityMapping, IdentityStatus


@dataclass(frozen=True)
class Resolution:
    mapping: IdentityMapping
    evaluable: bool


class IdentityResolver:
    """Resolve source symbols while preventing duplicate tradable identities."""

    def resolve_batch(self, mappings: list[IdentityMapping]) -> list[Resolution]:
        by_broker: dict[str, list[IdentityMapping]] = defaultdict(list)
        for mapping in mappings:
            by_broker[mapping.broker_instrument_id].append(mapping)

        results: list[Resolution] = []
        for group in by_broker.values():
            active = [m for m in group if m.status is IdentityStatus.ACTIVE]
            if len(active) <= 1:
                results.extend(Resolution(m, self._is_evaluable(m)) for m in group)
                continue

            # Never let two source symbols independently evaluate against one broker identity.
            canonical = min(active, key=lambda m: m.source_symbol)
            for mapping in group:
                if mapping is canonical:
                    results.append(Resolution(mapping, True))
                else:
                    results.append(
                        Resolution(
                            mapping.model_copy(update={
                                "canonical_instrument_id": None,
                                "status": IdentityStatus.TERMINAL,
                                "reason": "DATA_UNAVAILABLE_DUPLICATE_OF_CURRENT_MEMBER",
                            }),
                            False,
                        )
                    )
        return results

    @staticmethod
    def _is_evaluable(mapping: IdentityMapping) -> bool:
        return mapping.status is IdentityStatus.ACTIVE and mapping.canonical_instrument_id is not None
