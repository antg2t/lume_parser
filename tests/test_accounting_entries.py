from datetime import date
from decimal import Decimal

from lume_ingestion.accounting_entries import AccountChoice, AccountingBase, generate_accounting_entries
from lume_ingestion.models import (
    AccountingHistoryEntry,
    BankStatement,
    BankStatementOrigin,
    BankStatementTransaction,
    CashLedger,
    CashLedgerEntry,
    CashLedgerOrigin,
    ChartOfAccountsEntry,
    SpreadsheetOrigin,
)


def test_generic_engine_supports_cash_statement_or_both() -> None:
    sheet_origin = SpreadsheetOrigin(sheet="base", row_number=1)
    accounts = (
        ChartOfAccountsEntry(code="1.1.1.02.000", description="Bancos Conta Movimento", origin=sheet_origin),
        ChartOfAccountsEntry(code="1.1.1.02.001", reduced_code="BANK9", description="Banco Horizonte", origin=sheet_origin),
        ChartOfAccountsEntry(code="1.1.1.05.001", reduced_code="REC9", description="Duplicatas a Receber", origin=sheet_origin),
    )
    history = (
        AccountingHistoryEntry(
            date=date(2026, 7, 1), debit_account="BANK9", credit_account="REC9",
            amount=Decimal("10"), complement="Recebimento cliente Acme", origin=sheet_origin,
        ),
    )
    cash_entry = CashLedgerEntry(
        date=date(2026, 8, 1), counterparty="ACME", inflow=Decimal("50"), outflow=Decimal("0"),
        balance=Decimal("50"), origin=CashLedgerOrigin(source_format="xlsx", sheet="caixa", row_number=2),
    )
    ledger = CashLedger(account="Banco Horizonte", initial_balance=Decimal("0"), final_balance=Decimal("50"), entries=[cash_entry])
    bank_origin = BankStatementOrigin(page_number=1, region={"x0": 0, "x1": 1, "top": 0, "bottom": 1}, excerpt="ACME 50")
    statement = BankStatement(
        bank="Horizonte", layout="test", transactions=[BankStatementTransaction(
            date=date(2026, 8, 1), counterparty="ACME", amount=Decimal("50"),
            transaction_type="credit", origin=bank_origin,
        )],
    )

    for ledgers, statements, mode in (([ledger], [], "cash_only"), ([], [statement], "statement_only")):
        result = generate_accounting_entries(AccountingBase(tuple(statements), tuple(ledgers), history, accounts), "2026-08")
        assert result["audit"]["input_mode"] == mode
        assert len(result["entries"]) == 1
        assert result["entries"][0]["debit_account"] == "BANK9"
        assert result["entries"][0]["credit_account"] == "REC9"

    both = generate_accounting_entries(AccountingBase((statement,), (ledger,), history, accounts), "2026-08")
    assert both["audit"]["input_mode"] == "cash_and_statement"
    assert len(both["entries"]) == 2
    assert {row["debit_account"] for row in both["entries"]} == {"BANK9"}
    assert {row["credit_account"] for row in both["entries"]} == {"REC9"}

    unnamed = ledger.model_copy(update={"account": None})
    uncertain = generate_accounting_entries(AccountingBase((), (unnamed,), history, accounts), "2026-08")
    assert uncertain["entries"][0]["debit_account"] is None
    assert uncertain["entries"][0]["status"] == "REVISAR CONTA"

    first_month = generate_accounting_entries(AccountingBase((), (ledger,), (), accounts), "2026-08")
    assert first_month["audit"]["input_history_rows"] == 0
    assert first_month["entries"][0]["debit_account"] == "BANK9"
    assert first_month["entries"][0]["credit_account"] == "REC9"
    assert first_month["entries"][0]["status"] == "REVISAR CONTA"
    assert first_month["entries"][0]["reason"] == "padrao_por_direcao"

    global_fallback = lambda event, direction: AccountChoice("REC9", None, "Alta", "global_tfidf:test")
    bootstrapped = generate_accounting_entries(AccountingBase((), (ledger,), (), accounts), "2026-08", global_fallback)
    assert bootstrapped["entries"][0]["credit_account"] == "REC9"
    assert bootstrapped["entries"][0]["status"] == "LANCAR"
    assert bootstrapped["audit"]["global_fallback_count"] == 1

    local_first = generate_accounting_entries(AccountingBase((), (ledger,), history, accounts), "2026-08", global_fallback)
    assert local_first["entries"][0]["reason"].startswith("historico_textual:")
    assert local_first["audit"]["global_fallback_count"] == 0


