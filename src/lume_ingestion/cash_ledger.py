"""Normalization, validation and cross-format comparison for cash ledgers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from lume_ingestion.errors import IngestionFailure
from lume_ingestion.models import CashLedger, CashLedgerCollection, CashLedgerEntry, CashLedgerOrigin, Error, Warning


_MONEY_CENTS = Decimal("0.01")
_HEADER_FIELDS = {
    "date": {"DATA", "DATAMOVIMENTO"},
    "issue_date": {"EMISSAO", "DATAEMISSAO"},
    "document": {"DOC", "DOCUMENTO", "NUMERODOCUMENTO"},
    "counterparty": {"CLIENTEFORNECEDOR", "CLIENTE", "FORNECEDOR"},
    "notes": {"ANOTACOES", "ANOTACAO", "OBSERVACAO", "OBSERVACOES", "HISTORICO"},
    "inflow": {"ENTRADA", "CREDITO"},
    "outflow": {"SAIDA", "DEBITO"},
    "balance": {"SALDO"},
}
_REQUIRED_HEADERS = frozenset(_HEADER_FIELDS)


def fold_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    without_marks = "".join(character for character in decomposed if unicodedata.category(character) != "Mn")
    return re.sub(r"[^A-Z0-9]", "", without_marks.upper())


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).replace("\u00a0", " ").replace("\xad", "-")
    if "Ã" in rendered or "Â" in rendered:
        try:
            rendered = rendered.encode("latin-1").decode("utf-8")
        except UnicodeError:
            pass
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered or None


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    rendered = clean_text(value)
    if not rendered:
        return None
    for pattern in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(rendered, pattern).date()
        except ValueError:
            continue
    return None


def parse_money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, float):
        # XLSX values are exposed as binary floats by openpyxl.  Ledger money
        # is printed to cents, so quantization restores the printed precision.
        parsed = Decimal(str(value))
    else:
        rendered = clean_text(value)
        if not rendered:
            return None
        numeric = re.search(r"[-+]?\d[\d.]*,\d{1,2}|[-+]?\d+(?:\.\d{1,2})?", rendered.replace("R$", ""))
        if not numeric:
            return None
        compact = numeric.group(0)
        if "," in compact:
            compact = compact.replace(".", "").replace(",", ".")
        try:
            parsed = Decimal(compact)
        except InvalidOperation:
            return None
    if not parsed.is_finite():
        return None
    return parsed.quantize(_MONEY_CENTS, rounding=ROUND_HALF_UP)


def _header_columns(rows: list[dict[str, Any]]) -> tuple[int, dict[str, int]] | None:
    for row in rows:
        columns: dict[str, int] = {}
        for cell in row.get("cells", []):
            raw = cell.get("calculated_value") if cell.get("formula") else cell.get("value")
            label = fold_text(str(raw)) if raw is not None else ""
            for field, aliases in _HEADER_FIELDS.items():
                if label in aliases and field not in columns:
                    columns[field] = int(cell["column"])
        if _REQUIRED_HEADERS.issubset(columns):
            return int(row["row_number"]), columns
    return None


def _cell_map(row: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(cell["column"]): cell for cell in row.get("cells", [])}


def _cell_value(cells: dict[int, dict[str, Any]], column: int) -> Any:
    cell = cells.get(column)
    if cell is None:
        return None
    return cell.get("calculated_value") if cell.get("formula") else cell.get("value")


def _entry_from_xlsx_row(
    row: dict[str, Any],
    columns: dict[str, int],
    sheet: str,
) -> CashLedgerEntry | None:
    cells = _cell_map(row)
    transaction_date = parse_date(_cell_value(cells, columns["date"]))
    if transaction_date is None:
        return None
    inflow = parse_money(_cell_value(cells, columns["inflow"]))
    outflow = parse_money(_cell_value(cells, columns["outflow"]))
    balance = parse_money(_cell_value(cells, columns["balance"]))
    if balance is None or (inflow is None and outflow is None):
        return None
    involved_columns = sorted(set(columns.values()))
    return CashLedgerEntry(
        date=transaction_date,
        issue_date=parse_date(_cell_value(cells, columns["issue_date"])),
        document=clean_text(_cell_value(cells, columns["document"])),
        counterparty=clean_text(_cell_value(cells, columns["counterparty"])),
        notes=clean_text(_cell_value(cells, columns["notes"])),
        inflow=inflow or Decimal("0"),
        outflow=outflow or Decimal("0"),
        balance=balance,
        origin=CashLedgerOrigin(
            source_format="xlsx",
            sheet=sheet,
            row_number=int(row["row_number"]),
            cell_refs=[cells[column]["coordinate"] for column in involved_columns if column in cells],
        ),
    )


def _xlsx_segment_reason(previous: CashLedgerEntry, candidate: CashLedgerEntry) -> str | None:
    """Find account transitions without converting ordinary bad rows to breaks.

    This layout puts several account ledgers one after another in one table.
    A new account is evidenced by a restarted date sequence or a fresh zero
    opening balance.  Other discontinuities remain inside the ledger and are
    reported precisely by the validator.
    """

    inferred_opening = candidate.balance - candidate.inflow + candidate.outflow
    if candidate.date < previous.date:
        return "date_sequence_restarted"
    if inferred_opening == 0 and previous.balance != 0:
        return "zero_opening_balance_restarted"
    return None


def _ledger(entries: list[CashLedgerEntry], *, account: str | None = None, period_start: date | None = None, period_end: date | None = None) -> CashLedger:
    if not entries:
        raise ValueError("Um CashLedger precisa de pelo menos um lancamento.")
    initial = entries[0].balance - entries[0].inflow + entries[0].outflow
    return CashLedger(
        account=account,
        period_start=period_start or min(entry.date for entry in entries),
        period_end=period_end or max(entry.date for entry in entries),
        initial_balance=initial,
        final_balance=entries[-1].balance,
        entries=entries,
    )


def normalize_cash_ledger_xlsx(raw: dict[str, Any]) -> tuple[CashLedgerCollection, list[Warning]]:
    sheets = raw.get("workbook", {}).get("sheets")
    if not isinstance(sheets, list):
        raise IngestionFailure("invalid_raw", "O RAW XLSX nao possui as abas extraidas.")
    ledgers: list[CashLedger] = []
    warnings: list[Warning] = []
    header_found = False
    for sheet in sheets:
        if not isinstance(sheet, dict) or not isinstance(sheet.get("rows"), list):
            continue
        rows = sheet["rows"]
        header = _header_columns(rows)
        if header is None:
            continue
        header_found = True
        header_row, columns = header
        entries = [
            entry
            for row in rows
            if int(row.get("row_number", 0)) > header_row
            if (entry := _entry_from_xlsx_row(row, columns, str(sheet.get("name") or ""))) is not None
        ]
        if not entries:
            warnings.append(
                Warning(
                    code="cash_ledger_without_transactions",
                    message="A aba possui o cabecalho de caixa, mas nenhuma linha transacional utilizavel.",
                    details={"sheet": sheet.get("name")},
                )
            )
            continue
        segment: list[CashLedgerEntry] = []
        for entry in entries:
            reason = _xlsx_segment_reason(segment[-1], entry) if segment else None
            if reason:
                previous = segment[-1]
                ledgers.append(_ledger(segment))
                warnings.append(
                    Warning(
                        code="cash_ledger_segment_inferred",
                        message="Uma nova conta de caixa foi inferida pela descontinuidade da tabela XLSX; a planilha nao informa o nome da conta.",
                        details={
                            "reason": reason,
                            "previous_origin": previous.origin.model_dump(mode="json"),
                            "candidate_origin": entry.origin.model_dump(mode="json"),
                            "previous_balance": str(previous.balance),
                            "candidate_inferred_initial_balance": str(entry.balance - entry.inflow + entry.outflow),
                        },
                    )
                )
                segment = []
            segment.append(entry)
        if segment:
            ledgers.append(_ledger(segment))
    if not header_found:
        raise IngestionFailure(
            "cash_ledger_header_not_found",
            "Nao foi localizado um cabecalho de controle de caixa com data, emissao, documento, partes, entrada, saida e saldo.",
        )
    if not ledgers:
        raise IngestionFailure("cash_ledger_without_transactions", "O controle de caixa nao possui linhas transacionais utilizaveis.")
    return CashLedgerCollection(ledgers=ledgers), warnings


def recognize_cash_ledger_pdf(pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Recognize the report by printed labels, never by filename or account."""

    for page in pages:
        words = page.get("words") if isinstance(page, dict) else None
        text = " ".join(str(word.get("text", "")) for word in words if isinstance(word, dict)) if isinstance(words, list) else ""
        folded = fold_text(text)
        required = ("CONTROLEDEFECHAMENTODECAIXA", "SALDOINICIAL", "DATA", "EMISSAO", "ENTRADA", "SALDO")
        if all(label in folded for label in required):
            return {"adapter": "cash-ledger-report-v1", "page_number": page.get("page_number")}
    return None


