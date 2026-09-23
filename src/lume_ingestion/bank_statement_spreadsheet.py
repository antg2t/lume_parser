"""Deterministic adapters for the supported bank-statement spreadsheets."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from lume_ingestion.bank_statement import reconcile_bank_statement
from lume_ingestion.cash_ledger import clean_text, fold_text, parse_date, parse_money
from lume_ingestion.identity import extract_tax_id, split_document
from lume_ingestion.errors import IngestionFailure
from lume_ingestion.models import (
    BankStatement,
    BankStatementDailyBalance,
    BankStatementOrigin,
    BankStatementTransaction,
    Warning,
)


_ITAU_HEADERS = {
    "date": {"DATA"},
    "description": {"LANCAMENTO"},
    "counterparty": {"RAZAOSOCIAL"},
    "tax_id": {"CPFCNPJ"},
    "amount": {"VALOR", "VALORR"},
    "balance": {"SALDO", "SALDOR"},
}
_ITAU_REQUIRED = {"date", "description", "amount", "balance"}
_PRIOR_BALANCE_LOOKBACK_DAYS = 7
_BRADESCO_HEADERS = {
    "date": {"DATA"},
    "description": {"LANCAMENTO"},
    "document": {"DCTO", "DOCUMENTO"},
    "credit": {"CREDITO", "CREDITOR"},
    "debit": {"DEBITO", "DEBITOR"},
    "balance": {"SALDO", "SALDOR"},
}
_PERIOD_DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_PERIOD_RANGE = re.compile(r"(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})", re.I)
_DAY_MONTH = re.compile(r"^(\d{2})/(\d{2})$")
_BRADESCO_ACCOUNT = re.compile(r"AG(?:E)?NCIA\D*(\d+)\D*CONTA\D*([\d-]+)", re.I)
_BRADESCO_ACCOUNT_PRINTED = re.compile(r"AG.NCIA\s*:\s*(\d+).*?CONTA\s*:\s*([\d-]+)", re.I)


def _value(cell: dict[str, Any]) -> Any:
    return cell.get("calculated_value") if cell.get("formula") else cell.get("value")


def _cells(row: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(cell["column"]): cell for cell in row.get("cells", []) if isinstance(cell, dict) and "column" in cell}


def _header(
    rows: list[dict[str, Any]],
    fields: dict[str, set[str]],
    required: set[str] | None = None,
) -> tuple[int, dict[str, int]] | None:
    needed = required or set(fields)
    for row in rows:
        columns: dict[str, int] = {}
        for cell in row.get("cells", []):
            value = _value(cell)
            label = fold_text(str(value)) if value is not None else ""
            for field, aliases in fields.items():
                if label in aliases and field not in columns:
                    columns[field] = int(cell["column"])
        if needed <= set(columns):
            return int(row["row_number"]), columns
    return None


def _sheet_with_header(
    sheets: list[dict[str, Any]],
    fields: dict[str, set[str]],
    required: set[str] | None = None,
) -> tuple[dict[str, Any], int, dict[str, int]] | None:
    for sheet in sheets:
        rows = sheet.get("rows") if isinstance(sheet, dict) else None
        if isinstance(rows, list) and (found := _header(rows, fields, required)):
            header_row, columns = found
            return sheet, header_row, columns
    return None


def _all_text(sheets: list[dict[str, Any]]) -> str:
    return " ".join(
        str(_value(cell) or "")
        for sheet in sheets
        if isinstance(sheet, dict)
        for row in sheet.get("rows", [])
        if isinstance(row, dict)
        for cell in row.get("cells", [])
        if isinstance(cell, dict)
    )


def recognize_spreadsheet_bank_statement(sheets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Recognize only from a tabular signature and the printed bank name."""

    text = fold_text(_all_text(sheets))
    has_itau_columns = _sheet_with_header(sheets, _ITAU_HEADERS, _ITAU_REQUIRED)
    has_itau_counterparty = _sheet_with_header(sheets, _ITAU_HEADERS)
    if has_itau_counterparty and "ITAU" in text:
        return {"adapter": "itau-spreadsheet-bank-statement-v1", "bank": "Itaú", "layout": "spreadsheet-v1"}
    if has_itau_columns and "EXTRATODECONTACORRENTE" in text and "AGENCIACONTA" in text:
        return {"adapter": "itau-spreadsheet-bank-statement-v1", "bank": "Itaú", "layout": "spreadsheet-v1"}
    if _sheet_with_header(sheets, _BRADESCO_HEADERS) and "BRADESCO" in text:
        return {"adapter": "bradesco-net-empresa-xls-v1", "bank": "Bradesco", "layout": "net-empresa-v1"}
    return None


