from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from lume_ingestion.artifacts import read_json, write_json
from lume_ingestion.pipeline import run_pipeline, validate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
ITAU_PDF = next(DOCS_ROOT.glob("Extrato/pdf/*Itau*.pdf"))
CASH_LEDGER_PDF = next(DOCS_ROOT.glob("Controle de caixa/pdf/*.pdf"))
BRADESCO_PDF = DOCS_ROOT / "Clientes" / "Vanguarda" / "Extrato Agosto Vanguarda Bradesco.PDF"
LUFTKLIM_XLSX = DOCS_ROOT / "Clientes" / "LUFTKLIM" / "49-08-2026 - Extrato até 25-08.xlsx"
MARTINE_XLS = DOCS_ROOT / "Clientes" / "Martine" / "18-08-2026 - Extrato Bradesco.xls"
GOLDEN = json.loads((Path(__file__).parent / "goldens" / "bank_statement_itau.json").read_text(encoding="utf-8"))
TRANSACTION_FIELDS = (
    "date",
    "description",
    "counterparty",
    "counterparty_tax_id",
    "document",
    "amount",
    "transaction_type",
    "balance",
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.mark.integration
def test_itau_statement_matches_the_full_transaction_and_balance_golden(tmp_path: Path) -> None:
    result = run_pipeline(ITAU_PDF, tmp_path / "output")

    assert result.success, result.errors
    assert result.source_format == "pdf"
    assert result.document_type == "bank_statement"
    statement = result.data
    transactions = [{field: transaction[field] for field in TRANSACTION_FIELDS} for transaction in statement["transactions"]]
    daily_balances = [{field: balance[field] for field in ("date", "balance")} for balance in statement["daily_balances"]]
    assert len(transactions) == GOLDEN["transaction_count"]
    assert digest(transactions) == GOLDEN["transactions_sha256"]
    assert len(daily_balances) == GOLDEN["daily_balance_count"]
    assert digest(daily_balances) == GOLDEN["daily_balances_sha256"]
    assert statement["initial_balance"] == GOLDEN["initial_balance"]
    assert statement["final_balance"] == GOLDEN["final_balance"]
    assert statement["period_start"] == GOLDEN["period_start"]
    assert statement["period_end"] == GOLDEN["period_end"]
    assert transactions[0] == GOLDEN["first_transaction"]
    assert transactions[-1] == GOLDEN["last_transaction"]
    assert all(transaction["origin"]["page_number"] for transaction in statement["transactions"])

    raw = read_json(result.outputs.raw_json)
    assert raw["document_recognition"] == {
        "adapter": "itau-digital-bank-statement-v1",
        "bank": "Itaú",
        "layout": "digital-v1",
        "page_number": 1,
    }
    assert statement["bank"] == "Itaú"
    assert statement["branch"] == "8074"
    assert statement["account"] == "0025044-3"
    assert statement["holder_tax_id"] == "05.075.384/0001-25"
    assert statement["account_limit"] == "181200.00"
    assert statement["limit_used"] == "0.00"
    assert statement["limit_available"] == "181200.00"


@pytest.mark.integration
def test_itau_statement_preserves_signed_values_duplicates_and_page_continuations(tmp_path: Path) -> None:
    statement = run_pipeline(ITAU_PDF, tmp_path / "output").data
    transactions = statement["transactions"]

    assert any("IOF" in (transaction["description"] or "") and transaction["amount"] == "-24.60" for transaction in transactions)
    assert any(transaction["description"].startswith("PIX") for transaction in transactions)
    assert any(transaction["description"].startswith("BOLETO") for transaction in transactions)
    assert any(transaction["description"].startswith("TED") for transaction in transactions)
    repeated = [
        transaction
        for transaction in transactions
        if transaction["date"] == "2026-07-28"
        and transaction["description"] == "PAGAMENTOS CONCESSIONARIA"
        and transaction["counterparty"] == "DAEV"
        and transaction["amount"] == "-130.09"
    ]
    assert len(repeated) == 2
    protecamp = next(transaction for transaction in transactions if "PROTECAMP" in (transaction["counterparty"] or ""))
    assert protecamp["counterparty"] == "PROTECAMP MATERIAIS DE SEGURAN"
    assert any(origin["page_number"] == 4 for origin in protecamp["continuation_origins"])
    assert not any("SALDOTOTAL" in (transaction["description"] or "").replace(" ", "") for transaction in transactions)


@pytest.mark.integration
def test_cash_ledger_is_not_promoted_to_a_bank_statement(tmp_path: Path) -> None:
    result = run_pipeline(CASH_LEDGER_PDF, tmp_path / "output")

    assert result.success, result.errors
    assert result.document_type == "cash_ledger"


@pytest.mark.integration
def test_itau_classification_uses_content_not_the_source_name(tmp_path: Path) -> None:
    renamed = tmp_path / "unrelated-document.pdf"
    shutil.copyfile(ITAU_PDF, renamed)

    result = run_pipeline(renamed, tmp_path / "output")

    assert result.success, result.errors
    assert result.document_type == "bank_statement"
    assert result.data["bank"] == "Itaú"


@pytest.mark.integration
def test_bradesco_statement_is_normalized_and_reconciled(tmp_path: Path) -> None:
    result = run_pipeline(BRADESCO_PDF, tmp_path / "output")

    assert result.success, result.errors
    assert result.document_type == "bank_statement"
    assert result.data["bank"] == "Bradesco"
    assert len(result.data["transactions"]) == 88
    assert result.data["initial_balance"] == "460863.74"
    assert result.data["final_balance"] == "74.01"
    assert not result.warnings


@pytest.mark.integration
def test_itau_spreadsheet_statement_is_normalized_with_auditable_rows(tmp_path: Path) -> None:
    result = run_pipeline(LUFTKLIM_XLSX, tmp_path / "output")

    assert result.success, result.errors
    assert result.source_format == "xlsx"
    assert result.document_type == "bank_statement"
    assert result.data["bank"] == "Itaú"
    assert result.data["branch"] == "0191"
    assert result.data["account"] == "0041707-0"
    assert result.data["period_start"] == "2026-08-01"
    assert result.data["period_end"] == "2026-08-25"
    assert len(result.data["transactions"]) == 352
    assert len(result.data["daily_balances"]) == 17
    assert result.data["transactions"][0]["counterparty"] == "GRAND BELLAGIO PARTICIPACOES SOCIETARIAS LTDA"
    assert result.data["transactions"][0]["origin"]["source_format"] == "xlsx"
    assert result.data["transactions"][0]["origin"]["cell_refs"] == ["A12", "B12", "C12", "D12", "E12"]
    # The provider prints a final balance for 25/08 without a movement for
    # that day, so it must remain visible as an explicit reconciliation warning.
    assert [warning.code for warning in result.warnings] == ["bank_balance_mismatch"]


@pytest.mark.integration
def test_bradesco_legacy_xls_is_normalized_without_appending_the_next_period(tmp_path: Path) -> None:
    result = run_pipeline(MARTINE_XLS, tmp_path / "output")

    assert result.success, result.errors
    assert result.source_format == "xls"
    assert result.document_type == "bank_statement"
    assert result.data["bank"] == "Bradesco"
    assert result.data["branch"] == "1074"
    assert result.data["account"] == "45291-2"
    assert result.data["period_start"] == "2026-08-03"
    assert result.data["period_end"] == "2026-08-31"
    assert len(result.data["transactions"]) == 311
    assert not result.warnings
    assert result.data["transactions"][0]["amount"] == "2592.47"
    assert result.data["transactions"][0]["origin"]["source_format"] == "xls"
    assert result.data["transactions"][-1]["date"] == "2026-08-31"


@pytest.mark.integration
def test_first_reconciliation_divergence_is_a_detailed_warning(tmp_path: Path) -> None:
    result = run_pipeline(ITAU_PDF, tmp_path / "output")
    normalized = read_json(result.outputs.normalized_json)
    normalized["bank_statement"]["daily_balances"][0]["balance"] = "1.00"
    changed = tmp_path / "normalized.json"
    write_json(changed, normalized)

    validation = validate(changed)

    assert validation.success
    mismatch = next(warning for warning in validation.warnings if warning.code == "bank_balance_mismatch")
    assert mismatch.details["date"] == "2026-07-01"
    assert mismatch.details["expected_balance"] == "-106350.55"
    assert mismatch.details["actual_balance"] == "1.00"
    assert mismatch.details["origin"]["page_number"] == 1