def _pdf_lines(page: dict[str, Any]) -> list[list[dict[str, Any]]]:
    words = page.get("words")
    if not isinstance(words, list):
        return []
    usable: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, float]] = set()
    for word in words:
        if not isinstance(word, dict) or not word.get("text"):
            continue
        try:
            key = (str(word["text"]), round(float(word["x0"]), 2), round(float(word["top"]), 2), round(float(word["x1"]), 2))
        except (KeyError, TypeError, ValueError):
            continue
        if key not in seen:
            seen.add(key)
            usable.append(word)
    usable.sort(key=lambda word: (float(word["top"]), float(word["x0"])))
    grouped: list[list[dict[str, Any]]] = []
    for word in usable:
        if not grouped or abs(float(word["top"]) - float(grouped[-1][0]["top"])) > 2.0:
            grouped.append([word])
        else:
            grouped[-1].append(word)
    return [sorted(line, key=lambda word: float(word["x0"])) for line in grouped]


def _pdf_line_text(line: list[dict[str, Any]], left: float, right: float) -> str | None:
    words = [
        str(word["text"])
        for word in line
        if left <= (float(word["x0"]) + float(word["x1"])) / 2 < right
    ]
    return clean_text(" ".join(words))


def _pdf_period(lines: list[list[dict[str, Any]]]) -> tuple[date | None, date | None]:
    start = end = None
    for line in lines:
        text = fold_text(" ".join(str(word["text"]) for word in line))
        value = parse_date(_pdf_line_text(line, 650, 780))
        if "DATAINICIAL" in text and value:
            start = value
        if "DATAFINAL" in text and value:
            end = value
    return start, end


