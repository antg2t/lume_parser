"""Deterministic adapter for the current Itaú digital bank-statement layout.

The PDF's linear text interleaves long counterparties with adjacent rows.  This
adapter therefore uses the word coordinates retained in RAW and only derives a
transaction direction from the sign of its printed amount.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from lume_ingestion.cash_ledger import clean_text, fold_text, parse_date, parse_money
from lume_ingestion.errors import IngestionFailure
from lume_ingestion.models import (
    BankStatement,
    BankStatementDailyBalance,
    BankStatementOrigin,
    BankStatementTransaction,
    Warning,
)


_DATE_PATTERN = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_TAX_ID_PATTERN = re.compile(r"\b(?:\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2})\b")

# Column limits are measured from the printed header of this layout.  They are
# intentionally kept here, inside the Itaú adapter, rather than leaking into
# the generic PDF parser or a future bank adapter.
_DATE_COLUMN = (0.0, 80.0)
_DESCRIPTION_COLUMN = (80.0, 220.0)
_COUNTERPARTY_COLUMN = (220.0, 356.0)
_TAX_ID_COLUMN = (356.0, 450.0)
_AMOUNT_COLUMN = (450.0, 512.0)
_BALANCE_COLUMN = (512.0, 596.0)


@dataclass(frozen=True)
class StatementLine:
    page_number: int
    words: tuple[dict[str, Any], ...]
    top: float
    bottom: float

    @property
    def text(self) -> str:
        return " ".join(str(word["text"]) for word in self.words)

    @property
    def folded(self) -> str:
        return fold_text(self.text)

    def column(self, left: float, right: float) -> str | None:
        words = [
            str(word["text"])
            for word in self.words
            if left <= (float(word["x0"]) + float(word["x1"])) / 2 < right
        ]
        return clean_text(" ".join(words))

    def origin(self) -> BankStatementOrigin:
        return BankStatementOrigin(
            page_number=self.page_number,
            region={"x0": 0.0, "x1": 596.0, "top": self.top, "bottom": self.bottom},
            excerpt=clean_text(self.text) or "",
        )


@dataclass(frozen=True)
class Fragment:
    page_number: int
    top: float
    ordinal: int
    values: dict[str, str | None]
    origin: BankStatementOrigin


@dataclass
class StatementRow:
    line: StatementLine
    transaction_date: date
    kind: str
    amount: Decimal | None
    balance: Decimal | None
    base_values: dict[str, str | None]
    fragments: list[Fragment] = field(default_factory=list)


def _page_lines(page: dict[str, Any]) -> list[StatementLine]:
    raw_words = page.get("words") if isinstance(page, dict) else None
    if not isinstance(raw_words, list):
        return []
    words: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, float]] = set()
    for raw_word in raw_words:
        if not isinstance(raw_word, dict) or not raw_word.get("text"):
            continue
        try:
            key = (
                str(raw_word["text"]),
                round(float(raw_word["x0"]), 2),
                round(float(raw_word["x1"]), 2),
                round(float(raw_word["top"]), 2),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if key not in seen:
            seen.add(key)
            words.append(raw_word)
    words.sort(key=lambda word: (float(word["top"]), float(word["x0"])))

    grouped: list[list[dict[str, Any]]] = []
    for word in words:
        if not grouped or abs(float(word["top"]) - float(grouped[-1][0]["top"])) > 2.0:
            grouped.append([word])
        else:
            grouped[-1].append(word)
    page_number = int(page.get("page_number", 0))
    return [
        StatementLine(
            page_number=page_number,
            words=tuple(sorted(group, key=lambda word: float(word["x0"]))),
            top=min(float(word["top"]) for word in group),
            bottom=max(float(word.get("bottom", word["top"])) for word in group),
        )
        for group in grouped
    ]


def _line_date(line: StatementLine) -> date | None:
    rendered = line.column(*_DATE_COLUMN)
    return parse_date(rendered)


def _tax_id(value: str | None) -> str | None:
    if not value:
        return None
    match = _TAX_ID_PATTERN.search(value)
    return match.group(0) if match else None


def _row_from_line(line: StatementLine) -> StatementRow | None:
    transaction_date = _line_date(line)
    if transaction_date is None:
        return None
    description = line.column(*_DESCRIPTION_COLUMN)
    description_folded = fold_text(description or "")
    amount = parse_money(line.column(*_AMOUNT_COLUMN))
    balance = parse_money(line.column(*_BALANCE_COLUMN))
    base_values = {
        "description": description,
        "counterparty": line.column(*_COUNTERPARTY_COLUMN),
        "tax_id": line.column(*_TAX_ID_COLUMN),
    }
    if "SALDOANTERIOR" in description_folded:
        return StatementRow(line, transaction_date, "initial", amount, balance, base_values)
    if "SALDOTOTAL" in description_folded and "DIA" in description_folded:
        return StatementRow(line, transaction_date, "daily_balance", amount, balance, base_values)
    if amount is None:
        return None
    return StatementRow(line, transaction_date, "transaction", amount, balance, base_values)


def _fragment_from_line(line: StatementLine, ordinal: int) -> Fragment | None:
    values = {
        "description": line.column(*_DESCRIPTION_COLUMN),
        "counterparty": line.column(*_COUNTERPARTY_COLUMN),
        "tax_id": line.column(*_TAX_ID_COLUMN),
    }
    if not any(values.values()):
        return None
    # Repeated headers and page notes may share a column with the table, but
    # never represent a continuation of a movement.
    folded = fold_text(" ".join(value or "" for value in values.values()))
    if "LANCAMENTOS" in folded and "CNPJCPF" in folded:
        return None
    return Fragment(
        page_number=line.page_number,
        top=line.top,
        ordinal=ordinal,
        values=values,
        origin=line.origin(),
    )


def _row_values(row: StatementRow) -> tuple[dict[str, str | None], list[BankStatementOrigin]]:
    pieces: list[tuple[int, float, int, dict[str, str | None]]] = [
        (row.line.page_number, row.line.top, 0, row.base_values),
        *[(fragment.page_number, fragment.top, fragment.ordinal, fragment.values) for fragment in row.fragments],
    ]
    values: dict[str, str | None] = {}
    for field in ("description", "counterparty", "tax_id"):
        rendered = clean_text(" ".join(piece[field] or "" for _, _, _, piece in sorted(pieces) if piece[field]))
        values[field] = rendered
    continuation_origins = [fragment.origin for fragment in sorted(row.fragments, key=lambda item: (item.page_number, item.top, item.ordinal))]
    return values, continuation_origins


def _header_from_pages(pages: list[dict[str, Any]]) -> tuple[dict[str, Any], list[Warning]]:
    warnings: list[Warning] = []
    page_lines = [(page, _page_lines(page)) for page in pages if isinstance(page, dict)]
    period_page: dict[str, Any] | None = None
    lines: list[StatementLine] = []
    period_line: StatementLine | None = None
    for page, candidate_lines in page_lines:
        for line in candidate_lines:
            if "LANCAMENTOSDOPERIODO" in line.folded and "FUTUROS" not in line.folded:
                period_page, lines, period_line = page, candidate_lines, line
                break
        if period_line:
            break
    if period_line is None or period_page is None:
        raise IngestionFailure(
            "itau_statement_period_not_found",
            "O cabecalho de lancamentos do periodo do extrato Itaú nao foi localizado.",
        )

    period_dates = [parse_date(value) for value in _DATE_PATTERN.findall(period_line.text)]
    period_dates = [value for value in period_dates if value is not None]
    if len(period_dates) < 2:
        warnings.append(
            Warning(
                code="bank_statement_period_missing",
                message="O periodo impresso do extrato Itaú nao possui as duas datas esperadas.",
                details={"origin": period_line.origin().model_dump(mode="json")},
            )
        )

    identity_line = next(
        (line for line in lines if "CNPJ" in line.folded and "CONTA" in line.folded),
        None,
    )
    branch = account = holder = holder_tax_id = None
    if identity_line:
        identity_words = list(identity_line.words)
        cnpj_index = next((index for index, word in enumerate(identity_words) if "CNPJ" in fold_text(str(word["text"]))), None)
        if cnpj_index is not None:
            holder = clean_text(" ".join(str(word["text"]) for word in identity_words[:cnpj_index]))
        holder_tax_id = _tax_id(identity_line.text)
        for index, word in enumerate(identity_words[:-1]):
            label = fold_text(str(word["text"]))
            following = clean_text(str(identity_words[index + 1]["text"]))
            if label.startswith("AG") and following and re.fullmatch(r"\d{4}", following):
                branch = following
            if "CONTA" in label and following:
                account = following
    else:
        warnings.append(
            Warning(
                code="bank_statement_identity_missing",
                message="Agencia, conta, titular e CNPJ/CPF nao foram localizados juntos no cabecalho do extrato.",
            )
        )

    summary_line_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "SALDOTOTAL" in line.folded and "LIMITE" in line.folded and "UTILIZADO" in line.folded
        ),
        None,
    )
    totals: list[Decimal | None] = [None, None, None, None]
    if summary_line_index is not None and summary_line_index + 1 < len(lines):
        values_line = lines[summary_line_index + 1]
        totals = [
            parse_money(values_line.column(0, 140)),
            parse_money(values_line.column(140, 275)),
            parse_money(values_line.column(275, 410)),
            parse_money(values_line.column(410, 596)),
        ]
    else:
        warnings.append(
            Warning(
                code="bank_statement_summary_missing",
                message="Os saldos e limites impressos no cabecalho do extrato nao foram localizados.",
            )
        )
    return (
        {
            "branch": branch,
            "account": account,
            "holder": holder,
            "holder_tax_id": holder_tax_id,
            "period_start": period_dates[0] if period_dates else None,
            "period_end": period_dates[1] if len(period_dates) > 1 else None,
            "total_balance": totals[0],
            "account_limit": totals[1],
            "limit_used": totals[2],
            "limit_available": totals[3],
        },
        warnings,
    )


def recognize_itau_bank_statement(pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Recognize this bank and layout solely from printed PDF contents."""

    folded_pages: list[tuple[int | None, str]] = []
    for page in pages:
        lines = _page_lines(page)
        folded_pages.append((page.get("page_number") if isinstance(page, dict) else None, fold_text(" ".join(line.text for line in lines))))
    combined = " ".join(text for _, text in folded_pages)
    required = ("LANCAMENTOSDOPERIODO", "SALDOANTERIOR", "CNPJCPF", "VALOR", "SALDO")
    if "ITAU" not in combined or not all(label in combined for label in required):
        return None
    page_number = next((number for number, text in folded_pages if "LANCAMENTOSDOPERIODO" in text), None)
    return {
        "adapter": "itau-digital-bank-statement-v1",
        "bank": "Itaú",
        "layout": "digital-v1",
        "page_number": page_number,
    }


