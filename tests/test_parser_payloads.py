from __future__ import annotations

from pathlib import Path

import pytest

from lume_ingestion.bank_statement import normalize_bradesco_bank_statement, normalize_itau_bank_statement
from lume_ingestion.cash_ledger import normalize_cash_ledger_pdf
from lume_ingestion.parsers.pdf_recover import decode_cid_text


def _itau_word(text: str, x0: float, x1: float, top: float) -> dict:
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 8}


def _itau_page(rows: list[list[dict]]) -> dict:
    words = [word for row in rows for word in row]
    return {"page_number": 1, "words": words, "text": {"layout": " ".join(word["text"] for word in words), "basic": ""}}


def test_cid_map_decodes_itau_pdfcreator_header() -> None:
    lancamentos = "".join(f"(cid:{code})" for code in (32, 47, 58, 71, 47, 57, 51, 58, 64, 59, 63))
    assert decode_cid_text(lancamentos) == "Lançamentos"
    assert decode_cid_text("".join(f"(cid:{c})" for c in (42, 21, 34, 27, 41, 21, 38, 24, 21))) == "VANGUARDA"


def test_itau_puts_document_number_on_payload_not_text() -> None:
    page = _itau_page(
        [
            [
                _itau_word("Lançamentos do período: 01/08/2026 até 31/08/2026", 34, 400, 40),
            ],
            [
                _itau_word("Data", 35, 70, 60),
                _itau_word("Lançamentos", 90, 160, 60),
                _itau_word("Razão Social", 225, 300, 60),
                _itau_word("CNPJ/CPF", 361, 420, 60),
                _itau_word("Valor", 470, 500, 60),
                _itau_word("Saldo", 520, 560, 60),
            ],
            [
                _itau_word("Itaú", 34, 60, 70),
                _itau_word("SALDO ANTERIOR", 90, 180, 80),
                _itau_word("01/08/2026", 35, 78, 80),
                _itau_word("100,00", 520, 560, 80),
            ],
            [
                _itau_word("03/08/2026", 35, 78, 110),
                _itau_word("PIX ENVIADO 1044", 88, 200, 110),
                _itau_word("Padaria Sao Jose", 225, 350, 110),
                _itau_word("12.345.678/0001-90", 361, 440, 110),
                _itau_word("-50,00", 470, 508, 110),
                _itau_word("50,00", 520, 560, 110),
            ],
            [
                _itau_word("03/08/2026", 35, 78, 130),
                _itau_word("SALDO TOTAL DISPONIVEL DIA", 88, 210, 130),
                _itau_word("50,00", 520, 560, 130),
            ],
        ]
    )
    statement, _warnings = normalize_itau_bank_statement({"pages": [page]})
    assert len(statement.transactions) == 1
    txn = statement.transactions[0]
    assert txn.description == "PIX ENVIADO"
    assert txn.document == "1044"
    assert txn.counterparty == "Padaria Sao Jose"
    assert txn.counterparty_tax_id == "12.345.678/0001-90"


def test_itau_without_document_stays_empty() -> None:
    page = _itau_page(
        [
            [_itau_word("Lançamentos do período: 01/08/2026 até 31/08/2026 Itaú CNPJ/CPF Valor Saldo SALDO ANTERIOR", 34, 500, 40)],
            [
                _itau_word("01/08/2026", 35, 78, 80),
                _itau_word("SALDO ANTERIOR", 90, 180, 80),
                _itau_word("100,00", 520, 560, 80),
            ],
            [
                _itau_word("03/08/2026", 35, 78, 110),
                _itau_word("TARIFA PACOTE", 88, 200, 110),
                _itau_word("-29,90", 470, 508, 110),
                _itau_word("70,10", 520, 560, 110),
            ],
            [
                _itau_word("03/08/2026", 35, 78, 130),
                _itau_word("SALDO TOTAL DISPONIVEL DIA", 88, 210, 130),
                _itau_word("70,10", 520, 560, 130),
            ],
        ]
    )
    statement, _warnings = normalize_itau_bank_statement({"pages": [page]})
    assert statement.transactions[0].document is None
    assert statement.transactions[0].description == "TARIFA PACOTE"


def test_bradesco_fills_company_cnpj_and_document() -> None:
    layout = """
Agência | Conta Total Disponível (R$) Total (R$)
Extrato de: Ag: 214 | CC: 0082378-3
Entre 01/08/2026 e 31/08/2026
Data Lançamento Dcto. Crédito (R$) Débito (R$) Saldo (R$)
31/07/2026 SALDO ANTERIOR 460.863,74
TED-TRANSF ELET DISPON
03/08/2026 7345068 15.963,68 476.827,42
REMET.FLSMIDTH INDUSTRIAL
TED D CC HBANK*
04/08/2026 7233582 -50.000,00 426.827,42
DEST. VANGUARDA REFRATARIO
CNPJ05075384000125
"""
    raw = {"pages": [{"page_number": 1, "text": {"layout": layout, "basic": layout}}]}
    statement, warnings = normalize_bradesco_bank_statement(raw)
    assert len(statement.transactions) == 2
    first, second = statement.transactions
    assert first.document == "7345068"
    assert first.counterparty == "FLSMIDTH INDUSTRIAL"
    assert "7345068" not in (first.description or "")
    assert "FLSMIDTH" not in (first.description or "")
    assert second.document == "7233582"
    assert second.counterparty == "VANGUARDA REFRATARIO"
    assert second.counterparty_tax_id == "05.075.384/0001-25"
    assert not any(warning.code == "bank_initial_balance_missing" for warning in warnings)