def _pdf_account(lines: list[list[dict[str, Any]]]) -> str | None:
    for line in lines:
        top = float(line[0]["top"])
        if 55 <= top <= 78:
            value = _pdf_line_text(line, 0, 240)
            if value and "SALDO" not in fold_text(value):
                return value
    return None


def _append_pdf_continuation(entry: CashLedgerEntry, values: dict[str, str | None], line: list[dict[str, Any]]) -> None:
    for field in ("document", "counterparty", "notes"):
        continuation = values.get(field)
        if continuation:
            previous = getattr(entry, field)
            setattr(entry, field, clean_text(f"{previous or ''} {continuation}"))
    if entry.origin.region:
        entry.origin.region["bottom"] = max(
            entry.origin.region["bottom"],
            max(float(word.get("bottom", word["top"])) for word in line),
        )


def _entries_from_pdf_page(page: dict[str, Any]) -> list[CashLedgerEntry]:
    entries: list[CashLedgerEntry] = []
    current: CashLedgerEntry | None = None
    page_number = int(page["page_number"])
    for line in _pdf_lines(page):
        values = {
            "date": _pdf_line_text(line, 0, 76),
            "issue_date": _pdf_line_text(line, 76, 136),
            "document": _pdf_line_text(line, 136, 223),
            "counterparty": _pdf_line_text(line, 223, 465),
            "notes": _pdf_line_text(line, 465, 650),
            "inflow": _pdf_line_text(line, 650, 710),
            "outflow": _pdf_line_text(line, 710, 765),
            "balance": _pdf_line_text(line, 765, 830),
        }
        transaction_date = parse_date(values["date"])
        if transaction_date:
            inflow = parse_money(values["inflow"])
            outflow = parse_money(values["outflow"])
            balance = parse_money(values["balance"])
            if balance is None or (inflow is None and outflow is None):
                current = None
                continue
            top = min(float(word["top"]) for word in line)
            bottom = max(float(word.get("bottom", word["top"])) for word in line)
            excerpt = clean_text(" ".join(str(word["text"]) for word in line))
            current = CashLedgerEntry(
                date=transaction_date,
                issue_date=parse_date(values["issue_date"]),
                document=values["document"],
                counterparty=values["counterparty"],
                notes=values["notes"],
                inflow=inflow or Decimal("0"),
                outflow=outflow or Decimal("0"),
                balance=balance,
                origin=CashLedgerOrigin(
                    source_format="pdf",
                    page_number=page_number,
                    region={"x0": 0.0, "x1": 830.0, "top": top, "bottom": bottom},
                    excerpt=excerpt,
                ),
            )
            entries.append(current)
        elif current is not None and any(values[field] for field in ("document", "counterparty", "notes")):
            rendered_line = clean_text(" ".join(value or "" for value in values.values()))
            if rendered_line and re.fullmatch(r"\d+\s*/\s*\d+", rendered_line):
                continue  # Repeated report footer, not a wrapped description.
            _append_pdf_continuation(current, values, line)
    return entries