def _chart_and_bank():
    sheet_origin = SpreadsheetOrigin(sheet="base", row_number=1)
    accounts = (
        ChartOfAccountsEntry(code="1.1.1.02.000", description="Bancos Conta Movimento", origin=sheet_origin),
        ChartOfAccountsEntry(code="1.1.1.02.001", reduced_code="BANK9", description="Banco Horizonte", origin=sheet_origin),
        ChartOfAccountsEntry(code="1.1.1.05.001", reduced_code="REC9", description="Duplicatas a Receber", origin=sheet_origin),
        ChartOfAccountsEntry(code="2.1.1.01.001", reduced_code="MEM9", description="Clientes competencia", origin=sheet_origin),
    )
    bank_origin = BankStatementOrigin(page_number=1, region={"x0": 0, "x1": 1, "top": 0, "bottom": 1}, excerpt="ACME 50")
    statement = BankStatement(
        bank="Horizonte",
        layout="test",
        transactions=[BankStatementTransaction(
            date=date(2026, 2, 1), counterparty="ACME", amount=Decimal("50"),
            transaction_type="credit", origin=bank_origin,
        )],
    )
    return sheet_origin, accounts, statement


def test_cadastro_catalog_classifies_later_hist_into_earlier_period() -> None:
    sheet_origin, accounts, statement = _chart_and_bank()
    history = (
        AccountingHistoryEntry(
            date=date(2026, 7, 1), debit_account="BANK9", credit_account="REC9",
            amount=Decimal("10"), complement="Recebimento cliente Acme", origin=sheet_origin,
        ),
    )
    result = generate_accounting_entries(AccountingBase((statement,), (), history, accounts), "2026-02")
    assert result["audit"]["input_history_rows"] == 1
    assert result["entries"][0]["reason"].startswith("historico_textual:")
    assert result["entries"][0]["status"] == "LANCAR"
    assert result["entries"][0]["credit_account"] == "REC9"


def test_competence_memory_outranks_cadastro_and_stays_forward_only() -> None:
    sheet_origin, accounts, statement = _chart_and_bank()
    catalog = (
        AccountingHistoryEntry(
            date=date(2026, 7, 1), debit_account="BANK9", credit_account="REC9",
            amount=Decimal("10"), complement="Recebimento cliente Acme", origin=sheet_origin,
        ),
    )
    memory = (
        AccountingHistoryEntry(
            date=date(2026, 1, 15), debit_account="BANK9", credit_account="MEM9",
            amount=Decimal("50"), complement="ACME", origin=sheet_origin,
        ),
    )
    ranked = generate_accounting_entries(
        AccountingBase((statement,), (), catalog, accounts),
        "2026-02",
        memory_history=memory,
    )
    assert ranked["audit"]["input_memory_rows"] == 1
    assert ranked["entries"][0]["reason"].startswith("historico_competencia:")
    assert ranked["entries"][0]["credit_account"] == "MEM9"

    future_memory = (
        AccountingHistoryEntry(
            date=date(2026, 3, 1), debit_account="BANK9", credit_account="MEM9",
            amount=Decimal("50"), complement="ACME", origin=sheet_origin,
        ),
    )
    forward = generate_accounting_entries(
        AccountingBase((statement,), (), catalog, accounts),
        "2026-02",
        memory_history=future_memory,
    )
    assert forward["audit"]["input_memory_rows"] == 0
    assert forward["entries"][0]["reason"].startswith("historico_textual:")
    assert forward["entries"][0]["credit_account"] == "REC9"
