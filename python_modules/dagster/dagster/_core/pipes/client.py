from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, Optional

from dagster_pipes import (
    PipesExtras,
    PipesParams,
    PipesProcessContextData,
)

from dagster._core.execution.context.compute import OpExecutionContext

if TYPE_CHECKING:
    from .context import ExtMessageHandler, ExtResult


class ExtClient(ABC):
    @abstractmethod
    def run(
        self,
        *,
        context: OpExecutionContext,
        extras: Optional[PipesExtras] = None,
    ) -> Iterator["ExtResult"]: ...


class ExtContextInjector(ABC):
    @abstractmethod
    @contextmanager
    def inject_context(self, context_data: "PipesProcessContextData") -> Iterator[PipesParams]: ...


class ExtMessageReader(ABC):
    @abstractmethod
    @contextmanager
    def read_messages(self, handler: "ExtMessageHandler") -> Iterator[PipesParams]: ...
