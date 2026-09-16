from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from lume_ingestion.parsers.pdf_ai import extract_bank_statement_with_xai, statement_from_ai
from lume_ingestion.pipeline import run_pipeline


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


def test_statement_from_ai_maps_signed_amounts() -> None:
    statement = statement_from_ai(
        {
            "bank": "Itaú",
            "layout": "digital-v1",
            "period_start": "2026-02-01",
            "period_end": "2026-02-28",
            "initial_balance": "-3781.80",
            "final_balance": "100.00",
            "transactions": [
                {
                    "date": "2026-02-02",
                    "description": "BOLETO PAGO",
                    "amount": "-161.09",
                    "page_number": 1,
                },
                {
                    "date": "2026-02-02",
                    "description": "PIX RECEBIDO",
                    "amount": "50.000,00",
                    "page_number": 1,
                },
            ],
            "daily_balances": [{"date": "2026-02-02", "balance": "46057.11"}],
        }
    )
    assert statement.bank == "Itaú"
    assert statement.transactions[0].transaction_type == "debit"
    assert statement.transactions[0].amount == Decimal("-161.09")
    assert statement.transactions[1].amount == Decimal("50000.00")
    assert statement.transactions[1].transaction_type == "credit"


def test_extract_uses_injected_post_and_rejects_unknown_bank(monkeypatch) -> None:
    monkeypatch.setattr("lume_ingestion.parsers.pdf_ai.render_pdf_jpegs", lambda *_args, **_kwargs: [b"jpeg"])

    def fake_post(images, **_kwargs):
        assert images
        return json.dumps(
            {
                "bank": "Pine",
                "transactions": [{"date": "2026-02-01", "description": "x", "amount": "10.00", "page_number": 1}],
            }
        )

    result = extract_bank_statement_with_xai(b"%PDF-1.4", api_key="xai-test", post=fake_post)
    assert result is None


def test_extract_accepts_itau_payload(monkeypatch) -> None:
    def fake_render(_content, **_kwargs):
        return [b"fake-jpeg"]

    def fake_post(images, **_kwargs):
        assert images == [b"fake-jpeg"]
        return json.dumps(
            {
                "bank": "Itaú",
                "layout": "digital-v1",
                "initial_balance": "0.00",
                "transactions": [
                    {"date": "2026-02-02", "description": "PIX", "amount": "-10.00", "page_number": 1}
                ],
                "daily_balances": [],
            }
        )

    monkeypatch.setattr("lume_ingestion.parsers.pdf_ai.render_pdf_jpegs", fake_render)
    statement, payload = extract_bank_statement_with_xai(b"%PDF", api_key="xai-test", post=fake_post)
    assert payload["bank"] == "Itaú"
    assert statement.transactions[0].amount == Decimal("-10.00")


def test_native_text_pdf_does_not_call_xai(tmp_path: Path, monkeypatch) -> None:
    called = {"n": 0}

    def boom(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("xAI should not run on textual PDF")

    monkeypatch.setattr("lume_ingestion.parsers.pdf.extract_bank_statement_with_xai", boom)
    source = tmp_path / "native.pdf"
    source.write_bytes(_minimal_text_pdf())
    result = run_pipeline(source, tmp_path / "output")
    assert called["n"] == 0
    assert all(warning.code != "xai_vision_applied" for warning in result.warnings)


def test_pipeline_uses_ai_statement_when_parser_fails(tmp_path: Path, monkeypatch) -> None:
    payload = {
        "bank": "Bradesco",
        "layout": "monthly-v1",
        "initial_balance": "1000.00",
        "final_balance": "1100.00",
        "transactions": [
            {
                "date": "2026-02-02",
                "description": "TED RECEBIDA",
                "amount": "100.00",
                "page_number": 1,
            }
        ],
        "daily_balances": [{"date": "2026-02-02", "balance": "1100.00"}],
    }

    from lume_ingestion.parsers.pdf_ai import statement_from_ai

    statement = statement_from_ai(payload)

    def fake_extract(_content):
        return statement, payload

    monkeypatch.setattr("lume_ingestion.parsers.pdf.extract_bank_statement_with_xai", fake_extract)
    source = tmp_path / "scan.pdf"
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with source.open("wb") as handle:
        writer.write(handle)
    result = run_pipeline(source, tmp_path / "output")
    assert result.document_type == "bank_statement"
    assert result.data["bank"] == "Bradesco"
    assert len(result.data["transactions"]) == 1
    assert any(warning.code == "xai_vision_applied" for warning in result.warnings)
