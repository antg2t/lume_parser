from __future__ import annotations

from pathlib import Path

import pytest

from lume_ingestion.detection import FileTypeDetector
from lume_ingestion.errors import IngestionFailure


def test_detector_uses_signature_instead_of_extension(tmp_path: Path) -> None:
    disguised = tmp_path / "documento.txt"
    disguised.write_bytes(b"%PDF-1.4\n%%EOF\n")

    result = FileTypeDetector().inspect(disguised)

    assert result.format == "pdf"
    assert result.warnings[0].code == "extension_content_mismatch"


def test_false_pdf_extension_is_not_accepted(tmp_path: Path) -> None:
    false_pdf = tmp_path / "documento.pdf"
    false_pdf.write_text("isto nao e um pdf", encoding="utf-8")

    result = FileTypeDetector().inspect(false_pdf)

    assert result.format is None
    assert result.warnings[0].code == "extension_content_mismatch"


def test_empty_file_has_structured_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pdf"
    empty.touch()

    with pytest.raises(IngestionFailure) as captured:
        FileTypeDetector().inspect(empty)

    assert captured.value.code == "empty_file"


def test_detector_recognizes_semicolon_csv_by_content_with_bom(tmp_path: Path) -> None:
    source = tmp_path / "movimentos.txt"
    source.write_bytes(
        "\ufeffDt movimento;Desc;Vl\n13/02/2026;PIX UNIVERSO;-150,09\n".encode("utf-8")
    )

    result = FileTypeDetector().inspect(source)

    assert result.format == "csv"
    assert result.source.media_type == "text/csv"


@pytest.mark.integration
def test_detector_recognizes_legacy_xls_by_its_compound_file_signature() -> None:
    source = Path(__file__).resolve().parents[1] / "docs" / "Clientes" / "Martine" / "18-08-2026 - Extrato Bradesco.xls"
    if not source.is_file():
        pytest.skip("fixture XLS Bradesco ausente neste checkout")

    result = FileTypeDetector().inspect(source)

    assert result.format == "xls"
    assert result.source.media_type == "application/vnd.ms-excel"
