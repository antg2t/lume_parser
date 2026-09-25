"""Compare label JSON from any file to known formats; mint and persist new ones.

Incoming PDF tables, spreadsheet sheets and CSV-like rows all become a header
list.  The system maps those titles through a shared alias table, reuses a
stored format when the titles are close, and writes a new format when the
required fields map but the title set is new.  No human names the adapter.
"""

from __future__ import annotations

import json
import os
import re
import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from lume_ingestion.cash_ledger import clean_text, parse_date, parse_money
from lume_ingestion.errors import IngestionFailure

REUSE_MIN = 0.85
VERSION_MIN = 0.60
_PRIOR_BALANCE_LOOKBACK_DAYS = 7
_STORE_ENV = "LUME_FORMAT_REGISTRY"
_PERIOD_RANGE = re.compile(r"(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})", re.I)
_DAY_MONTH = re.compile(r"^(\d{2})/(\d{2})$")
_BANKS = (("Itaú", ("itau", "itaú")), ("Bradesco", ("bradesco",)), ("Santander", ("santander",)))

ALIASES: dict[str, set[str]] = {
    "date": {"DATA", "DATAMOVIMENTO", "DATALANCAMENTO"},
    "description": {"LANCAMENTO", "HISTORICO", "DESCRICAO", "HISTORICOPADRAO"},
    "counterparty": {"RAZAOSOCIAL", "CLIENTEFORNECEDOR", "CLIENTE", "FORNECEDOR", "CONTRAPARTE"},
    "tax_id": {"CPFCNPJ", "CNPJCPF", "CNPJ", "CPF"},
    "amount": {"VALOR", "VALORR", "VALORRS"},
    "credit": {"CREDITO", "CREDITOR"},
    "debit": {"DEBITO", "DEBITOR"},
    "balance": {"SALDO", "SALDOR", "SALDOATUAL"},
    "document": {"DCTO", "DOCUMENTO", "DOC", "NUMERODOCUMENTO"},
    "inflow": {"ENTRADA"},
    "outflow": {"SAIDA"},
    "issue_date": {"EMISSAO", "DATAEMISSAO"},
    "notes": {"ANOTACOES", "ANOTACAO", "OBSERVACAO", "OBSERVACOES"},
}

BANK_REQUIRED = ({"date", "description", "amount"}, {"date", "description", "credit", "debit"})
CASH_REQUIRED = {"date", "inflow", "outflow", "balance"}
LAYOUT_FIELDS = frozenset(ALIASES)
_ABBREVIATIONS = {
    "DT": "date", "DTMOV": "date", "DTMOVIMENTO": "date",
    "DESC": "description", "DESCR": "description", "TEXTO": "description",
    "VL": "amount", "VLR": "amount", "MONTANTE": "amount",
    "RECEBIDO": "inflow", "RECEBIMENTOS": "inflow",
    "PAGO": "outflow", "PAGAMENTOS": "outflow",
}


