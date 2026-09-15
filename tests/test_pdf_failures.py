from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

from lume_ingestion.pipeline import run_pipeline


def test_truncated_pdf_returns_actionable_error(tmp_path: Path) -> None:
    truncated = tmp_path / "truncated.pdf"
    truncated.write_bytes(b"%PDF-1.7\n1 0 obj\n")

    result = run_pipeline(truncated, tmp_path / "output")

    assert result.success is False
    assert result.errors[0].code == "truncated_pdf"
    assert result.outputs.result_json is not None


def test_encrypted_pdf_returns_actionable_error(tmp_path: Path) -> None:
    encrypted = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    with encrypted.open("wb") as stream:
        writer.write(stream)

    result = run_pipeline(encrypted, tmp_path / "output")

    assert result.success is False
    assert result.errors[0].code == "encrypted_pdf"


def test_unknown_content_returns_structured_error(tmp_path: Path) -> None:
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"not really a pdf")

    result = run_pipeline(fake, tmp_path / "output")

    assert result.success is False
    assert result.errors[0].code == "unknown_format"
    assert result.warnings[0].code == "extension_content_mismatch"
