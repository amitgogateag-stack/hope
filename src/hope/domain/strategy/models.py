from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

from hope.domain.market_data.context import PITMarketContext
from hope.domain.universe.models import UniverseVersion


class ParameterSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Strategy(ABC):
    name: str
    version: str

    @abstractmethod
    def generate_signals(
        self,
        market_context: PITMarketContext,
        universe: UniverseVersion,
        parameters: ParameterSnapshot,
    ):
        raise NotImplementedError
