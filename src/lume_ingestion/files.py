from __future__ import annotations

import hashlib
from pathlib import Path

from lume_ingestion.errors import IngestionFailure
from lume_ingestion.models import SourceFile


DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024


def require_regular_file(path: str | Path, max_size: int = DEFAULT_MAX_FILE_SIZE) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists():
        raise IngestionFailure("file_not_found", "O arquivo informado nao existe.", path=str(candidate))
    if not candidate.is_file():
        raise IngestionFailure("not_a_file", "O caminho informado nao e um arquivo regular.", path=str(candidate))
    size = candidate.stat().st_size
    if size == 0:
        raise IngestionFailure("empty_file", "O arquivo esta vazio.", path=str(candidate))
    if size > max_size:
        raise IngestionFailure(
            "file_too_large",
            "O arquivo excede o limite de processamento.",
            size_bytes=size,
            max_size_bytes=max_size,
        )
    return candidate


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def source_file(path: Path, sha256: str | None = None, short_hash_length: int = 12) -> SourceFile:
    digest = sha256 or sha256_file(path)
    return SourceFile(
        path=str(path.resolve()),
        name=path.name,
        extension=path.suffix.lower(),
        size_bytes=path.stat().st_size,
        sha256=digest,
        short_hash=digest[:short_hash_length],
        media_type=None,
    )
