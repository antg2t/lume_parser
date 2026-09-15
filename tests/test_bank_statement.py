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
