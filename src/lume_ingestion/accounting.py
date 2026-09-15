"""Explicit XLSX adapters for the supplied chart of accounts and journal layouts."""

from __future__ import annotations

from typing import Any

from lume_ingestion.cash_ledger import clean_text, fold_text, parse_date, parse_money
from lume_ingestion.errors import IngestionFailure
from lume_ingestion.models import (
    AccountingHistory,
    AccountingHistoryEntry,
    ChartOfAccounts,
    ChartOfAccountsEntry,
    SpreadsheetOrigin,
)


_CHART_HEADERS = {"code": {"CONTA"}, "description": {"DESCRICAO"}, "reduced_code": {"REDUZIDA"}}
_HISTORY_HEADERS = {
    "entry_number": {"LANCAMENTO"}, "date": {"DATA"}, "debit_account": {"DEBITO"},
    "credit_account": {"CREDITO"}, "amount": {"VALOR"}, "standard_history": {"HISTORICOPADRAO"},
    "complement": {"COMPLEMENTO"}, "debit_cost_center": {"CCDB"}, "credit_cost_center": {"CCCR"}, "tax_id": {"CNPJ"},
}


def _columns(rows: list[dict[str, Any]], fields: dict[str, set[str]]) -> tuple[int, dict[str, int]] | None:
    required = {"code", "description"} if fields is _CHART_HEADERS else {"date", "debit_account", "credit_account", "amount", "complement"}
    for row in rows:
        found = {
            field: int(cell["column"])
            for cell in row.get("cells", [])
            if (value := _value(cell)) is not None
            for field, aliases in fields.items()
            if fold_text(str(value)) in aliases
        }
        if required <= found.keys():
            return int(row["row_number"]), found
    return None


def _value(cell: dict[str, Any]) -> Any:
    return cell.get("calculated_value") if cell.get("formula") else cell.get("value")


def _code(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean_text(value)


def _row_values(row: dict[str, Any], columns: dict[str, int]) -> tuple[dict[str, Any], list[str]]:
    cells = {int(cell["column"]): cell for cell in row.get("cells", [])}
    return (
        {field: _value(cells[column]) if column in cells else None for field, column in columns.items()},
        [cells[column]["coordinate"] for column in sorted(columns.values()) if column in cells],
    )


def recognize_xlsx_document_type(sheets: list[dict[str, Any]]) -> str | None:
    for sheet in sheets:
        rows = sheet.get("rows") if isinstance(sheet, dict) else None
        if isinstance(rows, list) and _columns(rows, _CHART_HEADERS):
            return "chart_of_accounts"
    for sheet in sheets:
        rows = sheet.get("rows") if isinstance(sheet, dict) else None
        if isinstance(rows, list) and _columns(rows, _HISTORY_HEADERS):
            return "accounting_history"
    return None


def normalize_chart_of_accounts(raw: dict[str, Any]) -> ChartOfAccounts:
    accounts: list[ChartOfAccountsEntry] = []
    for sheet in raw.get("workbook", {}).get("sheets", []):
        rows = sheet.get("rows") if isinstance(sheet, dict) else None
        if not isinstance(rows, list) or not (header := _columns(rows, _CHART_HEADERS)):
            continue
        header_row, columns = header
        for row in rows:
            if int(row.get("row_number", 0)) <= header_row:
                continue
            values, refs = _row_values(row, columns)
            code, description = _code(values["code"]), clean_text(values["description"])
            if code and description:
                accounts.append(ChartOfAccountsEntry(code=code, description=description, reduced_code=_code(values.get("reduced_code")), origin=SpreadsheetOrigin(sheet=str(sheet.get("name") or ""), row_number=int(row["row_number"]), cell_refs=refs)))
    if not accounts:
        raise IngestionFailure("chart_of_accounts_rows_not_found", "O plano de contas nao possui linhas com conta e descricao.")
    if len({account.code for account in accounts}) != len(accounts):
        raise IngestionFailure("duplicate_chart_of_accounts_code", "O plano de contas possui contas duplicadas.")
    return ChartOfAccounts(accounts=accounts)


def normalize_accounting_history(raw: dict[str, Any]) -> AccountingHistory:
    entries: list[AccountingHistoryEntry] = []
    for sheet in raw.get("workbook", {}).get("sheets", []):
        rows = sheet.get("rows") if isinstance(sheet, dict) else None
        if not isinstance(rows, list) or not (header := _columns(rows, _HISTORY_HEADERS)):
            continue
        header_row, columns = header
        for row in rows:
            if int(row.get("row_number", 0)) <= header_row:
                continue
            values, refs = _row_values(row, columns)
            entry_date, debit, credit, amount = parse_date(values["date"]), _code(values["debit_account"]), _code(values["credit_account"]), parse_money(values["amount"])
            if entry_date and debit and credit and amount is not None:
                entries.append(AccountingHistoryEntry(entry_number=_code(values.get("entry_number")), date=entry_date, debit_account=debit, credit_account=credit, amount=amount, standard_history=_code(values.get("standard_history")), complement=clean_text(values.get("complement")), debit_cost_center=_code(values.get("debit_cost_center")), credit_cost_center=_code(values.get("credit_cost_center")), tax_id=_code(values.get("tax_id")), origin=SpreadsheetOrigin(sheet=str(sheet.get("name") or ""), row_number=int(row["row_number"]), cell_refs=refs)))
    if not entries:
        raise IngestionFailure("accounting_history_rows_not_found", "O historico contabil nao possui lancamentos utilizaveis.")
    return AccountingHistory(entries=entries)