def _parse_row_date(value: Any, period_start: date | None, period_end: date | None) -> date | None:
    parsed = parse_date(value)
    if parsed:
        return parsed
    rendered = clean_text(value)
    if not rendered:
        return None
    match = _DAY_MONTH.fullmatch(rendered)
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    years: list[int] = []
    for year in (period_end.year if period_end else None, period_start.year if period_start else None, (period_start.year - 1) if period_start else None):
        if year is not None and year not in years:
            years.append(year)
    window_start = period_start - timedelta(days=_PRIOR_BALANCE_LOOKBACK_DAYS) if period_start else None
    for year in years:
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if window_start and period_end:
            if window_start <= candidate <= period_end:
                return candidate
        else:
            return candidate
    return None


def _cell_value(cells: dict[int, dict[str, Any]], column: int) -> Any:
    return _value(cells[column]) if column in cells else None


def _document(value: Any) -> str | None:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean_text(value)


def _origin(
    source_format: str,
    sheet: str,
    row: dict[str, Any],
    cells: dict[int, dict[str, Any]],
    columns: dict[str, int],
) -> BankStatementOrigin:
    involved = [cells[column] for column in sorted(set(columns.values())) if column in cells]
    row_number = int(row["row_number"])
    excerpt = clean_text(" | ".join(str(_value(cell) or "") for cell in involved)) or "Linha de extrato"
    return BankStatementOrigin(
        source_format=source_format,
        sheet=sheet,
        row_number=row_number,
        cell_refs=[str(cell["coordinate"]) for cell in involved],
        region={"x0": 0.0, "x1": float(max(columns.values())), "top": float(row_number), "bottom": float(row_number + 1)},
        excerpt=excerpt,
    )


def _metadata(rows: list[dict[str, Any]]) -> tuple[str | None, str | None, date | None, date | None]:
    branch = account = None
    dates: list[date] = []
    for row in rows:
        values = [_value(cell) for cell in row.get("cells", [])]
        blob = " ".join(str(value or "") for value in values)
        row_label = fold_text(blob)
        range_match = _PERIOD_RANGE.search(blob.replace("\xa0", " "))
        if range_match:
            for raw in range_match.groups():
                if (parsed := parse_date(raw)) and parsed not in dates:
                    dates.append(parsed)
        for index, value in enumerate(values):
            label = fold_text(str(value)) if value is not None else ""
            following = clean_text(values[index + 1]) if index + 1 < len(values) else None
            if label == "AGENCIA" and following:
                branch = following
            elif label == "CONTA" and following:
                account = following
            elif label == "AGENCIACONTA" and following:
                parts = following.replace(" ", "").split("/", 1)
                if len(parts) == 2:
                    branch = branch or parts[0]
                    account = account or parts[1]
        if "PERIODO" in row_label and not dates:
            dates.extend(parsed for raw in _PERIOD_DATE.findall(blob) if (parsed := parse_date(raw)))
    return branch, account, dates[0] if dates else None, dates[1] if len(dates) > 1 else None


def _is_daily_balance(folded: str) -> bool:
    return (
        folded == "SALDO"
        or folded.startswith("SALDOTOTALDISPONIVELDIA")
        or folded.startswith("SALDOEMCONTACORRENTE")
        or folded.startswith("SDOCTA")
    )


def _itau_statement(raw: dict[str, Any], source_format: str) -> tuple[BankStatement, list[Warning]]:
    sheets = raw.get("workbook", {}).get("sheets", [])
    found = _sheet_with_header(sheets, _ITAU_HEADERS, _ITAU_REQUIRED)
    if not found:
        raise IngestionFailure("unsupported_bank_statement_layout", "O RAW nao corresponde ao layout tabular Itaú suportado.")
    sheet, header_row, columns = found
    rows = sheet["rows"]
    branch, account, period_start, period_end = _metadata(rows)
    initial_balance: Decimal | None = None
    daily_by_date: dict[date, BankStatementDailyBalance] = {}
    transactions: list[BankStatementTransaction] = []
    for row in rows:
        if int(row["row_number"]) <= header_row:
            continue
        cells = _cells(row)
        transaction_date = _parse_row_date(_cell_value(cells, columns["date"]), period_start, period_end)
        description = clean_text(_cell_value(cells, columns["description"]))
        if transaction_date is None or not description:
            continue
        balance = parse_money(_cell_value(cells, columns["balance"]))
        origin = _origin(source_format, str(sheet["name"]), row, cells, columns)
        folded = fold_text(description)
        if "SALDOANTERIOR" in folded:
            initial_balance = initial_balance if initial_balance is not None else balance
            continue
        if _is_daily_balance(folded):
            if balance is not None:
                daily_by_date[transaction_date] = BankStatementDailyBalance(date=transaction_date, balance=balance, origin=origin)
            continue
        amount = parse_money(_cell_value(cells, columns["amount"]))
        if amount is None:
            continue
        description, document = split_document(description)
        counterparty = clean_text(_cell_value(cells, columns["counterparty"])) if "counterparty" in columns else None
        tax_id = clean_text(_cell_value(cells, columns["tax_id"])) if "tax_id" in columns else None
        transactions.append(BankStatementTransaction(
            date=transaction_date,
            description=description,
            counterparty=counterparty,
            counterparty_tax_id=tax_id or extract_tax_id(tax_id, counterparty),
            document=document,
            amount=amount,
            transaction_type="credit" if amount >= 0 else "debit",
            balance=None,
            origin=origin,
        ))
    daily_balances = [daily_by_date[key] for key in sorted(daily_by_date)]
    if not transactions and initial_balance is None:
        raise IngestionFailure("unsupported_bank_statement_layout", "O RAW nao corresponde ao layout tabular Itaú suportado.")
    statement = BankStatement(
        bank="Itaú",
        layout="spreadsheet-v1",
        branch=branch,
        account=account,
        period_start=period_start or min((entry.date for entry in transactions), default=None),
        period_end=period_end or max((entry.date for entry in transactions), default=None),
        initial_balance=initial_balance,
        final_balance=daily_balances[-1].balance if daily_balances else None,
        transactions=transactions,
        daily_balances=daily_balances,
    )
    return statement, reconcile_bank_statement(statement)