def fold_text(value: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFD", value)
    without_marks = "".join(character for character in decomposed if unicodedata.category(character) != "Mn")
    return re.sub(r"[^A-Z0-9]", "", without_marks.upper())


def _value(cell: dict[str, Any]) -> Any:
    return cell.get("calculated_value") if cell.get("formula") else cell.get("value")


def _seed_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "format_registry.json"


def _store_path() -> Path:
    override = os.environ.get(_STORE_ENV)
    return Path(override) if override else Path("/var/lib/lume/format-registry.json")


def _load_json(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    formats = payload.get("formats") if isinstance(payload, dict) else payload
    return [item for item in formats or [] if isinstance(item, dict) and item.get("id")]


def load_formats() -> list[dict[str, Any]]:
    by_key: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for item in _load_json(_seed_path()) + _load_json(_store_path()):
        headers = tuple(fold_text(h) for h in item.get("headers") or [] if fold_text(h))
        by_key[(str(item["id"]), headers)] = item
    return list(by_key.values())


def remember_format(item: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _load_json(path)
    headers = [fold_text(h) for h in item.get("headers") or [] if fold_text(h)]
    if any(
        str(existing.get("id")) == item["id"]
        and [fold_text(h) for h in existing.get("headers") or []] == headers
        for existing in current
    ):
        return
    current.append(item)
    payload = json.dumps({"schema_version": "1.0", "formats": current}, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _map_headers(cells: list[dict[str, Any]]) -> tuple[list[str], dict[str, int]]:
    titles: list[str] = []
    columns: dict[str, int] = {}
    for cell in cells:
        raw = _value(cell)
        if raw is None or str(raw).strip() == "":
            continue
        folded = fold_text(str(raw))
        if not folded:
            continue
        titles.append(folded)
        for field, aliases in ALIASES.items():
            if folded in aliases and field not in columns:
                columns[field] = int(cell["column"])
    return titles, columns


def _family_for(columns: dict[str, int]) -> str | None:
    mapped = set(columns)
    if CASH_REQUIRED <= mapped:
        return "cash_ledger"
    if any(req <= mapped for req in BANK_REQUIRED):
        return "bank_statement"
    return None


def _tables_as_sheets(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sheets: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or 0)
        for index, table in enumerate(page.get("tables") or []):
            if not isinstance(table, list):
                continue
            rows: list[dict[str, Any]] = []
            for row_index, row in enumerate(table):
                if not isinstance(row, list):
                    continue
                cells = [
                    {"column": col + 1, "value": value, "formula": None, "calculated_value": None, "coordinate": f"{col + 1}:{row_index + 1}"}
                    for col, value in enumerate(row)
                    if value not in (None, "")
                ]
                if cells:
                    rows.append({"row_number": row_index + 1, "cells": cells})
            if rows:
                sheets.append({"name": f"page-{page_number}-table-{index}", "rows": rows})
    return sheets


def sheets_from_extract(raw: dict[str, Any]) -> list[dict[str, Any]]:
    workbook = raw.get("workbook") if isinstance(raw.get("workbook"), dict) else {}
    sheets = workbook.get("sheets") if isinstance(workbook, dict) else None
    if isinstance(sheets, list) and sheets:
        return [sheet for sheet in sheets if isinstance(sheet, dict)]
    pages = raw.get("pages") if isinstance(raw.get("pages"), list) else []
    return _tables_as_sheets(pages)


def header_titles_from_extract(raw: dict[str, Any]) -> list[str]:
    best: list[str] = []
    best_fields = 0
    for sheet in sheets_from_extract(raw):
        for row in sheet.get("rows") or []:
            if not isinstance(row, dict):
                continue
            titles, columns = _map_headers(row.get("cells") or [])
            if len(columns) > best_fields:
                best, best_fields = titles, len(columns)
    return best


def _rendered_cells(row: dict[str, Any]) -> dict[int, str]:
    rendered: dict[int, str] = {}
    for cell in row.get("cells") or []:
        if not isinstance(cell, dict) or "column" not in cell:
            continue
        value = clean_text(_value(cell))
        if value:
            rendered[int(cell["column"])] = value
    return rendered


def _looks_like_date(value: str) -> bool:
    return parse_date(value) is not None or bool(re.fullmatch(r"\d{2}/\d{2}", value))


def _looks_like_tax_id(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return len(digits) in {11, 14}


def _sample_kind(values: list[str]) -> str:
    if not values:
        return "unknown"
    date_hits = sum(_looks_like_date(value) for value in values)
    money_hits = sum(parse_money(value) is not None for value in values)
    tax_hits = sum(_looks_like_tax_id(value) for value in values)
    threshold = max(1, (len(values) + 1) // 2)
    if date_hits >= threshold:
        return "date"
    if tax_hits >= threshold:
        return "tax_id"
    if money_hits >= threshold:
        return "money"
    return "text"


def _suggest_field(title: str, values: list[str]) -> tuple[str | None, float]:
    folded = fold_text(title)
    for field, aliases in ALIASES.items():
        if folded in aliases:
            return field, 1.0
    if folded in _ABBREVIATIONS:
        return _ABBREVIATIONS[folded], 0.72
    for field, aliases in ALIASES.items():
        if any(len(folded) >= 4 and (folded.startswith(alias[:4]) or alias.startswith(folded[:4])) for alias in aliases):
            return field, 0.62
    kind = _sample_kind(values)
    if kind == "date":
        return "date", 0.45
    if kind == "tax_id":
        return "tax_id", 0.45
    if kind == "money":
        return "amount", 0.40
    return None, 0.0


def layout_proposal_from_extract(raw: dict[str, Any]) -> dict[str, Any]:
    """Return privacy-safe column evidence even when no family can be closed."""
    best: dict[str, Any] | None = None
    best_score = -1
    for sheet in sheets_from_extract(raw):
        rows = [row for row in sheet.get("rows") or [] if isinstance(row, dict)]
        for index, row in enumerate(rows):
            headers = _rendered_cells(row)
            if len(headers) < 2:
                continue
            samples: dict[str, list[str]] = {}
            for column in headers:
                values: list[str] = []
                for sample_row in rows[index + 1:index + 21]:
                    value = _rendered_cells(sample_row).get(column)
                    if value:
                        values.append(value[:160])
                    if len(values) >= 5:
                        break
                samples[str(column)] = values
            suggestions: dict[str, str] = {}
            confidence: dict[str, float] = {}
            claimed: set[str] = set()
            ranked = []
            for column, title in headers.items():
                field_name, score = _suggest_field(title, samples[str(column)])
                if field_name:
                    ranked.append((score, column, field_name))
            for score, column, field_name in sorted(ranked, reverse=True):
                if field_name in claimed:
                    continue
                claimed.add(field_name)
                suggestions[str(column)] = field_name
                confidence[str(column)] = round(score, 2)
            mapped = set(suggestions.values())
            family = "cash" if {"date", "inflow", "outflow"} <= mapped else "bank" if {"date", "description", "amount"} <= mapped else None
            score = len(suggestions) * 10 + sum(bool(values) for values in samples.values())
            candidate = {
                "sheet": str(sheet.get("name") or ""),
                "headerRow": int(row.get("row_number") or 0),
                "headers": [headers[column] for column in sorted(headers)],
                "columns": [column for column in sorted(headers)],
                "samples": samples,
                "suggestions": suggestions,
                "confidence": confidence,
                "family": family,
            }
            if score > best_score:
                best = candidate
                best_score = score
    if best is None:
        best = {"sheet": "", "headerRow": 0, "headers": [], "columns": [], "samples": {}, "suggestions": {}, "confidence": {}, "family": None, "_score": 0}
    folded = [fold_text(title) for title in best["headers"]]
    best["fingerprint"] = hashlib.sha256(json.dumps(folded, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return best


def match_from_layout_map(raw: dict[str, Any], layout_map: dict[str, Any]) -> FormatMatch:
    family_id = str(layout_map.get("family") or "")
    family = {"bank": "bank_statement", "cash": "cash_ledger"}.get(family_id)
    columns_raw = layout_map.get("columns")
    if family is None or not isinstance(columns_raw, dict):
        raise IngestionFailure("layout_map_invalid", "O mapa de colunas nao possui familia e colunas validas.")
    try:
        columns = {str(field): int(column) for field, column in columns_raw.items()}
    except (TypeError, ValueError) as exc:
        raise IngestionFailure("layout_map_invalid", "O mapa de colunas possui indice invalido.") from exc
    if not columns or not set(columns) <= LAYOUT_FIELDS or len(set(columns.values())) != len(columns):
        raise IngestionFailure("layout_map_invalid", "O mapa de colunas possui campo repetido ou desconhecido.")
    mapped = set(columns)
    valid = (
        ({"date", "description", "amount"} <= mapped or {"date", "description", "credit", "debit"} <= mapped)
        if family == "bank_statement"
        else ("date" in mapped and bool({"inflow", "outflow"} & mapped))
    )
    if not valid:
        raise IngestionFailure("layout_map_invalid", "O mapa nao fecha os campos obrigatorios desta familia.")
    proposal = layout_proposal_from_extract(raw)
    sheet_name = str(layout_map.get("sheet") or proposal["sheet"])
    header_row = int(layout_map.get("headerRow") or proposal["headerRow"])
    sheets = {str(sheet.get("name") or ""): sheet for sheet in sheets_from_extract(raw)}
    sheet = sheets.get(sheet_name)
    header = next((row for row in (sheet or {}).get("rows", []) if int(row.get("row_number") or 0) == header_row), None)
    available = set(_rendered_cells(header or {}))
    if sheet is None or not set(columns.values()) <= available:
        raise IngestionFailure("layout_map_invalid", "O mapa aponta para coluna ausente no arquivo.")
    titles = [value for _, value in sorted(_rendered_cells(header).items())]
    return FormatMatch(
        adapter=f"confirmed-{proposal['fingerprint'][:16]}",
        family=family,
        bank=None,
        similarity=1.0,
        minted=False,
        headers=titles,
        columns=columns,
        header_row=header_row,
        sheet_name=sheet_name,
        required=list(columns),
        extractor="layout_map",
    )


def _detect_bank(raw: dict[str, Any], filename: str = "") -> str | None:
    parts = [filename, *header_titles_from_extract(raw)]
    pages = raw.get("pages") if isinstance(raw.get("pages"), list) else []
    for page in pages:
        if isinstance(page, dict):
            text = page.get("text") if isinstance(page.get("text"), dict) else {}
            parts.append(str(text.get("layout") or ""))
            parts.append(str(text.get("basic") or ""))
    blob = fold_text(" ".join(parts))
    for name, aliases in _BANKS:
        if any(fold_text(alias) in blob for alias in aliases):
            return name
    return None


def _next_id(family: str, bank: str | None, existing: list[dict[str, Any]]) -> str:
    slug_bank = fold_text(bank or "desconhecido") or "desconhecido"
    kind = "bank-statement" if family == "bank_statement" else "cash-ledger"
    prefix = f"{slug_bank}-spreadsheet-{kind}-v"
    versions = [1]
    for item in existing:
        match = re.fullmatch(re.escape(prefix) + r"(\d+)", str(item.get("id") or ""))
        if match:
            versions.append(int(match.group(1)))
    return f"{prefix}{max(versions)}"


@dataclass
class FormatMatch:
    adapter: str
    family: str
    bank: str | None
    similarity: float
    minted: bool
    headers: list[str]
    columns: dict[str, int]
    header_row: int
    sheet_name: str
    extractor: str = "format_registry"
    required: list[str] = field(default_factory=list)

    @classmethod
    def from_recognition(cls, raw: dict[str, Any], recognition: dict[str, Any]) -> "FormatMatch":
        return cls(
            adapter=str(recognition.get("adapter") or "desconhecido-spreadsheet-bank-statement-v1"),
            family=str(raw.get("document_type") or "bank_statement"),
            bank=recognition.get("bank") if isinstance(recognition.get("bank"), str) else None,
            similarity=float(recognition.get("similarity") or 0),
            minted=bool(recognition.get("minted")),
            headers=[str(h) for h in recognition.get("headers") or []],
            columns={str(k): int(v) for k, v in (recognition.get("columns") or {}).items()},
            header_row=int(recognition.get("header_row") or 0),
            sheet_name=str(recognition.get("sheet") or ""),
            required=[str(k) for k in (recognition.get("columns") or {})],
        )

    def as_recognition(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "bank": self.bank,
            "layout": "label-registry-v1",
            "extractor": self.extractor,
            "similarity": round(self.similarity, 4),
            "minted": self.minted,
            "headers": self.headers,
            "columns": self.columns,
            "header_row": self.header_row,
            "sheet": self.sheet_name,
        }


def match_extract(raw: dict[str, Any], filename: str = "") -> FormatMatch | None:
    """Map header titles of any tabular extract onto a stored or minted format."""
    best: FormatMatch | None = None
    formats = load_formats()
    bank = _detect_bank(raw, filename)
    for sheet in sheets_from_extract(raw):
        rows = sheet.get("rows") if isinstance(sheet.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            titles, columns = _map_headers(row.get("cells") or [])
            family = _family_for(columns)
            if not family or not titles:
                continue
            incoming = set(titles)
            ranked: list[tuple[float, dict[str, Any]]] = []
            for item in formats:
                if item.get("family") != family:
                    continue
                known = {fold_text(h) for h in item.get("headers") or [] if fold_text(h)}
                ranked.append((jaccard(incoming, known), item))
            ranked.sort(key=lambda pair: pair[0], reverse=True)
            score, item = ranked[0] if ranked else (0.0, None)
            if family == "cash_ledger":
                required = list(item.get("required") or CASH_REQUIRED) if item else list(CASH_REQUIRED)
            else:
                required = list(item.get("required") or next(req for req in BANK_REQUIRED if req <= set(columns))) if item else list(next(req for req in BANK_REQUIRED if req <= set(columns)))
            if not set(required) <= set(columns):
                continue
            if item and score >= REUSE_MIN:
                adapter, minted = str(item["id"]), False
            else:
                adapter, minted = _next_id(family, bank, formats), True
            current = FormatMatch(
                adapter=adapter,
                family=family,
                bank=bank or (str(item.get("bank")) if item and item.get("bank") else None),
                similarity=score if item else 1.0,
                minted=minted,
                headers=titles,
                columns=columns,
                header_row=int(row.get("row_number") or 0),
                sheet_name=str(sheet.get("name") or ""),
                required=required,
            )
            if best is None or (current.similarity, len(current.columns)) > (best.similarity, len(best.columns)):
                best = current
    return best


def catalog_layouts() -> list[dict[str, Any]]:
    layouts = []
    for item in load_formats():
        headers = [fold_text(h) for h in item.get("headers") or [] if fold_text(h)]
        labels = [fold_text(h) for h in item.get("labels") or [] if fold_text(h)]
        layouts.append({
            "document_type": item.get("family"),
            "family": "caixa" if item.get("family") == "cash_ledger" else "extrato",
            "parser": "format-registry",
            "adapter": item.get("id"),
            "labels": tuple(labels or headers),
            "filename": (),
            "title": (),
            "headers": tuple(headers),
        })
    return layouts


def _cells(row: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(cell["column"]): cell for cell in row.get("cells") or [] if isinstance(cell, dict) and "column" in cell}


def _cell_value(cells: dict[int, dict[str, Any]], column: int) -> Any:
    cell = cells.get(column)
    if cell is None:
        return None
    return _value(cell)


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


def _branch_account(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    branch = account = None
    for row in rows:
        values = [_value(cell) for cell in row.get("cells") or []]
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
    return branch, account


def _period(rows: list[dict[str, Any]]) -> tuple[date | None, date | None]:
    dates: list[date] = []
    for row in rows:
        blob = " ".join(str(_value(cell) or "") for cell in row.get("cells") or [])
        found = _PERIOD_RANGE.search(blob.replace("\xa0", " "))
        if found:
            for raw in found.groups():
                if (parsed := parse_date(raw)) and parsed not in dates:
                    dates.append(parsed)
    return (dates[0] if dates else None, dates[1] if len(dates) > 1 else None)


def _amount(cells: dict[int, dict[str, Any]], columns: dict[str, int]) -> Decimal | None:
    if "amount" in columns:
        return parse_money(_cell_value(cells, columns["amount"]))
    credit = parse_money(_cell_value(cells, columns["credit"])) if "credit" in columns else None
    debit = parse_money(_cell_value(cells, columns["debit"])) if "debit" in columns else None
    if credit is None and debit is None:
        return None
    if credit is not None and debit is not None:
        return credit - abs(debit)
    return credit if credit is not None else -abs(debit or Decimal("0"))


def _is_daily_balance(folded: str) -> bool:
    return folded == "SALDO" or folded.startswith("SALDOTOTAL") or folded.startswith("SALDOEMCONTA") or folded.startswith("SDOCTA")


def extract_bank_statement(raw: dict[str, Any], match: FormatMatch):
    from lume_ingestion.bank_statement import reconcile_bank_statement
    from lume_ingestion.models import BankStatement, BankStatementDailyBalance, BankStatementOrigin, BankStatementTransaction
    sheets = {str(sheet.get("name") or ""): sheet for sheet in sheets_from_extract(raw)}
    sheet = sheets.get(match.sheet_name) or next(iter(sheets.values()), None)
    if sheet is None:
        raise IngestionFailure("spreadsheet_columns_not_mapped", "Nao ha tabela com titulos de coluna utilizaveis.")
    rows = sheet.get("rows") or []
    source_format = str(raw.get("source_format") or "xlsx")
    period_start, period_end = _period(rows)
    branch, account = _branch_account(rows)
    columns = match.columns
    initial_balance: Decimal | None = None
    daily_by_date: dict[date, BankStatementDailyBalance] = {}
    transactions: list[BankStatementTransaction] = []
    for row in rows:
        if not isinstance(row, dict) or int(row.get("row_number") or 0) <= match.header_row:
            continue
        cells = _cells(row)
        transaction_date = _parse_row_date(_cell_value(cells, columns["date"]) if "date" in columns else None, period_start, period_end)
        description = clean_text(_cell_value(cells, columns["description"])) if "description" in columns else None
        if transaction_date is None or not description:
            continue
        balance = parse_money(_cell_value(cells, columns["balance"])) if "balance" in columns else None
        origin = BankStatementOrigin(
            source_format=source_format,
            sheet=match.sheet_name,
            row_number=int(row["row_number"]),
            cell_refs=[str(cell.get("coordinate") or "") for cell in row.get("cells") or []],
            region={"x0": 0.0, "x1": 1.0, "top": float(row["row_number"]), "bottom": float(row["row_number"]) + 1},
            excerpt=description,
        )
        folded = fold_text(description)
        if "SALDOANTERIOR" in folded:
            initial_balance = initial_balance if initial_balance is not None else balance
            continue
        if _is_daily_balance(folded):
            if balance is not None:
                daily_by_date[transaction_date] = BankStatementDailyBalance(date=transaction_date, balance=balance, origin=origin)
            continue
        amount = _amount(cells, columns)
        if amount is None:
            continue
        transactions.append(BankStatementTransaction(
            date=transaction_date,
            description=description,
            counterparty=clean_text(_cell_value(cells, columns["counterparty"])) if "counterparty" in columns else None,
            counterparty_tax_id=clean_text(_cell_value(cells, columns["tax_id"])) if "tax_id" in columns else None,
            document=clean_text(_cell_value(cells, columns["document"])) if "document" in columns else None,
            amount=amount,
            transaction_type="credit" if amount >= 0 else "debit",
            balance=None,
            origin=origin,
        ))
    daily_balances = [daily_by_date[key] for key in sorted(daily_by_date)]
    if not transactions and initial_balance is None:
        raise IngestionFailure("spreadsheet_columns_not_mapped", "Os titulos de coluna nao produziram lancamentos.")
    _persist_minted(match, source_format)
    statement = BankStatement(
        bank=match.bank or "Desconhecido",
        layout="label-registry-v1",
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


def _persist_minted(match: FormatMatch, source_format: str) -> None:
    # Runtime learning belongs to the SQL recipe catalog.  The parser keeps
    # bundled seeds read-only and must never mint a host-global JSON recipe.
    del match, source_format


def extract_cash_ledger(raw: dict[str, Any], match: FormatMatch):
    from lume_ingestion.models import CashLedger, CashLedgerCollection, CashLedgerEntry, CashLedgerOrigin, Warning

    sheets = {str(sheet.get("name") or ""): sheet for sheet in sheets_from_extract(raw)}
    sheet = sheets.get(match.sheet_name) or next(iter(sheets.values()), None)
    if sheet is None:
        raise IngestionFailure("spreadsheet_columns_not_mapped", "Nao ha tabela com titulos de coluna utilizaveis.")
    rows = sheet.get("rows") or []
    source_format = str(raw.get("source_format") or "xlsx")
    origin_format = source_format
    columns = match.columns
    entries: list[CashLedgerEntry] = []
    running_balance = Decimal("0")
    for row in rows:
        if not isinstance(row, dict) or int(row.get("row_number") or 0) <= match.header_row:
            continue
        cells = _cells(row)
        transaction_date = parse_date(_cell_value(cells, columns["date"]) if "date" in columns else None)
        inflow = parse_money(_cell_value(cells, columns["inflow"])) if "inflow" in columns else None
        outflow = parse_money(_cell_value(cells, columns["outflow"])) if "outflow" in columns else None
        balance = parse_money(_cell_value(cells, columns["balance"])) if "balance" in columns else None
        if transaction_date is None or (inflow is None and outflow is None):
            continue
        running_balance = balance if balance is not None else running_balance + (inflow or Decimal("0")) - (outflow or Decimal("0"))
        entries.append(CashLedgerEntry(
            date=transaction_date,
            issue_date=parse_date(_cell_value(cells, columns["issue_date"])) if "issue_date" in columns else None,
            document=clean_text(_cell_value(cells, columns["document"])) if "document" in columns else None,
            counterparty=clean_text(_cell_value(cells, columns["counterparty"])) if "counterparty" in columns else None,
            notes=clean_text(_cell_value(cells, columns.get("notes", columns.get("description", 0)))) if "notes" in columns or "description" in columns else None,
            inflow=inflow or Decimal("0"),
            outflow=outflow or Decimal("0"),
            balance=running_balance,
            origin=CashLedgerOrigin(
                source_format=origin_format,
                sheet=match.sheet_name,
                row_number=int(row["row_number"]),
                cell_refs=[str(cell.get("coordinate") or "") for cell in row.get("cells") or []],
            ),
        ))
    if not entries:
        raise IngestionFailure("spreadsheet_columns_not_mapped", "Os titulos de coluna nao produziram lancamentos de caixa.")
    _persist_minted(match, source_format)
    ledger = CashLedger(
        initial_balance=entries[0].balance - entries[0].inflow + entries[0].outflow,
        final_balance=entries[-1].balance,
        period_start=entries[0].date,
        period_end=entries[-1].date,
        entries=entries,
    )
    return CashLedgerCollection(ledgers=[ledger]), []
