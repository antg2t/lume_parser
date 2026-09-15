from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from lume_ingestion.errors import IngestionFailure


class Parser(Protocol):
    name: str
    version: str

    def extract(self, path: Any, source: Any, warnings: Any, limits: Any) -> dict[str, Any]: ...


class ParserRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], Parser]] = {}

    def register(self, source_format: str, factory: Callable[[], Parser]) -> None:
        if source_format in self._factories:
            raise ValueError(f"Parser ja registrado para {source_format!r}")
        self._factories[source_format] = factory

    def get(self, source_format: str | None) -> Parser:
        if source_format is None:
            raise IngestionFailure("unknown_format", "O formato do arquivo nao foi reconhecido pelo conteudo.")
        factory = self._factories.get(source_format)
        if factory is None:
            raise IngestionFailure(
                "unsupported_format",
                "Nao existe parser registrado para o formato detectado.",
                source_format=source_format,
            )
        return factory()

    @property
    def formats(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


registry = ParserRegistry()
