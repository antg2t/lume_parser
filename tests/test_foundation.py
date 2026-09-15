from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from lume_ingestion.artifacts import OutputDirectory, read_json, write_json
from lume_ingestion.files import sha256_file, source_file
from lume_ingestion.models import Error, IngestionResult, SourceFile, Warning


def test_sha256_is_content_based(tmp_path: Path) -> None:
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"same")
    second.write_bytes(b"same")

    assert sha256_file(first) == sha256_file(second)


def test_same_filename_with_different_content_uses_different_directories(tmp_path: Path) -> None:
    first_dir = tmp_path / "one"
    second_dir = tmp_path / "two"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "repetido.pdf"
    second = second_dir / "repetido.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    first_output = OutputDirectory(tmp_path / "output", source_file(first))
    second_output = OutputDirectory(tmp_path / "output", source_file(second))

    assert first_output.path != second_output.path


def test_json_serializes_money_as_string_and_dates_as_iso(tmp_path: Path) -> None:
    destination = tmp_path / "values.json"
    write_json(destination, {"amount": Decimal("10.20"), "date": date(2026, 9, 14)})

    loaded = read_json(destination)
    assert loaded == {"amount": "10.20", "date": "2026-09-14"}


def test_required_pydantic_models_serialize() -> None:
    source = SourceFile(
        path="x.pdf",
        name="x.pdf",
        extension=".pdf",
        size_bytes=10,
        sha256="a" * 64,
        short_hash="a" * 12,
        media_type="application/pdf",
    )
    result = IngestionResult(
        success=False,
        source=source,
        warnings=[Warning(code="warning", message="aviso")],
        errors=[Error(code="error", message="erro")],
    )

    assert result.model_dump(mode="json")["source"]["sha256"] == "a" * 64
    assert result.errors[0].code == "error"
