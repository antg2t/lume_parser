from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from lume_ingestion.parsers.pdf_recover import (
    ocr_image,
    page_needs_recovery,
    tesseract_available,
    valid_character_ratio,
)
from lume_ingestion.pipeline import run_pipeline


FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
PRIVATE = Path(__file__).resolve().parent / "fixtures" / "private"
ITAU_FEB = PRIVATE / "itau-2026-02.pdf"
BRADESCO_FEB = PRIVATE / "bradesco-2026-02.pdf"
ITAU_AUG = PRIVATE / "itau-2026-08-native.pdf"
BRADESCO_AUG = PRIVATE / "bradesco-2026-08-native.pdf"
CAIXA_FEB = PRIVATE / "caixa-2026-02.pdf"


def _minimal_text_pdf() -> bytes:
    return b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 55 >> stream
BT /F1 12 Tf 72 720 Td (Hello native text page here) Tj ET
endstream
endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
trailer << /Root 1 0 R >>
%%EOF
"""


def _image_pdf(lines: list[str], tmp_path: Path) -> Path:
    image = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 28) if FONT.exists() else ImageFont.load_default()
    top = 80
    for line in lines:
        draw.text((40, top), line, fill=(0, 0, 0), font=font)
        top += 44
    pdf_path = tmp_path / "scan.pdf"
    image.save(pdf_path, "PDF", resolution=150.0)
    return pdf_path


def test_valid_character_ratio_treats_cid_tokens_as_invalid() -> None:
    assert valid_character_ratio("(cid:42)(cid:21)(cid:34)") == 0.0
    assert valid_character_ratio("LANCAMENTOS DO PERIODO SALDO ANTERIOR") >= 0.85


def test_page_needs_recovery_on_cid_words() -> None:
    page = {
        "text": {"basic": "****", "layout": "(cid:42)(cid:21) LANCAMENTOS"},
        "words": [{"text": "(cid:42)(cid:21)", "x0": 10, "x1": 80, "top": 40, "bottom": 50}],
        "metrics": {"has_useful_text": True},
    }
    assert page_needs_recovery(page) is True


def test_page_needs_recovery_on_empty_image_page() -> None:
    page = {
        "text": {"basic": "", "layout": ""},
        "words": [],
        "metrics": {"has_useful_text": False},
    }
    assert page_needs_recovery(page) is True


def test_page_needs_recovery_skips_native_layout() -> None:
    page = {
        "text": {
            "basic": "Lançamentos do período SALDO ANTERIOR CNPJ/CPF Valor Saldo Itaú",
            "layout": "Lançamentos do período SALDO ANTERIOR CNPJ/CPF Valor Saldo Itaú",
        },
        "words": [
            {"text": "Lançamentos", "x0": 10, "x1": 80, "top": 40, "bottom": 50},
            {"text": "do", "x0": 82, "x1": 95, "top": 40, "bottom": 50},
            {"text": "período", "x0": 97, "x1": 140, "top": 40, "bottom": 50},
        ],
        "metrics": {"has_useful_text": True},
    }
    assert page_needs_recovery(page) is False


@pytest.mark.skipif(not tesseract_available() or not FONT.exists(), reason="tesseract/fonte ausentes")
def test_ocr_image_reads_bank_labels() -> None:
    image = Image.new("RGB", (900, 240), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 32)
    draw.text((20, 80), "SALDO ANTERIOR  ITAU  LANCAMENTOS", fill=(0, 0, 0), font=font)
    _words, layout = ocr_image(image, page_width=595.0, page_height=160.0)
    folded = "".join(character for character in layout.upper() if character.isalnum())
    assert "SALDOANTERIOR" in folded


def test_image_pdf_without_bank_labels_is_not_a_statement(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr("lume_ingestion.parsers.pdf.extract_bank_statement_with_xai", lambda *_args, **_kwargs: None)
    pdf_path = _image_pdf(["Nota fiscal de servico nacional", "Tomador e prestador", "ISS 2,00"], tmp_path)
    result = run_pipeline(pdf_path, tmp_path / "output")
    assert result.document_type != "bank_statement"


def test_native_text_pdf_does_not_mark_ocr_applied(tmp_path: Path) -> None:
    source = tmp_path / "native.pdf"
    source.write_bytes(_minimal_text_pdf())
    result = run_pipeline(source, tmp_path / "output")
    assert all(warning.code != "ocr_applied" for warning in result.warnings)


@pytest.mark.integration
@pytest.mark.skipif(not ITAU_FEB.exists(), reason="fixture Itaú ausente")
def test_vanguarda_february_itau_pdfcreator(tmp_path: Path) -> None:
    result = run_pipeline(ITAU_FEB, tmp_path / "output")
    assert result.success, result.errors
    assert result.document_type == "bank_statement"
    assert result.data["bank"] == "Itaú"
    assert len(result.data["transactions"]) > 0
    from lume_ingestion.artifacts import read_json

    raw = read_json(result.outputs.raw_json)
    assert raw["document_recognition"]["adapter"] == "itau-digital-bank-statement-v1"
    assert any(warning.code == "cid_decoded" for warning in result.warnings)
    assert all(warning.code != "xai_vision_applied" for warning in result.warnings)


@pytest.mark.integration
@pytest.mark.skipif(not BRADESCO_FEB.exists() or not tesseract_available(), reason="fixture Bradesco ou tesseract ausente")
def test_vanguarda_february_bradesco_pdfcreator(tmp_path: Path) -> None:
    result = run_pipeline(BRADESCO_FEB, tmp_path / "output")
    assert result.success, result.errors
    assert result.document_type == "bank_statement"
    assert result.data["bank"] == "Bradesco"
    assert len(result.data["transactions"]) > 0
    from lume_ingestion.artifacts import read_json

    raw = read_json(result.outputs.raw_json)
    assert raw["document_recognition"]["adapter"] == "bradesco-bank-statement-v1"
    assert any(warning.code == "ocr_applied" for warning in result.warnings)


@pytest.mark.integration
@pytest.mark.skipif(not ITAU_AUG.exists() or not BRADESCO_AUG.exists(), reason="goldens nativos ausentes")
def test_august_native_statements_do_not_regress(tmp_path: Path) -> None:
    itau = run_pipeline(ITAU_AUG, tmp_path / "itau")
    bradesco = run_pipeline(BRADESCO_AUG, tmp_path / "brad")
    assert itau.success and itau.document_type == "bank_statement"
    assert bradesco.success and bradesco.document_type == "bank_statement"
    assert all(warning.code != "ocr_applied" for warning in itau.warnings)
    assert all(warning.code != "ocr_applied" for warning in bradesco.warnings)
    assert len(itau.data["transactions"]) > 0
    assert len(bradesco.data["transactions"]) > 0


@pytest.mark.integration
@pytest.mark.skipif(not CAIXA_FEB.exists(), reason="fixture privada caixa fev ausente")
def test_february_cash_pdf_stays_cash_ledger(tmp_path: Path) -> None:
    result = run_pipeline(CAIXA_FEB, tmp_path / "output")
    assert result.success, result.errors
    assert result.document_type == "cash_ledger"
    assert all(warning.code != "ocr_applied" for warning in result.warnings)
