from abc import ABC, abstractmethod
from pydantic import BaseModel, ConfigDict

class ParameterSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

class Strategy(ABC):
    name: str
    version: str

    @abstractmethod
    def generate_signals(self, market_context, universe, parameters: ParameterSnapshot):
        raise NotImplementedError