def normalize_cash_ledger_pdf(raw: dict[str, Any]) -> tuple[CashLedgerCollection, list[Warning]]:
    pages = raw.get("pages")
    if not isinstance(pages, list) or not pages:
        raise IngestionFailure("invalid_raw", "O RAW PDF nao possui paginas para o controle de caixa.")
    ledgers: list[CashLedger] = []
    current_entries: list[CashLedgerEntry] = []
    current_account: str | None = None
    current_period_start: date | None = None
    current_period_end: date | None = None
    warnings: list[Warning] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        lines = _pdf_lines(page)
        account = _pdf_account(lines)
        period_start, period_end = _pdf_period(lines)
        page_entries = _entries_from_pdf_page(page)
        if recognize_cash_ledger_pdf([page]) and not page_entries:
            warnings.append(
                Warning(
                    code="unexpected_cash_ledger_page_break",
                    message="A pagina possui cabecalho de controle de caixa, mas nenhuma linha transacional completa foi reconstruida.",
                    details={"page_number": page.get("page_number")},
                )
            )
        if current_entries and account != current_account:
            ledgers.append(
                _ledger(
                    current_entries,
                    account=current_account,
                    period_start=current_period_start,
                    period_end=current_period_end,
                )
            )
            current_entries = []
            current_period_start = current_period_end = None
        if account is not None:
            current_account = account
        current_period_start = current_period_start or period_start
        current_period_end = current_period_end or period_end
        current_entries.extend(page_entries)
    if current_entries:
        ledgers.append(
            _ledger(
                current_entries,
                account=current_account,
                period_start=current_period_start,
                period_end=current_period_end,
            )
        )
    if not ledgers:
        raise IngestionFailure("cash_ledger_without_transactions", "O PDF de controle de caixa nao possui linhas transacionais utilizaveis.")
    return CashLedgerCollection(ledgers=ledgers), warnings


def validate_cash_ledger_collection(collection: CashLedgerCollection) -> list[Error]:
    errors: list[Error] = []
    for ledger_index, ledger in enumerate(collection.ledgers, start=1):
        previous = ledger.initial_balance
        for entry_index, entry in enumerate(ledger.entries, start=1):
            expected = previous + entry.inflow - entry.outflow
            if expected != entry.balance:
                errors.append(
                    Error(
                        code="cash_balance_mismatch",
                        message="O saldo do lancamento nao fecha com o saldo anterior, entrada e saida.",
                        details={
                            "ledger_index": ledger_index,
                            "entry_index": entry_index,
                            "date": entry.date.isoformat(),
                            "expected_balance": str(expected),
                            "actual_balance": str(entry.balance),
                            "origin": entry.origin.model_dump(mode="json"),
                        },
                    )
                )
            previous = entry.balance
        if ledger.entries and ledger.final_balance != ledger.entries[-1].balance:
            errors.append(
                Error(
                    code="cash_final_balance_mismatch",
                    message="O saldo final do controle diverge do ultimo lancamento.",
                    details={
                        "ledger_index": ledger_index,
                        "expected_balance": str(ledger.entries[-1].balance),
                        "actual_balance": str(ledger.final_balance),
                    },
                )
            )
    return errors


def _all_entries(collection: CashLedgerCollection) -> Iterable[CashLedgerEntry]:
    return (entry for ledger in collection.ledgers for entry in ledger.entries)


def _comparison_value(field: str, value: Any) -> Any:
    # PDF word extraction can split a printed document identifier at harmless
    # whitespace positions.  Compare its characters, while retaining each
    # original rendering in a possible divergence report.
    if field == "document" and value is not None:
        return re.sub(r"\s+", "", str(value))
    return value


def compare_cash_ledger_collections(
    reference: CashLedgerCollection | dict[str, Any],
    candidate: CashLedgerCollection | dict[str, Any],
) -> dict[str, Any]:
    """Compare the printed fields required by the R03 cross-format contract."""

    reference_model = CashLedgerCollection.model_validate(reference)
    candidate_model = CashLedgerCollection.model_validate(candidate)
    reference_entries = list(_all_entries(reference_model))
    candidate_entries = list(_all_entries(candidate_model))
    differences: list[dict[str, Any]] = []
    fields = ("date", "issue_date", "document", "inflow", "outflow", "balance")
    for index, (left, right) in enumerate(zip(reference_entries, candidate_entries, strict=False), start=1):
        for field in fields:
            if _comparison_value(field, getattr(left, field)) != _comparison_value(field, getattr(right, field)):
                differences.append(
                    {
                        "code": "cash_ledger_field_mismatch",
                        "entry_index": index,
                        "field": field,
                        "reference": str(getattr(left, field)) if getattr(left, field) is not None else None,
                        "candidate": str(getattr(right, field)) if getattr(right, field) is not None else None,
                        "reference_origin": left.origin.model_dump(mode="json"),
                        "candidate_origin": right.origin.model_dump(mode="json"),
                    }
                )
    if len(reference_entries) != len(candidate_entries):
        differences.append(
            {
                "code": "cash_ledger_entry_count_mismatch",
                "reference_count": len(reference_entries),
                "candidate_count": len(candidate_entries),
            }
        )
    return {
        "matches": not differences,
        "reference_entry_count": len(reference_entries),
        "candidate_entry_count": len(candidate_entries),
        "differences": differences,
    }