def test_bradesco_does_not_invent_pix_enviado_as_company() -> None:
    layout = """
Agência | Conta Total Disponível (R$) Total (R$)
Extrato de: Ag: 214 | CC: 0082378-3
Entre 01/08/2026 e 31/08/2026
Data Lançamento Dcto. Crédito (R$) Débito (R$) Saldo (R$)
31/07/2026 SALDO ANTERIOR 100,00
PIX ENVIADO
03/08/2026 1111 10,00 110,00
"""
    raw = {"pages": [{"page_number": 1, "text": {"layout": layout, "basic": layout}}]}
    statement, _warnings = normalize_bradesco_bank_statement(raw)
    assert statement.transactions[0].description == "PIX ENVIADO"
    assert statement.transactions[0].document == "1111"
    assert statement.transactions[0].counterparty is None


def _cash_word(text: str, x0: float, x1: float, top: float) -> dict:
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 8}


def test_caixa_copies_cnpj_from_notes_and_leaves_line_ready() -> None:
    header_top = 40
    words = [
        _cash_word("Controle de Fechamento de Caixa", 320, 560, 10),
        _cash_word("SALDO INICIAL", 240, 320, 20),
        _cash_word("DATA", 10, 50, header_top),
        _cash_word("EMISSAO", 80, 130, header_top),
        _cash_word("ENTRADA", 655, 705, header_top),
        _cash_word("SALDO", 770, 820, header_top),
        _cash_word("09/02/2026", 10, 70, 80),
        _cash_word("03/02/2026", 80, 130, 80),
        _cash_word("0000001044", 140, 210, 80),
        _cash_word("MARCELO PARANHOS", 230, 400, 80),
        _cash_word("29304072808", 470, 560, 80),
        _cash_word("0,00", 655, 700, 80),
        _cash_word("570,00", 715, 760, 80),
        _cash_word("100,00", 770, 820, 80),
        _cash_word("10/02/2026", 10, 70, 110),
        _cash_word("10/02/2026", 80, 130, 110),
        _cash_word("TRANSFERENCIA", 230, 400, 110),
        _cash_word("0,00", 655, 700, 110),
        _cash_word("10,00", 715, 760, 110),
        _cash_word("90,00", 770, 820, 110),
    ]
    page = {"page_number": 1, "words": words, "text": {"layout": " ".join(w["text"] for w in words)}}
    collection, _warnings = normalize_cash_ledger_pdf({"pages": [page]})
    entries = [entry for ledger in collection.ledgers for entry in ledger.entries]
    assert len(entries) == 2
    first, second = entries
    assert first.document == "0000001044"
    assert first.counterparty_tax_id == "293.040.728-08"
    assert first.notes is None
    assert second.counterparty_tax_id is None


PRIVATE = Path(__file__).resolve().parent / "fixtures" / "private"
ITAU_FEB = PRIVATE / "itau-2026-02.pdf"
BRADESCO_AUG = PRIVATE / "bradesco-2026-08-native.pdf"
ITAU_AUG = PRIVATE / "itau-2026-08-native.pdf"
CAIXA_FEB = PRIVATE / "caixa-2026-02.pdf"


@pytest.mark.integration
@pytest.mark.skipif(not ITAU_FEB.exists(), reason="fixture Itaú digitalizado ausente")
def test_itau_february_cid_reuses_digital_adapter(tmp_path: Path) -> None:
    from lume_ingestion.artifacts import read_json
    from lume_ingestion.pipeline import run_pipeline

    result = run_pipeline(ITAU_FEB, tmp_path / "output")
    assert result.success, result.errors
    assert result.document_type == "bank_statement"
    assert result.data["bank"] == "Itaú"
    assert len(result.data["transactions"]) > 0
    raw = read_json(result.outputs.raw_json)
    assert raw["document_recognition"]["adapter"] == "itau-digital-bank-statement-v1"
    assert all(warning.code != "xai_vision_applied" for warning in result.warnings)


@pytest.mark.integration
@pytest.mark.skipif(not BRADESCO_AUG.exists(), reason="golden Bradesco nativo ausente")
def test_bradesco_native_has_company_on_remet_lines(tmp_path: Path) -> None:
    from lume_ingestion.pipeline import run_pipeline

    result = run_pipeline(BRADESCO_AUG, tmp_path / "output")
    assert result.success, result.errors
    assert len(result.data["transactions"]) == 88
    counterparties = [txn["counterparty"] for txn in result.data["transactions"] if txn.get("counterparty")]
    assert any("FLSMIDTH" in (name or "") for name in counterparties)
    assert any(txn.get("document") for txn in result.data["transactions"])
    assert result.data["initial_balance"] == "460863.74"
    assert result.data["final_balance"] == "74.01"


@pytest.mark.integration
@pytest.mark.skipif(not ITAU_AUG.exists(), reason="golden Itaú nativo ausente")
def test_itau_native_still_has_counterparty_and_tax_id(tmp_path: Path) -> None:
    from lume_ingestion.pipeline import run_pipeline

    result = run_pipeline(ITAU_AUG, tmp_path / "output")
    assert result.success, result.errors
    txns = result.data["transactions"]
    assert any(txn.get("counterparty") and txn.get("counterparty_tax_id") for txn in txns)
    assert result.data["bank"] == "Itaú"


@pytest.mark.integration
@pytest.mark.skipif(not CAIXA_FEB.exists(), reason="fixture caixa ausente")
def test_caixa_february_extracts_cpf_from_notes(tmp_path: Path) -> None:
    from lume_ingestion.pipeline import run_pipeline

    result = run_pipeline(CAIXA_FEB, tmp_path / "caixa")
    assert result.success, result.errors
    entries = [entry for ledger in result.data["ledgers"] for entry in ledger["entries"]]
    taxed = [entry for entry in entries if entry.get("counterparty_tax_id")]
    assert taxed
    assert any(entry["document"] and "1044" in (entry["document"] or "") for entry in entries)
