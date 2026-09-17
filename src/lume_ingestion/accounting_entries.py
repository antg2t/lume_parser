"""Deterministic cash-to-accounting classification over normalized documents."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable

from lume_ingestion.artifacts import write_json
from lume_ingestion.models import (
    AccountingHistory,
    AccountingHistoryEntry,
    BankStatement,
    CashLedger,
    CashLedgerCollection,
    ChartOfAccounts,
    ChartOfAccountsEntry,
    IngestionResult,
)
from lume_ingestion.pipeline import discover_files, run_pipeline


def plain(value: object) -> str:
    return re.sub(
        r"\s+", " ",
        unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower(),
    ).strip()


def words(value: object) -> set[str]:
    ignored = {
        "para", "com", "por", "dos", "das", "uma", "ltda", "eireli", "brasil", "pagamento",
        "recebimento", "nota", "fiscal", "fatura", "valor", "competencia", "numero", "mes",
    }
    return {word for word in re.findall(r"[a-z]{3,}", plain(value)) if word not in ignored}


def signed(entry) -> Decimal:
    return entry.inflow - entry.outflow


def month_of(value: date) -> str:
    return value.isoformat()[:7]


@dataclass(frozen=True)
class AccountingBase:
    statements: tuple[BankStatement, ...]
    ledgers: tuple[CashLedger, ...]
    history: tuple[AccountingHistoryEntry, ...]
    accounts: tuple[ChartOfAccountsEntry, ...]

    @classmethod
    def from_results(cls, results: Iterable[IngestionResult]) -> "AccountingBase":
        statements: list[BankStatement] = []
        ledgers: list[CashLedger] = []
        history: list[AccountingHistoryEntry] = []
        accounts: dict[str, ChartOfAccountsEntry] = {}
        for result in results:
            if not result.success:
                continue
            if result.document_type == "bank_statement":
                statements.append(BankStatement.model_validate(result.data))
            elif result.document_type == "cash_ledger":
                ledgers.extend(CashLedgerCollection.model_validate(result.data).ledgers)
            elif result.document_type == "accounting_history":
                history.extend(AccountingHistory.model_validate(result.data).entries)
            elif result.document_type == "chart_of_accounts":
                for account in ChartOfAccounts.model_validate(result.data).accounts:
                    key = account.reduced_code or account.code
                    previous = accounts.get(key)
                    if previous and plain(previous.description) != plain(account.description):
                        raise ValueError(f"Conta {key} possui descricoes conflitantes nos planos.")
                    accounts[key] = account
        if (not ledgers and not statements) or not accounts:
            raise ValueError("Sao obrigatorios movimentos (caixa e/ou extrato) e plano de contas.")
        return cls(tuple(statements), tuple(ledgers), tuple(history), tuple(accounts.values()))


@dataclass(frozen=True)
class CashEvent:
    ledger_index: int
    row_number: int | None
    date: date
    amount: Decimal
    document: str | None
    counterparty: str | None
    notes: str | None
    financial_account: str | None
    financial_name: str | None

    @property
    def text(self) -> str:
        return " ".join(filter(None, (self.counterparty, self.notes, self.document)))


@dataclass(frozen=True)
class AccountChoice:
    counterpart: str | None
    standard_history: str | None
    confidence: str
    reason: str


@dataclass(frozen=True)
class GeneratedEntry:
    date: date
    debit_account: str | None
    credit_account: str | None
    amount: Decimal
    standard_history: str | None
    complement: str
    financial_name: str | None
    document: str | None
    source_rows: tuple[int, ...]
    status: str
    confidence: str
    reason: str

    def json(self) -> dict:
        value = asdict(self)
        value["date"] = self.date.isoformat()
        value["amount"] = str(self.amount)
        value["source_rows"] = list(self.source_rows)
        return value


Fallback = Callable[[CashEvent, str], AccountChoice | None]


class AccountingEntryEngine:
    def __init__(
        self,
        base: AccountingBase,
        period: str,
        fallback: Fallback | None = None,
        memory_history: Iterable[AccountingHistoryEntry] | None = None,
    ):
        if not re.fullmatch(r"\d{4}-\d{2}", period):
            raise ValueError("Periodo deve usar YYYY-MM.")
        self.base = base
        self.period = period
        self.accounts = {account.reduced_code: account for account in base.accounts if account.reduced_code}
        bank_parents = [account.code.rsplit(".", 1)[0] + "." for account in base.accounts if "bancos conta movimento" in plain(account.description)]
        application_parents = [account.code.rsplit(".", 1)[0] + "." for account in base.accounts if "aplicacoes de curto prazo" in plain(account.description)]
        self.bank_accounts = {
            code for code, account in self.accounts.items()
            if any(account.code.startswith(prefix) for prefix in bank_parents)
        }
        self.application_accounts = {
            code for code, account in self.accounts.items()
            if any(account.code.startswith(prefix) for prefix in application_parents)
        }
        self.financial_accounts = self.bank_accounts | self.application_accounts
        def usable(row: AccountingHistoryEntry) -> bool:
            return row.debit_account in self.accounts and row.credit_account in self.accounts

        # Cadastro HIST is a classification catalog: any month may classify the competence.
        self.catalog = tuple(row for row in base.history if usable(row))
        # Confirmed competence memory is forward-only (strictly earlier months).
        self.memory = tuple(
            row for row in (memory_history or ())
            if month_of(row.date) < period and usable(row)
        )
        self.history = self.memory + self.catalog
        self.account_activity = Counter(
            code for row in self.history for code in (row.debit_account, row.credit_account)
        )
        self.external_fallback = fallback
        self.ledger_accounts = self._map_ledgers()

    def _role(self, *groups: tuple[str, ...]) -> str | None:
        candidates = [
            code for code, account in self.accounts.items()
            if all(any(term in plain(account.description) for term in group) for group in groups)
        ]
        return max(candidates, key=lambda code: self.account_activity[code], default=None)

    def _statement_account(self, statement: BankStatement) -> str | None:
        bank_words = words(statement.bank)
        ranked = sorted(
            self.bank_accounts,
            key=lambda code: (
                len(bank_words & words(self.accounts[code].description)),
                self.account_activity[code],
                -len(self.accounts[code].description),
            ),
            reverse=True,
        )
        return ranked[0] if ranked and bank_words & words(self.accounts[ranked[0]].description) else None

    @staticmethod
    def _overlap(ledger: CashLedger, statement: BankStatement) -> int:
        available = Counter((row.date, row.amount) for row in statement.transactions)
        matched = 0
        for row in ledger.entries:
            key = (row.date, signed(row))
            if available[key]:
                available[key] -= 1
                matched += 1
        return matched

    def _map_ledgers(self) -> dict[int, tuple[str | None, str | None]]:
        output: dict[int, tuple[str | None, str | None]] = {}
        remaining = set(range(len(self.base.statements)))
        for index, ledger in sorted(enumerate(self.base.ledgers), key=lambda item: -len(item[1].entries)):
            if ledger.account:
                ledger_words = words(ledger.account)
                account_text = plain(ledger.account)
                pool = self.application_accounts if any(term in account_text for term in ("cdb", "aplic", "invest")) else self.bank_accounts
                if not any(ledger_words & words(self.accounts[code].description) for code in pool):
                    pool = self.accounts.keys()
                ranked_accounts = sorted(
                    pool,
                    key=lambda code: (
                        len(ledger_words & words(self.accounts[code].description)),
                        self.account_activity[code],
                    ),
                    reverse=True,
                )
                if ranked_accounts and ledger_words & words(self.accounts[ranked_accounts[0]].description):
                    code = ranked_accounts[0]
                    output[index] = (code, ledger.account)
                    continue
            ranked = sorted(remaining, key=lambda position: self._overlap(ledger, self.base.statements[position]), reverse=True)
            if ranked and self._overlap(ledger, self.base.statements[ranked[0]]) >= max(1, len(ledger.entries) // 2):
                statement = self.base.statements[ranked[0]]
                code = self._statement_account(statement)
                output[index] = (code, statement.bank)
                remaining.remove(ranked[0])
                continue
            ledger_text = " ".join(row.notes or "" for row in ledger.entries)
            candidates = list(self.application_accounts) if any(term in plain(ledger_text) for term in ("cdb", "aplic", "invest")) else []
            ranked_accounts = sorted(
                candidates,
                key=lambda code: (
                    self.account_activity[code],
                    len(words(ledger_text) & words(self.accounts[code].description)),
                ),
                reverse=True,
            )
            code = ranked_accounts[0] if ranked_accounts else None
            output[index] = (code, self.accounts[code].description if code else ledger.account)
        return output

    def _events(self) -> list[CashEvent]:
        events = []
        for ledger_index, ledger in enumerate(self.base.ledgers):
            account, name = self.ledger_accounts[ledger_index]
            for entry in ledger.entries:
                if month_of(entry.date) == self.period and signed(entry):
                    events.append(CashEvent(
                        ledger_index=ledger_index,
                        row_number=entry.origin.row_number,
                        date=entry.date,
                        amount=signed(entry),
                        document=entry.document,
                        counterparty=entry.counterparty,
                        notes=entry.notes,
                        financial_account=account,
                        financial_name=name,
                    ))
        for statement_index, statement in enumerate(self.base.statements):
            account = self._statement_account(statement)
            for transaction in statement.transactions:
                if month_of(transaction.date) == self.period and transaction.amount:
                    events.append(CashEvent(
                        ledger_index=len(self.base.ledgers) + statement_index,
                        row_number=None,
                        date=transaction.date,
                        amount=transaction.amount,
                        document=transaction.document,
                        counterparty=transaction.counterparty,
                        notes=transaction.description,
                        financial_account=account,
                        financial_name=statement.bank,
                    ))
        return events

    @staticmethod
    def _is_transfer(event: CashEvent) -> bool:
        text = plain(event.text)
        return "transfer" in text or ("cdb" in text and any(term in text for term in ("aplic", "resgat")))

    def _transfers(self, events: list[CashEvent]) -> tuple[list[GeneratedEntry], set[int]]:
        entries: list[GeneratedEntry] = []
        used: set[int] = set()
        for left, event in enumerate(events):
            if left in used or not self._is_transfer(event) or not event.financial_account:
                continue
            right = next((
                index for index, candidate in enumerate(events[left + 1 :], start=left + 1)
                if index not in used
                and candidate.date == event.date
                and candidate.amount == -event.amount
                and candidate.financial_account
                and candidate.financial_account != event.financial_account
                and self._is_transfer(candidate)
            ), None)
            if right is None:
                continue
            other = events[right]
            inflow, outflow = (event, other) if event.amount > 0 else (other, event)
            entries.append(GeneratedEntry(
                date=event.date,
                debit_account=inflow.financial_account,
                credit_account=outflow.financial_account,
                amount=abs(event.amount),
                standard_history=None,
                complement="Transferencia entre contas",
                financial_name=f"{outflow.financial_name} -> {inflow.financial_name}",
                document=None,
                source_rows=tuple(row for row in (event.row_number, other.row_number) if row),
                status="LANCAR",
                confidence="Alta",
                reason="transferencia_espelhada",
            ))
            used.update((left, right))
        return entries, used

    def _history_score(self, event: CashEvent, row: AccountingHistoryEntry) -> float:
        left, right = words(event.text), words(row.complement)
        if not left or not right:
            return 0.0
        containment = len(left & right) / min(len(left), len(right))
        sequence = SequenceMatcher(None, " ".join(sorted(left)), " ".join(sorted(right))).ratio()
        return 0.75 * containment + 0.25 * sequence

    def _match_history(
        self,
        event: CashEvent,
        direction: str,
        rows: tuple[AccountingHistoryEntry, ...],
        reason_prefix: str,
    ) -> AccountChoice | None:
        if not event.financial_account or not rows:
            return None
        exact = [
            row for row in rows
            if (direction == "inflow" and row.debit_account == event.financial_account)
            or (direction == "outflow" and row.credit_account == event.financial_account)
        ]
        candidates = exact or [
            row for row in rows
            if (direction == "inflow" and row.debit_account in self.financial_accounts)
            or (direction == "outflow" and row.credit_account in self.financial_accounts)
        ]
        if not candidates:
            return None
        ranked = sorted(candidates, key=lambda row: self._history_score(event, row), reverse=True)
        best = ranked[0]
        score = self._history_score(event, best)
        if score < 0.50:
            return None
        counterpart = best.credit_account if direction == "inflow" else best.debit_account
        return AccountChoice(
            counterpart=counterpart,
            standard_history=best.standard_history,
            confidence="Alta" if score >= 0.70 else "Media",
            reason=f"{reason_prefix}:{score:.2f}",
        )

    def _historical_fallback(self, event: CashEvent, direction: str) -> AccountChoice | None:
        return self._match_history(event, direction, self.memory, "historico_competencia") or self._match_history(
            event, direction, self.catalog, "historico_textual"
        )

    def _semantic_choice(self, event: CashEvent, direction: str) -> AccountChoice | None:
        text = plain(event.text)
        role: str | None = None
        reason = ""
        if direction == "inflow" and any(term in text for term in ("rendimento", "rentab", "juros receb")):
            role, reason = self._role(("juros", "rendimento"), ("recebid", "receita")), "rendimento"
        elif direction == "outflow" and any(term in text for term in ("tarifa", "iof", "encargos banc", "doc/ted")):
            role, reason = self._role(("desp",), ("banc",)), "despesa_bancaria"
        elif "capitaliza" in text:
            role, reason = self._role(("capitaliza",)), "capitalizacao"
        else:
            taxes = {
                "icms": (("icm",), ("recolher",)),
                "ipi": (("ipi",), ("recolher",)),
                "inss": (("inss",), ("recolher",)),
                "fgts": (("fgts",), ("recolher",)),
                "irpj": (("renda pj", "irpj"), ("recolher",)),
                "csll": (("contribuicao social", "csll"), ("recolher",)),
                "pis": (("pis",), ("recolher",)),
                "cofins": (("cofins",), ("recolher",)),
            }
            present = [name for name in taxes if re.search(rf"\b{name}\b", text)]
            if len(present) == 1:
                role, reason = self._role(*taxes[present[0]]), f"tributo:{present[0]}"
        return AccountChoice(role, None, "Alta", reason) if role else None

    @staticmethod
    def _compound(event: CashEvent) -> bool:
        text = plain(event.text)
        tax_patterns = (
            r"\bicms\b", r"\bipi\b", r"\bpis\b", r"\bcofins\b", r"\binss\b",
            r"\birrf\b", r"\birpj\b", r"\bcsll\b|\bc[ ._-]*soc\b|contribuicao social", r"\bpcc\b",
        )
        return sum(bool(re.search(pattern, text)) for pattern in tax_patterns) > 1

    @staticmethod
    def _complement(event: CashEvent) -> str:
        party = re.sub(r"^\d{3,}-", "", event.counterparty or "").strip()
        return " - ".join(part for part in (party, event.notes, event.document) if part) or "Movimento bancario"

    def _classify(self, event: CashEvent) -> GeneratedEntry:
        direction = "inflow" if event.amount > 0 else "outflow"
        compound = self._compound(event)
        choice = None if compound else self._semantic_choice(event, direction)
        choice = choice or (None if compound else self._historical_fallback(event, direction))
        choice = choice or (None if compound or not self.external_fallback else self.external_fallback(event, direction))
        if choice is None and not compound:
            default = (
                self._role(("dupl", "cliente"), ("receber",))
                if direction == "inflow"
                else self._role(("fornecedor",))
            )
            choice = AccountChoice(default, None, "Baixa", "padrao_por_direcao")
        counterpart = choice.counterpart if choice else None
        debit = event.financial_account if direction == "inflow" else counterpart
        credit = counterpart if direction == "inflow" else event.financial_account
        review = compound or not debit or not credit or not choice or choice.confidence == "Baixa"
        return GeneratedEntry(
            date=event.date,
            debit_account=debit,
            credit_account=credit,
            amount=abs(event.amount),
            standard_history=choice.standard_history if choice else None,
            complement=self._complement(event),
            financial_name=event.financial_name,
            document=event.document,
            source_rows=tuple(row for row in (event.row_number,) if row),
            status="REVISAR RATEIO" if compound else "REVISAR CONTA" if review else "LANCAR",
            confidence="Baixa" if compound or not choice else choice.confidence,
            reason="movimento_composto_sem_memoria_de_calculo" if compound else choice.reason if choice else "sem_classificacao",
        )

    def generate(self) -> dict:
        events = self._events()
        transfers, used = self._transfers(events)
        entries = transfers + [self._classify(event) for index, event in enumerate(events) if index not in used]
        entries.sort(key=lambda row: (row.date, row.source_rows, row.amount))
        codes = {code for row in entries for code in (row.debit_account, row.credit_account) if code}
        return {
            "schema_version": "1.0",
            "period": self.period,
            "entries": [row.json() for row in entries],
            "review": [row.json() for row in entries if row.status != "LANCAR"],
            "audit": {
                "input_statements": len(self.base.statements),
                "input_ledgers": len(self.base.ledgers),
                "input_history_rows": len(self.catalog),
                "input_memory_rows": len(self.memory),
                "input_accounts": len(self.accounts),
                "input_mode": "cash_and_statement" if self.base.ledgers and self.base.statements else "cash_only" if self.base.ledgers else "statement_only",
                "cash_movements": len(events),
                "generated_entries": len(entries),
                "transfers_collapsed": len(transfers),
                "launch": sum(row.status == "LANCAR" for row in entries),
                "review_count": sum(row.status != "LANCAR" for row in entries),
                "global_fallback_count": sum(row.reason.startswith("global_tfidf:") for row in entries),
                "unknown_account_codes": sorted(codes - self.accounts.keys()),
                "ledger_accounts": {
                    str(index): {"account": account, "name": name}
                    for index, (account, name) in self.ledger_accounts.items()
                },
            },
        }


def generate_accounting_entries(
    base: AccountingBase,
    period: str,
    fallback: Fallback | None = None,
    memory_history: Iterable[AccountingHistoryEntry] | None = None,
) -> dict:
    """Rules, cadastro catalog, optional forward-only competence memory, then fallback."""
    return AccountingEntryEngine(base, period, fallback, memory_history).generate()


def render_report(result: dict) -> str:
    audit = result["audit"]
    preview = result["entries"][:20]
    lines = [
        "# Lançamentos contábeis gerados",
        "",
        f"- Período: `{result['period']}`",
        f"- Modo de entrada: `{audit['input_mode']}`",
        f"- Movimentos financeiros: {audit['cash_movements']}",
        f"- Lançamentos gerados: {audit['generated_entries']}",
        f"- Prontos para lançar: {audit['launch']}",
        f"- Para revisão: {audit['review_count']}",
        f"- Transferências espelhadas colapsadas: {audit['transfers_collapsed']}",
    ]
    if audit.get("global_model_version"):
        lines += [
            f"- Fallback global: `{audit['global_model_version']}`",
            f"- Sugestões do fallback global: {audit['global_fallback_count']}",
            f"- Threshold automático global: {audit['global_auto_threshold']:.4f}",
        ]
    lines += [
        "",
        "## Prévia",
        "",
        "| Data | Débito | Crédito | Valor | Status | Confiança | Complemento |",
        "|---|---|---|---:|---|---|---|",
    ]
    lines.extend(
        f"| {row['date']} | {row['debit_account'] or ''} | {row['credit_account'] or ''} | "
        f"{row['amount']} | {row['status']} | {row['confidence']} | {row['complement'].replace('|', '/')} |"
        for row in preview
    )
    reasons = Counter(row["reason"] for row in result["review"])
    lines += ["", "## Fila de revisão", ""]
    lines.extend(f"- {reason}: {count}" for reason, count in reasons.most_common())
    return "\n".join(lines) + "\n"


def run_cycle(
    paths: list[str | Path],
    period: str,
    output: Path,
    global_model: Path | None = None,
    global_auto_threshold: float | None = None,
) -> dict:
    imported = output / "imported"
    results = [run_pipeline(path, imported) for path in discover_files(paths)]
    base = AccountingBase.from_results(results)
    fallback = None
    if global_model:
        from lume_ingestion.global_account_model import GlobalAccountFallback

        fallback = GlobalAccountFallback(global_model, base.accounts, period, global_auto_threshold)
    generated = generate_accounting_entries(base, period, fallback)
    if fallback:
        generated["audit"]["global_model_version"] = fallback.model_version
        generated["audit"]["global_auto_threshold"] = fallback.auto_threshold
    generated["audit"]["ingestion_failures"] = [
        {"source": result.source.name if result.source else None, "errors": [error.model_dump(mode="json") for error in result.errors]}
        for result in results if not result.success
    ]
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "lancamentos.json", generated)
    (output / "report.md").write_text(render_report(generated), encoding="utf-8", newline="\n")
    return generated


def self_check() -> None:
    assert plain("Débito  BANCÁRIO") == "debito bancario"
    assert words("Pagamento NF 123 ACME LTDA") == {"acme"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera lancamentos por regras, historico local e fallback global opcional.")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--period", required=True)
    parser.add_argument("--output", type=Path, default=Path("output/accounting_entries"))
    parser.add_argument("--global-model", type=Path)
    parser.add_argument("--global-auto-threshold", type=float)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print("self-check ok")
        return
    result = run_cycle(args.paths, args.period, args.output, args.global_model, args.global_auto_threshold)
    print(json.dumps(result["audit"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