def _bradesco_statement(raw: dict[str, Any], source_format: str) -> tuple[BankStatement, list[Warning]]:
    sheets = raw.get("workbook", {}).get("sheets", [])
    found = _sheet_with_header(sheets, _BRADESCO_HEADERS)
    if not found:
        raise IngestionFailure("unsupported_bank_statement_layout", "O RAW nao corresponde ao layout Bradesco Net Empresa suportado.")
    sheet, header_row, columns = found
    rows = sheet["rows"]
    branch = account = None
    printed = _all_text([sheet])
    match = _BRADESCO_ACCOUNT_PRINTED.search(printed) or _BRADESCO_ACCOUNT.search(fold_text(printed))
    if match:
        branch, account = match.groups()
    initial_balance: Decimal | None = None
    transactions: list[BankStatementTransaction] = []
    daily_by_date: dict[date, BankStatementDailyBalance] = {}
    for row in rows:
        if int(row["row_number"]) <= header_row:
            continue
        if _header([row], _BRADESCO_HEADERS):
            # A second printed header begins a distinct statement period.  It
            # must not be appended to the August account movements.
            break
        cells = _cells(row)
        transaction_date = parse_date(_cell_value(cells, columns["date"]))
        description = clean_text(_cell_value(cells, columns["description"]))
        if transaction_date is None or not description:
            continue
        balance = parse_money(_cell_value(cells, columns["balance"]))
        origin = _origin(source_format, str(sheet["name"]), row, cells, columns)
        folded = fold_text(description)
        if "SALDOANTERIOR" in folded:
            initial_balance = initial_balance if initial_balance is not None else balance
            continue
        credit = parse_money(_cell_value(cells, columns["credit"]))
        debit = parse_money(_cell_value(cells, columns["debit"]))
        if credit is None and debit is None:
            continue
        amount = credit if credit is not None else -abs(debit or Decimal("0"))
        transaction = BankStatementTransaction(
            date=transaction_date,
            description=description,
            document=_document(_cell_value(cells, columns["document"])),
            amount=amount,
            transaction_type="credit" if amount >= 0 else "debit",
            balance=balance,
            origin=origin,
        )
        transactions.append(transaction)
        if balance is not None:
            daily_by_date[transaction_date] = BankStatementDailyBalance(date=transaction_date, balance=balance, origin=origin)
    daily_balances = [daily_by_date[key] for key in sorted(daily_by_date)]
    statement = BankStatement(
        bank="Bradesco",
        layout="net-empresa-v1",
        branch=branch,
        account=account,
        period_start=min((entry.date for entry in transactions), default=None),
        period_end=max((entry.date for entry in transactions), default=None),
        initial_balance=initial_balance,
        final_balance=daily_balances[-1].balance if daily_balances else None,
        transactions=transactions,
        daily_balances=daily_balances,
    )
    return statement, reconcile_bank_statement(statement)


def normalize_spreadsheet_bank_statement(raw: dict[str, Any]) -> tuple[BankStatement, list[Warning]]:
    source_format = str(raw.get("source_format") or "")
    if source_format not in {"xlsx", "xls"}:
        raise IngestionFailure("invalid_raw", "O extrato tabular precisa ter origem XLSX ou XLS.")
    sheets = raw.get("workbook", {}).get("sheets", [])
    if not isinstance(sheets, list):
        raise IngestionFailure("invalid_raw", "O RAW tabular nao possui abas de planilha.")
    recognition = recognize_spreadsheet_bank_statement(sheets)
    if recognition is None:
        raise IngestionFailure("unsupported_bank_statement_layout", "O layout tabular do extrato bancario nao e suportado.")
    if recognition["bank"] == "Itaú":
        return _itau_statement(raw, source_format)
    return _bradesco_statement(raw, source_format)