def _page_text(page: dict[str, Any]) -> str:
    text = page.get("text", "")
    if isinstance(text, dict):
        return str(text.get("layout") or text.get("basic") or "")
    return str(text or "")


def recognize_bradesco_bank_statement(pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    combined = fold_text(" ".join(_page_text(page) for page in pages if isinstance(page, dict)))
    required = ("EXTRATODE", "SALDOANTERIOR", "CREDITOR", "DEBITOR", "AGENCIA", "CONTA")
    if not all(label in combined for label in required):
        return None
    return {"adapter": "bradesco-bank-statement-v1", "bank": "Bradesco", "layout": "monthly-v1", "page_number": 1}


def recognize_bank_statement(pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    return recognize_itau_bank_statement(pages) or recognize_bradesco_bank_statement(pages)


def _attach_fragments(rows: list[StatementRow], lines: list[StatementLine], pending: list[Fragment], ordinal: int) -> tuple[list[Fragment], int]:
    transaction_rows = [row for row in rows if row.kind == "transaction"]
    if pending and transaction_rows:
        transaction_rows[0].fragments.extend(pending)
        pending = []
    if not rows:
        return pending, ordinal
    first_top = rows[0].line.top
    last_top = rows[-1].line.top
    for line in lines:
        if _line_date(line) is not None or line.top < first_top - 8 or line.top > last_top + 20:
            continue
        fragment = _fragment_from_line(line, ordinal)
        ordinal += 1
        if fragment is None:
            continue
        if line.top > last_top + 2:
            if fragment.values["description"] and not fragment.values["counterparty"] and not fragment.values["tax_id"]:
                # A wrapped description can cross the bottom edge while its
                # date, counterparty and amount remain on the last printed
                # line (for example, the Itaú TED code ending in "B V").
                # It still belongs to that last movement.
                nearest = min(transaction_rows, key=lambda row: abs(row.line.top - line.top), default=None)
                if nearest is not None:
                    nearest.fragments.append(fragment)
                continue
            # The current PDF breaks one counterparty at the page boundary.
            # Keeping the fragment until the next page preserves its printed
            # reading order without treating it as a duplicate transaction.
            pending.append(fragment)
            continue
        nearest = min(transaction_rows, key=lambda row: abs(row.line.top - line.top), default=None)
        if nearest is not None:
            nearest.fragments.append(fragment)
    return pending, ordinal


def _build_rows(pages: list[dict[str, Any]]) -> list[StatementRow]:
    rows: list[StatementRow] = []
    pending: list[Fragment] = []
    ordinal = 1
    for page in pages:
        if not isinstance(page, dict):
            continue
        lines = _page_lines(page)
        page_rows = [row for line in lines if (row := _row_from_line(line)) is not None]
        pending, ordinal = _attach_fragments(page_rows, lines, pending, ordinal)
        rows.extend(page_rows)
    return rows


def reconcile_bank_statement(statement: BankStatement) -> list[Warning]:
    """Reconcile printed daily balances and report only the first divergence."""

    if statement.initial_balance is None:
        return [
            Warning(
                code="bank_initial_balance_missing",
                message="Nao foi localizado o saldo anterior impresso para iniciar a conciliacao.",
            )
        ]
    daily_by_date = {balance.date: balance for balance in statement.daily_balances}
    for transaction in statement.transactions:
        if transaction.date not in daily_by_date:
            return [
                Warning(
                    code="bank_daily_balance_missing",
                    message="Ha lancamentos sem o saldo diario impresso correspondente.",
                    details={
                        "date": transaction.date.isoformat(),
                        "origin": transaction.origin.model_dump(mode="json"),
                    },
                )
            ]
    previous = statement.initial_balance
    for daily_balance in statement.daily_balances:
        movements = [transaction for transaction in statement.transactions if transaction.date == daily_balance.date]
        expected = previous + sum((transaction.amount for transaction in movements), Decimal("0"))
        if expected != daily_balance.balance:
            return [
                Warning(
                    code="bank_balance_mismatch",
                    message="O primeiro saldo diario divergente nao fecha com o saldo anterior e os lancamentos impressos.",
                    details={
                        "date": daily_balance.date.isoformat(),
                        "expected_balance": str(expected),
                        "actual_balance": str(daily_balance.balance),
                        "transaction_count": len(movements),
                        "origin": daily_balance.origin.model_dump(mode="json"),
                    },
                )
            ]
        previous = daily_balance.balance
    if statement.final_balance is None:
        return [
            Warning(
                code="bank_final_balance_missing",
                message="Nao foi localizado o saldo diario final impresso no extrato.",
            )
        ]
    if statement.daily_balances and statement.final_balance != statement.daily_balances[-1].balance:
        return [
            Warning(
                code="bank_final_balance_mismatch",
                message="O saldo final normalizado diverge do ultimo saldo diario impresso.",
                details={
                    "expected_balance": str(statement.daily_balances[-1].balance),
                    "actual_balance": str(statement.final_balance),
                },
            )
        ]
    return []


def normalize_itau_bank_statement(raw: dict[str, Any]) -> tuple[BankStatement, list[Warning]]:
    pages = raw.get("pages")
    if not isinstance(pages, list) or not pages:
        raise IngestionFailure("invalid_raw", "O RAW PDF nao possui paginas para o extrato bancario.")
    recognition = recognize_itau_bank_statement(pages)
    if recognition is None:
        raise IngestionFailure(
            "unsupported_bank_statement_layout",
            "O RAW nao corresponde ao layout de extrato digital Itaú suportado nesta release.",
        )
    header, warnings = _header_from_pages(pages)
    rows = _build_rows(pages)
    initial_row = next((row for row in rows if row.kind == "initial" and row.balance is not None), None)
    daily_rows = [row for row in rows if row.kind == "daily_balance" and row.balance is not None]
    transactions: list[BankStatementTransaction] = []
    for row in rows:
        if row.kind != "transaction" or row.amount is None:
            continue
        values, continuation_origins = _row_values(row)
        if values["description"] is None:
            warnings.append(
                Warning(
                    code="bank_transaction_description_missing",
                    message="Um lancamento possui valor, mas nao possui descricao impressa utilizavel.",
                    details={"origin": row.line.origin().model_dump(mode="json")},
                )
            )
        transactions.append(
            BankStatementTransaction(
                date=row.transaction_date,
                description=values["description"],
                counterparty=values["counterparty"],
                counterparty_tax_id=_tax_id(values["tax_id"]),
                document=None,
                amount=row.amount,
                transaction_type="credit" if row.amount >= 0 else "debit",
                balance=row.balance,
                origin=row.line.origin(),
                continuation_origins=continuation_origins,
            )
        )
    daily_balances = [
        BankStatementDailyBalance(date=row.transaction_date, balance=row.balance, origin=row.line.origin())
        for row in daily_rows
        if row.balance is not None
    ]
    statement = BankStatement(
        bank=str(recognition["bank"]),
        layout=str(recognition["layout"]),
        **header,
        initial_balance=initial_row.balance if initial_row else None,
        final_balance=daily_balances[-1].balance if daily_balances else None,
        transactions=transactions,
        daily_balances=daily_balances,
    )
    warnings.extend(reconcile_bank_statement(statement))
    return statement, warnings


def normalize_bradesco_bank_statement(raw: dict[str, Any]) -> tuple[BankStatement, list[Warning]]:
    pages = raw.get("pages")
    if not isinstance(pages, list) or not pages:
        raise IngestionFailure("invalid_raw", "O RAW PDF nao possui paginas para o extrato bancario.")
    recognition = recognize_bradesco_bank_statement(pages)
    if recognition is None:
        raise IngestionFailure("unsupported_bank_statement_layout", "O RAW nao corresponde ao layout Bradesco suportado.")

    date_prefix = re.compile(r"^(\d{2}/\d{2}/\d{4})(.*)$")
    movement = re.compile(r"(-?\d{1,3}(?:\.\d{3})*,\d{2})\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})$")
    period_pattern = re.compile(r"Entre\s+(\d{2}/\d{2}/\d{4})\s+e\s+(\d{2}/\d{2}/\d{4})", re.I)
    account_pattern = re.compile(r"Extrato\s+de:\s*Ag:\s*(\d+)\s*\|\s*CC:\s*([\d-]+)", re.I)
    pending: list[str] = []
    transactions: list[BankStatementTransaction] = []
    initial_balance: Decimal | None = None
    current_date: date | None = None
    combined = "\n".join(_page_text(page) for page in pages if isinstance(page, dict))
    period_match, account_match = period_pattern.search(combined), account_pattern.search(combined)

    for page in pages:
        page_number = int(page.get("page_number", 1))
        for line_number, raw_line in enumerate(_page_text(page).splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            if match := date_prefix.match(line):
                current_date = parse_date(match.group(1))
                line = match.group(2).strip()
            if "SALDOANTERIOR" in fold_text(line):
                values = re.findall(r"-?\d{1,3}(?:\.\d{3})*,\d{2}", line)
                if values and initial_balance is None:
                    initial_balance = parse_money(values[-1])
                pending.clear()
                continue
            if fold_text(line).startswith(("TOTAL", "DATALANCAMENTO", "OSDADOS", "FOLHA")):
                pending.clear()
                continue
            match = movement.search(line)
            if match and current_date:
                amount, balance = parse_money(match.group(1)), parse_money(match.group(2))
                if amount is None or balance is None:
                    continue
                description = clean_text(" ".join([*pending, line[: match.start()]]))
                origin = BankStatementOrigin(
                    page_number=page_number,
                    region={"x0": 0.0, "x1": 596.0, "top": float(line_number), "bottom": float(line_number + 1)},
                    excerpt=line,
                )
                transactions.append(BankStatementTransaction(
                    date=current_date,
                    description=description,
                    document=None,
                    amount=amount,
                    transaction_type="credit" if amount >= 0 else "debit",
                    balance=balance,
                    origin=origin,
                ))
                pending.clear()
            elif not any(marker in fold_text(line) for marker in ("EXTRATOMENSAL", "NOMEDOUSUARIO", "SALDOSINVEST")):
                pending.append(line)

    # ponytail: this layout repeats the last movement at the next page; a boundary-only suffix match is enough here.
    unique: list[BankStatementTransaction] = []
    for transaction in transactions:
        boundary_repeat = float(transaction.origin.region.get("top", 99)) <= 5 and any(
            previous.origin.page_number == transaction.origin.page_number - 1
            and previous.date == transaction.date
            and previous.amount == transaction.amount
            and (previous.description or "").endswith(transaction.description or "")
            for previous in unique
        )
        if not boundary_repeat:
            unique.append(transaction)
    last_by_date = {transaction.date: transaction for transaction in unique}
    daily_balances = [
        BankStatementDailyBalance(date=day, balance=transaction.balance, origin=transaction.origin)
        for day, transaction in sorted(last_by_date.items())
        if transaction.balance is not None
    ]
    statement = BankStatement(
        bank=str(recognition["bank"]),
        layout=str(recognition["layout"]),
        branch=account_match.group(1) if account_match else None,
        account=account_match.group(2) if account_match else None,
        period_start=parse_date(period_match.group(1)) if period_match else None,
        period_end=parse_date(period_match.group(2)) if period_match else None,
        initial_balance=initial_balance,
        final_balance=daily_balances[-1].balance if daily_balances else None,
        transactions=unique,
        daily_balances=daily_balances,
    )
    warnings = reconcile_bank_statement(statement)
    return statement, warnings


def normalize_bank_statement(raw: dict[str, Any]) -> tuple[BankStatement, list[Warning]]:
    pages = raw.get("pages")
    recognition = recognize_bank_statement(pages if isinstance(pages, list) else [])
    if recognition and recognition["adapter"].startswith("bradesco"):
        return normalize_bradesco_bank_statement(raw)
    return normalize_itau_bank_statement(raw)
