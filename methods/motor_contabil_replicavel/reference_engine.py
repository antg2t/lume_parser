#!/usr/bin/env python3
"""Frozen, table-only reference for the accounting decision engine.

Input and output contracts are documented in README.md and data-contract.json.
The implementation deliberately mirrors the production decision order used in
the experiment: deterministic rules, local text history, local client model,
optional global semantic model, then C/D -> HIST.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "motor-contabil-replicavel.v1"
GLOBAL_SEMANTIC_SCHEMA = "chart-description-path.v1"
HISTORY_COMPLEMENT_SCHEMA = "hist-complement.v1"
CLIENT_MODEL_SCHEMA = "client-accounting-template.v3"
GLOBAL_MODEL_SCHEMA = "global-account-counterpart.v1"
PAIR_HISTORY_SCHEMA = "account-pair-to-hist.v1"
THRESHOLDS = (0.80, 0.85, 0.90, 0.95, 0.98, 1.00)


def plain(value: object) -> str:
    return re.sub(
        r"\s+", " ",
        unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower(),
    ).strip()


def normalize_text(value: object) -> str:
    """The exact normalizer used by the text-account models."""
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    replacements = (
        (r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b|\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", " documento_fiscal "),
        (r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2})\b|\b\d{4}-\d{2}-\d{2}\b", " data "),
        (r"\b(?:0?[1-9]|1[0-2])[/.-](?:\d{4}|\d{2})\b", " competencia "),
        (r"(?:r\$\s*)?\b\d+(?:\.\d{3})*,\d{2}\b|r\$\s*\d+(?:\.\d{2})?\b", " valor "),
        (r"\b((?:nf|nfe|nf-e|fat|fatura|nota)\s*[.:#-]?\s*)\d+\b", r"\1 numero "),
        (r"\b\d{4,}\b", " numero "),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return re.sub(r"\s+", " ", text).strip()


def words(value: object) -> set[str]:
    ignored = {
        "para", "com", "por", "dos", "das", "uma", "ltda", "eireli", "brasil", "pagamento",
        "recebimento", "nota", "fiscal", "fatura", "valor", "competencia", "numero", "mes",
    }
    return {word for word in re.findall(r"[a-z]{3,}", plain(value)) if word not in ignored}


def feature_text(text: object, direction: str, financial_account: str, amount: Decimal | None = None) -> str:
    if direction not in {"inflow", "outflow"}:
        raise ValueError("direction must be inflow or outflow")
    value = abs(amount) if amount is not None else None
    amount_features = "" if not value else f" valor_{int(value * 100)} ordem_{len(str(int(value)))}"
    return f"direcao_{direction} banco_{financial_account}{amount_features} {normalize_text(text)}".strip()


def event_text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(key) or "").strip() for key in ("counterparty", "notes", "document")).strip()


def month(row: dict[str, Any]) -> str:
    return str(row["date"])[:7]


def amount(value: object) -> Decimal:
    return Decimal(str(value))


def history_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip().upper()


def history_party(value: object) -> str:
    party = history_text(value)
    party = re.sub(r"^(?:\d{3,}\s*[-/]\s*)+", "", party).strip(" -/")
    return re.sub(r"S/A$", " S/A", party)


def history_detail(row: dict[str, Any], operation: str) -> str:
    party, notes = history_party(row.get("counterparty")), history_text(row.get("notes"))
    normalized = plain(notes)
    if operation in {"PAGAMENTO PIX", "RECEBIMENTO PIX"}:
        notes = re.sub(r"\bPIX\s+(?:ENVIADO|RECEBIDO|RECEBIDA)?\b", "", notes, flags=re.I).strip(" -/")
    elif operation in {"PAGAMENTO TED", "RECEBIMENTO TED"}:
        notes = re.sub(r"\bTED\s+(?:ENVIADA|ENVIADO|RECEBIDA|RECEBIDO)?\b", "", notes, flags=re.I).strip(" -/")
    elif operation == "BOLETO PAGO":
        notes = re.sub(r"\bBOLETO\s+PAGO\b", "", notes, flags=re.I).strip(" -/")
    elif operation == "TARIFA BANCARIA":
        notes = re.sub(r"TARIFA(?:\s*BANCARIA)?", "", notes, flags=re.I).strip(" -/")
    elif operation == "IOF":
        notes = re.sub(r"IOF", "", notes, flags=re.I).strip(" -/")
    elif operation == "RENDIMENTO BANCARIO":
        notes = re.sub(r"RENDIMENTO", "", notes, flags=re.I).strip(" -/")
    if normalized in {"pix enviado", "pix recebido", "pix recebida", "ted enviada", "ted recebido", "ted recebida", "boleto pago", "pagamento", "recebimento"}:
        notes = ""
    pieces = [value for value in (party, notes) if value]
    if party and notes and plain(party) == plain(notes):
        pieces = [party]
    return " - ".join(pieces)


def format_history_complement(row: dict[str, Any]) -> str:
    """Movement -> HIST Complemento. It never invents a party, document or nature."""
    direction = "inflow" if amount(row["amount"]) > 0 else "outflow"
    source = plain(event_text(row))
    if "transfer" in source:
        operation = "TRANSFERENCIA ENTRE CONTAS"
    elif "rendimento" in source or "rentab" in source:
        operation = "RENDIMENTO BANCARIO"
    elif "iof" in source:
        operation = "IOF"
    elif "tarifa" in source or "encargo" in source:
        operation = "TARIFA BANCARIA" if "tarifa" in source else "ENCARGOS BANCARIOS"
    elif "boleto" in source and direction == "outflow":
        operation = "BOLETO PAGO"
    elif "pix" in source:
        operation = "RECEBIMENTO PIX" if direction == "inflow" else "PAGAMENTO PIX"
    elif "ted" in source:
        operation = "RECEBIMENTO TED" if direction == "inflow" else "PAGAMENTO TED"
    else:
        operation = "RECEBIMENTO" if direction == "inflow" else "PAGAMENTO"
    detail, document = history_detail(row, operation), history_text(row.get("document"))
    pieces = [operation] + ([detail] if detail else [])
    if document and document not in detail:
        pieces.append(f"DOC {document}")
    return " - ".join(pieces)


def ancestor_codes(full_code: str) -> list[str]:
    parts = full_code.split(".")
    if len(parts) < 2 and re.fullmatch(r"\d{8}", full_code):
        widths, ends, total = (1, 1, 1, 2, 3), [], 0
        for width in widths:
            total += width
            ends.append(total)
        return [full_code[:end] + "0" * (len(full_code) - end) for end in ends]
    if len(parts) < 2:
        return [full_code]
    return [".".join(parts[:depth] + ["0" * len(part) for part in parts[depth:]]) for depth in range(1, len(parts) + 1)]


def semantic_catalog(accounts: list[dict[str, Any]]) -> dict[str, str]:
    descriptions = {str(row["code"]): str(row.get("description") or "") for row in accounts}
    output = {}
    for row in accounts:
        reduced = str(row.get("reduced_code") or "")
        if not reduced:
            continue
        names = [descriptions[code] for code in ancestor_codes(str(row["code"])) if code in descriptions]
        path = " > ".join(filter(None, (plain(name) for name in names))) or plain(row.get("description"))
        output[reduced] = hashlib.sha256(f"{GLOBAL_SEMANTIC_SCHEMA}\0{path}".encode()).hexdigest()
    return output


def _classifier(*, global_model: bool = False):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import SGDClassifier
    from sklearn.pipeline import FeatureUnion, Pipeline

    kwargs = {"max_features": 12_000} if global_model else {}
    char_kwargs = {"max_features": 16_000} if global_model else {}
    vectors = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, **kwargs)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, **char_kwargs)),
    ])
    return Pipeline([("tfidf", vectors), ("classifier", SGDClassifier(loss="log_loss", alpha=1e-5, max_iter=1_000, tol=1e-3, random_state=42))])


def _fit_head(rows: list[dict[str, Any]], labels: list[str]) -> dict[str, Any]:
    by_feature, by_text = defaultdict(Counter), defaultdict(Counter)
    for row, label in zip(rows, labels):
        by_feature[row["features"]][label] += 1
        no_amount = feature_text(row["text"], row["direction"], row["financial_account"])
        by_text[no_amount][label] += 1
    exact = {key: values.most_common(1)[0][0] for key, values in by_feature.items() if len(values) == 1}
    exact_text = {key: values.most_common(1)[0][0] for key, values in by_text.items() if len(values) == 1}
    support = Counter(labels)
    eligible = [(row, label) for row, label in zip(rows, labels) if support[label] >= 2]
    model = None
    if len({label for _, label in eligible}) >= 2:
        model = _classifier()
        model.fit([row["features"] for row, _ in eligible], [label for _, label in eligible])
    return {"exact": exact, "exact_text": exact_text, "model": model, "majority": support.most_common(1)[0][0]}


def _predict_head(head: dict[str, Any], rows: list[dict[str, Any]]) -> list[tuple[str, float, str]]:
    output: list[tuple[str, float, str] | None] = [None] * len(rows)
    pending = []
    for index, row in enumerate(rows):
        label = head["exact"].get(row["features"]) or head["exact_text"].get(feature_text(row["text"], row["direction"], row["financial_account"]))
        if label:
            output[index] = (label, 1.0, "exact_memory")
        else:
            pending.append(index)
    if pending and head["model"] is not None:
        scores = head["model"].predict_proba([rows[index]["features"] for index in pending])
        classes = list(head["model"].classes_)
        for index, probabilities in zip(pending, scores):
            best = max(range(len(classes)), key=lambda pos: float(probabilities[pos]))
            output[index] = (str(classes[best]), float(probabilities[best]), "client_tfidf")
    for index in pending:
        if output[index] is None:
            output[index] = (head["majority"], 0.0, "client_majority")
    return [value for value in output if value is not None]


class LocalClientModel:
    """Exact memory + local TF-IDF heads for account and HIST."""

    def __init__(self, approved_examples: Iterable[dict[str, Any]], before_period: str) -> None:
        rows = [dict(row) for row in approved_examples if month(row) < before_period and str(row.get("counterpart") or "").isdigit()]
        self.ready = bool(rows)
        self.account_head = self.history_head = None
        if not rows:
            return
        for row in rows:
            row["amount"] = amount(row["amount"]) if row.get("amount") is not None else None
            row["features"] = feature_text(row["text"], row["direction"], row["financial_account"], row["amount"])
        self.account_head = _fit_head(rows, [str(row["counterpart"]) for row in rows])
        history_rows = [row for row in rows if str(row.get("standard_history") or "")]
        self.history_head = _fit_head(history_rows, [str(row["standard_history"]) for row in history_rows]) if history_rows else None

    def predict(self, movement: dict[str, Any]) -> dict[str, Any] | None:
        if not self.ready or not movement.get("financial_account") or not normalize_text(event_text(movement)):
            return None
        probe = {
            "text": event_text(movement), "direction": "inflow" if amount(movement["amount"]) > 0 else "outflow",
            "financial_account": str(movement["financial_account"]), "amount": abs(amount(movement["amount"])),
        }
        probe["features"] = feature_text(probe["text"], probe["direction"], probe["financial_account"], probe["amount"])
        account_label, account_probability, account_reason = _predict_head(self.account_head, [probe])[0]
        if self.history_head:
            history_label, history_probability, history_reason = _predict_head(self.history_head, [probe])[0]
        else:
            history_label, history_probability, history_reason = None, None, None
        return {
            "counterpart": account_label, "standard_history": history_label,
            "combined_probability": min(account_probability, history_probability if history_probability is not None else 1.0),
            "reason": account_reason, "account_probability": account_probability,
            "account_probability_source": account_reason, "history_probability": history_probability,
            "history_probability_source": history_reason,
        }


class GlobalAccountModel:
    """Optional cross-client semantic fallback; never supplies HIST."""

    def __init__(self, global_examples: Iterable[dict[str, Any]], accounts: list[dict[str, Any]], before_period: str) -> None:
        rows = [row for row in global_examples if month(row) < before_period]
        support = Counter(str(row["semantic_key"]) for row in rows)
        rows = [row for row in rows if support[str(row["semantic_key"])] >= 2]
        self.model = None
        if len({str(row["semantic_key"]) for row in rows}) >= 2:
            self.model = _classifier(global_model=True)
            self.model.fit([f"direcao_{row['direction']} {normalize_text(row['text'])}" for row in rows], [str(row["semantic_key"]) for row in rows])
        grouped = defaultdict(list)
        for code, key in semantic_catalog(accounts).items():
            grouped[key].append(code)
        self.local_accounts = dict(grouped)

    def predict(self, movement: dict[str, Any]) -> dict[str, Any] | None:
        if self.model is None or not normalize_text(event_text(movement)):
            return None
        direction = "inflow" if amount(movement["amount"]) > 0 else "outflow"
        scores = self.model.predict_proba([f"direcao_{direction} {normalize_text(event_text(movement))}"])[0]
        classes = list(self.model.classes_)
        best = max(range(len(classes)), key=lambda pos: float(scores[pos]))
        key, probability = str(classes[best]), float(scores[best])
        matches = self.local_accounts.get(key, [])
        if len(matches) != 1:
            return None
        return {"counterpart": matches[0], "probability": probability}


class AccountPairHistoryModel:
    """Empirical local P(HIST | debit, credit), trained only before target month."""

    def __init__(self, history: Iterable[dict[str, Any]], before_period: str, known_accounts: set[str]) -> None:
        pairs: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        for row in history:
            if month(row) >= before_period:
                continue
            debit, credit, hist = str(row.get("debit_account") or ""), str(row.get("credit_account") or ""), str(row.get("standard_history") or "").strip()
            if hist and debit in known_accounts and credit in known_accounts:
                pairs[(debit, credit)][hist] += 1
        self.pairs = dict(pairs)

    def predict(self, debit: str | None, credit: str | None) -> dict[str, Any] | None:
        values = self.pairs.get((str(debit or ""), str(credit or "")))
        if not values:
            return None
        hist, frequency = values.most_common(1)[0]
        support = sum(values.values())
        return {"candidate": hist, "probability": frequency / support, "support": support, "alternatives": len(values), "stable": support >= 2 and frequency == support}


def choose_prequential_threshold(previous: list[tuple[bool, float]], target: float = 0.92, minimum: int = 20) -> float:
    """Gate used in the approved 92% policy; never inspect the current month."""
    candidates = []
    for threshold in THRESHOLDS:
        selected = [correct for correct, confidence in previous if confidence >= threshold]
        if len(selected) >= minimum and sum(selected) / len(selected) >= target:
            candidates.append(threshold)
    return min(candidates) if candidates else 1.0


@dataclass(frozen=True)
class Choice:
    counterpart: str | None
    standard_history: str | None
    confidence: str
    reason: str
    account_probability: float | None = None
    account_probability_source: str | None = None
    history_probability: float | None = None
    history_probability_source: str | None = None


class Engine:
    def __init__(self, payload: dict[str, Any]) -> None:
        if payload.get("schema_version") != SCHEMA:
            raise ValueError(f"schema_version must be {SCHEMA}")
        self.payload, self.period = payload, str(payload["target_period"])
        if not re.fullmatch(r"\d{4}-\d{2}", self.period):
            raise ValueError("target_period must use YYYY-MM")
        self.accounts = {str(row["reduced_code"]): row for row in payload["accounts"] if str(row.get("reduced_code") or "")}
        if not self.accounts:
            raise ValueError("accounts must include reduced_code")
        bank_parents = [str(row["code"]).rsplit(".", 1)[0] + "." for row in payload["accounts"] if "bancos conta movimento" in plain(row.get("description"))]
        app_parents = [str(row["code"]).rsplit(".", 1)[0] + "." for row in payload["accounts"] if "aplicacoes de curto prazo" in plain(row.get("description"))]
        self.bank_accounts = {code for code, row in self.accounts.items() if any(str(row["code"]).startswith(prefix) for prefix in bank_parents)}
        self.application_accounts = {code for code, row in self.accounts.items() if any(str(row["code"]).startswith(prefix) for prefix in app_parents)}
        self.financial_accounts = self.bank_accounts | self.application_accounts
        self.history = [row for row in payload["history"] if month(row) < self.period and str(row.get("debit_account")) in self.accounts and str(row.get("credit_account")) in self.accounts]
        self.activity = Counter(code for row in self.history for code in (str(row["debit_account"]), str(row["credit_account"])))
        self.client = LocalClientModel(payload["approved_examples"], self.period)
        self.global_model = GlobalAccountModel(payload.get("global_examples", []), payload["accounts"], self.period)
        self.client_threshold = float(payload.get("client_auto_threshold", 1.0))
        self.global_threshold = float(payload.get("global_auto_threshold", 1.0))
        self.hist_model = AccountPairHistoryModel(self.history, self.period, set(self.accounts))

    def role(self, *groups: tuple[str, ...]) -> str | None:
        candidates = [code for code, row in self.accounts.items() if all(any(term in plain(row.get("description")) for term in group) for group in groups)]
        return max(candidates, key=lambda code: self.activity[code], default=None)

    def compound(self, row: dict[str, Any]) -> bool:
        patterns = (r"\bicms\b", r"\bipi\b", r"\bpis\b", r"\bcofins\b", r"\binss\b", r"\birrf\b", r"\birpj\b", r"\bcsll\b|\bc[ ._-]*soc\b|contribuicao social", r"\bpcc\b")
        return sum(bool(re.search(pattern, plain(event_text(row)))) for pattern in patterns) > 1

    def semantic_choice(self, row: dict[str, Any], direction: str) -> Choice | None:
        text, role, reason = plain(event_text(row)), None, ""
        if direction == "inflow" and any(term in text for term in ("rendimento", "rentab", "juros receb")):
            role, reason = self.role(("juros", "rendimento"), ("recebid", "receita")), "rendimento"
        elif direction == "outflow" and any(term in text for term in ("tarifa", "iof", "encargos banc", "doc/ted")):
            role, reason = self.role(("desp",), ("banc",)), "despesa_bancaria"
        elif "capitaliza" in text:
            role, reason = self.role(("capitaliza",)), "capitalizacao"
        else:
            taxes = {"icms": (("icm",), ("recolher",)), "ipi": (("ipi",), ("recolher",)), "inss": (("inss",), ("recolher",)), "fgts": (("fgts",), ("recolher",)), "irpj": (("renda pj", "irpj"), ("recolher",)), "csll": (("contribuicao social", "csll"), ("recolher",)), "pis": (("pis",), ("recolher",)), "cofins": (("cofins",), ("recolher",))}
            present = [name for name in taxes if re.search(rf"\b{name}\b", text)]
            if len(present) == 1:
                role, reason = self.role(*taxes[present[0]]), f"tributo:{present[0]}"
        return Choice(role, None, "Alta", reason, 1.0, "deterministic_rule") if role else None

    def history_choice(self, row: dict[str, Any], direction: str) -> Choice | None:
        financial = str(row.get("financial_account") or "")
        if not financial:
            return None
        exact = [prior for prior in self.history if (direction == "inflow" and str(prior["debit_account"]) == financial) or (direction == "outflow" and str(prior["credit_account"]) == financial)]
        candidates = exact or [prior for prior in self.history if (direction == "inflow" and str(prior["debit_account"]) in self.financial_accounts) or (direction == "outflow" and str(prior["credit_account"]) in self.financial_accounts)]
        def score(prior: dict[str, Any]) -> float:
            left, right = words(event_text(row)), words(prior.get("complement"))
            if not left or not right:
                return 0.0
            containment = len(left & right) / min(len(left), len(right))
            sequence = SequenceMatcher(None, " ".join(sorted(left)), " ".join(sorted(right))).ratio()
            return 0.75 * containment + 0.25 * sequence
        if not candidates:
            return None
        best, value = max(((prior, score(prior)) for prior in candidates), key=lambda pair: pair[1])
        if value < 0.50:
            return None
        counterpart = str(best["credit_account"]) if direction == "inflow" else str(best["debit_account"])
        hist = str(best.get("standard_history") or "") or None
        return Choice(counterpart, hist, "Alta" if value >= 0.70 else "Media", f"historico_textual:{value:.2f}", value, "text_similarity", value if hist else None, "source_history_similarity" if hist else None)

    def model_choice(self, row: dict[str, Any]) -> Choice | None:
        local = self.client.predict(row)
        def local_choice(confidence: str) -> Choice:
            reason = (
                f"{local['reason']}:{local['account_probability']:.2f}"
                f"|hist_texto:{local['history_probability_source']}:{local['history_probability']:.2f}"
                if local["history_probability"] is not None
                else f"{local['reason']}:{local['account_probability']:.2f}"
            )
            return Choice(local["counterpart"], local["standard_history"], confidence, reason, local["account_probability"], local["account_probability_source"], local["history_probability"], local["history_probability_source"])
        if local and local["combined_probability"] >= self.client_threshold:
            return local_choice("Alta")
        global_prediction = self.global_model.predict(row)
        if global_prediction and global_prediction["probability"] >= self.global_threshold:
            probability = global_prediction["probability"]
            return Choice(global_prediction["counterpart"], None, "Alta", f"global_tfidf:{probability:.2f}", probability, "global_tfidf")
        if local:
            return local_choice("Baixa")
        if global_prediction:
            probability = global_prediction["probability"]
            return Choice(global_prediction["counterpart"], None, "Baixa", f"global_tfidf:{probability:.2f}", probability, "global_tfidf")
        return None

    @staticmethod
    def transfer(row: dict[str, Any]) -> bool:
        text = plain(event_text(row))
        return "transfer" in text or ("cdb" in text and any(term in text for term in ("aplic", "resgat")))

    def classify(self, row: dict[str, Any]) -> dict[str, Any]:
        direction, compound = ("inflow" if amount(row["amount"]) > 0 else "outflow"), self.compound(row)
        choice = None if compound else self.semantic_choice(row, direction)
        choice = choice or (None if compound else self.history_choice(row, direction))
        choice = choice or (None if compound else self.model_choice(row))
        if choice is None and not compound:
            default = self.role(("dupl", "cliente"), ("receber",)) if direction == "inflow" else self.role(("fornecedor",))
            choice = Choice(default, None, "Baixa", "padrao_por_direcao", 0.0, "direction_default")
        counterpart = choice.counterpart if choice else None
        debit = str(row.get("financial_account")) if direction == "inflow" and row.get("financial_account") else counterpart
        credit = counterpart if direction == "inflow" else (str(row.get("financial_account")) if row.get("financial_account") else None)
        pair = self.hist_model.predict(debit, credit)
        standard_history = choice.standard_history if choice else None
        hist_probability = choice.history_probability if choice else None
        hist_source = choice.history_probability_source if choice else None
        reason = "movimento_composto_sem_memoria_de_calculo" if compound else choice.reason if choice else "sem_classificacao"
        if pair and pair["stable"]:
            standard_history, hist_probability, hist_source = pair["candidate"], pair["probability"], "account_pair_frequency"
            reason = f"{reason}|hist_contas:{pair['support']}x"
        review = compound or not debit or not credit or not choice or choice.confidence == "Baixa"
        return {
            "id": row["id"], "date": row["date"], "debit_account": debit, "credit_account": credit,
            "amount": str(abs(amount(row["amount"]))), "standard_history": standard_history,
            "complement": format_history_complement(row), "financial_name": row.get("financial_name"),
            "document": row.get("document"), "source_rows": [row["source_row"]] if row.get("source_row") else [],
            "status": "REVISAR RATEIO" if compound else "REVISAR CONTA" if review else "LANCAR",
            "confidence": "Baixa" if compound or not choice else choice.confidence, "reason": reason,
            "account_probability": choice.account_probability if choice else None,
            "account_probability_source": choice.account_probability_source if choice else None,
            "history_probability": hist_probability, "history_probability_source": hist_source,
            "history_cd_candidate": pair["candidate"] if pair else None,
            "history_cd_probability": pair["probability"] if pair else None,
            "history_cd_support": pair["support"] if pair else None,
            "history_cd_alternatives": pair["alternatives"] if pair else None,
        }

    def generate(self) -> dict[str, Any]:
        movements = [row for row in self.payload["movements"] if month(row) == self.period and amount(row["amount"])]
        entries, used = [], set()
        for left, row in enumerate(movements):
            if left in used or not self.transfer(row) or not row.get("financial_account"):
                continue
            right = next((index for index, candidate in enumerate(movements[left + 1:], left + 1) if index not in used and candidate["date"] == row["date"] and amount(candidate["amount"]) == -amount(row["amount"]) and candidate.get("financial_account") and candidate["financial_account"] != row["financial_account"] and self.transfer(candidate)), None)
            if right is None:
                continue
            other = movements[right]
            inflow, outflow = (row, other) if amount(row["amount"]) > 0 else (other, row)
            entries.append({"id": f"{row['id']}+{other['id']}", "date": row["date"], "debit_account": inflow["financial_account"], "credit_account": outflow["financial_account"], "amount": str(abs(amount(row["amount"]))), "standard_history": None, "complement": "Transferencia entre contas", "financial_name": f"{outflow.get('financial_name') or ''} -> {inflow.get('financial_name') or ''}", "document": None, "source_rows": [value for value in (row.get("source_row"), other.get("source_row")) if value], "status": "LANCAR", "confidence": "Alta", "reason": "transferencia_espelhada", "account_probability": 1.0, "account_probability_source": "matched_transfer", "history_probability": None, "history_probability_source": None, "history_cd_candidate": None, "history_cd_probability": None, "history_cd_support": None, "history_cd_alternatives": None})
            used.update((left, right))
        entries.extend(self.classify(row) for index, row in enumerate(movements) if index not in used)
        entries.sort(key=lambda row: (row["date"], row["source_rows"], row["amount"]))
        return {"schema_version": "1.1", "contract": SCHEMA, "period": self.period, "entries": entries, "review": [row for row in entries if row["status"] != "LANCAR"], "audit": {"history_complement_schema": HISTORY_COMPLEMENT_SCHEMA, "client_model_schema": CLIENT_MODEL_SCHEMA, "global_model_schema": GLOBAL_MODEL_SCHEMA, "account_pair_history_schema": PAIR_HISTORY_SCHEMA, "prediction_probability_schema": "account-and-hist-probabilities.v1", "input_history_rows_before_period": len(self.history), "account_pair_history_pairs": len(self.hist_model.pairs), "generated_entries": len(entries), "launch": sum(row["status"] == "LANCAR" for row in entries), "review_count": sum(row["status"] != "LANCAR" for row in entries)}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate the table-based accounting decision engine.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = Engine(json.loads(args.input.read_text(encoding="utf-8"))).generate()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["audit"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
