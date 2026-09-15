from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from lume_ingestion.models import ArtifactPaths, SourceFile


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Valor nao serializavel: {type(value).__name__}")


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        value = json.load(stream, parse_float=Decimal)
    if not isinstance(value, dict):
        raise ValueError("O JSON deve conter um objeto na raiz.")
    return value


def write_json(path: str | Path, value: Any) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)
        stream.write("\n")
    temporary.replace(destination)
    return destination


class OutputDirectory:
    def __init__(self, root: str | Path, source: SourceFile) -> None:
        self.root = Path(root).resolve()
        self.source = source
        self.path = self._collision_safe_path()
        self.path.mkdir(parents=True, exist_ok=True)

    def _collision_safe_path(self) -> Path:
        for length in range(12, 65, 4):
            candidate = self.root / self.source.sha256[:length]
            if not candidate.exists():
                self.source.short_hash = self.source.sha256[:length]
                return candidate
            known_hash = self._known_hash(candidate)
            if known_hash in (None, self.source.sha256):
                self.source.short_hash = self.source.sha256[:length]
                return candidate
        raise RuntimeError("Nao foi possivel criar um diretorio de saida sem colisao de hash.")

    @staticmethod
    def _known_hash(directory: Path) -> str | None:
        for filename in ("raw.json", "result.json"):
            artifact = directory / filename
            if artifact.is_file():
                try:
                    return str(read_json(artifact).get("source", {}).get("sha256"))
                except (OSError, ValueError, TypeError):
                    return "invalid"
        # Um diretorio preexistente sem manifesto pode ser resto de uma execucao
        # interrompida. Nao o reutilizamos, pois seu conteudo nao e atribuivel.
        return "unknown"

    @property
    def artifacts(self) -> ArtifactPaths:
        pages = sorted(str(path) for path in self.path.glob("page-*.txt"))
        return ArtifactPaths(
            raw_json=str(self.path / "raw.json") if (self.path / "raw.json").exists() else None,
            normalized_json=str(self.path / "normalized.json") if (self.path / "normalized.json").exists() else None,
            result_json=str(self.path / "result.json") if (self.path / "result.json").exists() else None,
            page_texts=pages,
        )

    def write_page_texts(self, pages: list[dict[str, Any]]) -> list[str]:
        paths: list[str] = []
        for page in pages:
            page_number = int(page["page_number"])
            destination = self.path / f"page-{page_number:04d}.txt"
            text = str(page.get("text", {}).get("basic") or page.get("text", {}).get("layout") or "")
            destination.write_text(text + ("\n" if text else ""), encoding="utf-8", newline="\n")
            paths.append(str(destination))
        return paths
