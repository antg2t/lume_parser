"""One entry for every file the user uploads.

A known format must not skip the person, and an unknown file must not die
at the door.  The path is the same for plano, histórico, extrato and cash:
enter, guess a column map, and ask only for the gap.  A saved recipe is
used only when it still matches its own kind.  Fixed bands and pinned
column indexes are a guess; when they drift, the journey pauses instead
of pretending the read was right.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from lume_ingestion import format_registry
from lume_ingestion.format_registry import fold_text, layout_proposal_from_extract
from lume_ingestion.layouts.cash_ledger_report import apply_cash_pdf_handshake

_QUESTIONS = {
    "kind": "Que tipo de arquivo é este?",
    "text": "Não consigo ler o texto.",
    "date": "Qual coluna é a data?",
    "amount": "Qual coluna é o valor?",
    "description": "Qual coluna é o histórico?",
    "inflow": "Qual coluna é a entrada?",
    "outflow": "Qual coluna é a saída?",
    "balance": "Qual coluna é o saldo?",
    "credit": "Qual coluna é o crédito?",
    "debit": "Qual coluna é o débito?",
    "code": "Qual coluna é a conta?",
    "debit_account": "Qual coluna é a conta de débito?",
    "credit_account": "Qual coluna é a conta de crédito?",
    "complement": "Qual coluna é o complemento?",
}

# Required fields per kind.  The shortest gap wins, so a file that already
# has date and histórico is asked only for the amount — not for every column.
_REQUIRED: dict[str, tuple[set[str], ...]] = {
    "cash": ({"date", "inflow", "outflow", "balance"},),
    "bank": ({"date", "description", "amount"}, {"date", "description", "credit", "debit"}),
    "chart": ({"code", "description"},),
    "history": ({"date", "debit_account", "credit_account", "amount", "complement"},),
}

_HEADER_FIELDS = {
    "CONTA": "code",
    "CODIGO": "code",
    "DESCRICAO": "description",
    "COMPLEMENTO": "complement",
    "DEBITO": "debit_account",
    "CREDITO": "credit_account",
}


def pause_message(proposal: dict[str, Any]) -> str:
    """One question, only for what is still missing."""

    if proposal.get("gap") == "unreadable_text":
        return _QUESTIONS["text"]
    missing = [str(item) for item in proposal.get("missing") or []]
    if not missing:
        return "Os titulos foram lidos, mas ainda precisam ser associados."
    if missing == ["kind"]:
        return _QUESTIONS["kind"]
    if missing == ["text"]:
        return _QUESTIONS["text"]
    questions = [_QUESTIONS.get(item, item) for item in missing]
    if len(questions) == 1:
        return questions[0]
    return "Falta só isto: " + " ".join(questions)


def finish_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    """Attach the guessed kind and the list of required fields still unmapped."""

    finished = dict(proposal)
    suggestions = {str(key): str(value) for key, value in (finished.get("suggestions") or {}).items()}
    headers = [str(item) for item in finished.get("headers") or []]
    columns = [int(item) for item in finished.get("columns") or []]
    if len(columns) != len(headers):
        columns = list(range(1, len(headers) + 1))
        finished["columns"] = columns
    folded = [fold_text(header) for header in headers]
    balance_titles = format_registry.ALIASES["balance"]
    has_balance = "balance" in suggestions.values() or any(name in balance_titles for name in folded)
    for index, name in enumerate(folded):
        field = _HEADER_FIELDS.get(name)
        if field in {"credit_account", "debit_account"} and has_balance:
            continue
        if field and field not in suggestions.values():
            suggestions[str(columns[index])] = field
    # Extrato keeps Crédito/Débito as the movement. Histórico, which has no
    # saldo column, still reads those titles as accounts.
    if not has_balance:
        if "debit" in suggestions.values() and "debit_account" not in suggestions.values():
            if "COMPLEMENTO" in folded or "SALDO" not in folded:
                for key, value in list(suggestions.items()):
                    if value == "debit":
                        suggestions[key] = "debit_account"
                        break
        if "credit" in suggestions.values() and "credit_account" not in suggestions.values():
            if "COMPLEMENTO" in folded or "SALDO" not in folded:
                for key, value in list(suggestions.items()):
                    if value == "credit":
                        suggestions[key] = "credit_account"
                        break
    finished["suggestions"] = suggestions
    mapped = set(suggestions.values())
    family = finished.get("family") if finished.get("family") in _REQUIRED else _guess_family(mapped, folded)
    finished["family"] = family
    finished["missing"] = _missing(family, mapped)
    finished["question"] = pause_message(finished)
    if "fingerprint" not in finished:
        finished["fingerprint"] = hashlib.sha256(
            json.dumps(folded, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest()
    return finished


def apply_entry(raw: dict[str, Any]) -> None:
    """Let the file in.  Pause with a prefilled map unless its own recipe still fits."""

    if not isinstance(raw, dict):
        return
    recognition = raw.get("document_recognition")
    if isinstance(recognition, dict) and recognition.get("extractor") == "layout_map":
        return
    apply_cash_pdf_handshake(raw)
    if _cash_pause(raw):
        raw["layout_proposal"] = finish_proposal(raw["layout_proposal"])
        return
    shifted = _pinned_shift(raw)
    if shifted:
        proposal = _proposal_for(raw) or finish_proposal(layout_proposal_from_extract(raw))
        proposal["shifted"] = shifted
        proposal["outside_bands"] = list(dict.fromkeys([*(proposal.get("outside_bands") or []), *shifted]))
        raw["layout_proposal"] = proposal
        raw["document_type"] = None
        raw.pop("document_recognition", None)
        return
    if _trusted(raw):
        return
    proposal = _proposal_for(raw)
    if proposal and len(proposal.get("headers") or []) >= 2:
        raw["layout_proposal"] = proposal
        raw["document_type"] = None
        if not (isinstance(recognition, dict) and recognition.get("extractor") == "layout_map"):
            raw.pop("document_recognition", None)
        return
    if _unreadable(raw):
        raw["document_type"] = None
        raw.pop("document_recognition", None)
        raw["layout_proposal"] = _unreadable_proposal()


def _cash_pause(raw: dict[str, Any]) -> bool:
    proposal = raw.get("layout_proposal")
    return (
        raw.get("source_format") == "pdf"
        and raw.get("document_type") is None
        and isinstance(proposal, dict)
        and proposal.get("format_id") == "cash-ledger-report-v1"
    )


def _trusted(raw: dict[str, Any]) -> bool:
    """A recipe, or a built-in adapter of the same kind, already closed the file."""

    kind = raw.get("document_type")
    if kind == "nfse":
        return True
    if kind in {"chart_of_accounts", "accounting_history"} and raw.get("source_format") in {"xlsx", "xls", "csv"}:
        return True
    recognition = raw.get("document_recognition")
    if kind in {"bank_statement", "cash_ledger"} and isinstance(recognition, dict):
        if recognition.get("extractor") in {"format_registry", "layout_map"}:
            return True
        if recognition.get("adapter") or recognition.get("layout"):
            return True
    if kind == "cash_ledger" and raw.get("source_format") == "pdf":
        # The cash handshake already checked the saved recipe and the bands.
        return True
    if kind == "bank_statement" and raw.get("source_format") == "pdf" and isinstance(recognition, dict):
        return True
    return False


def _pinned_shift(raw: dict[str, Any]) -> list[str] | None:
    """Saved column indexes of this kind that no longer sit on the same header."""

    recognition = raw.get("document_recognition")
    if not isinstance(recognition, dict) or recognition.get("extractor") not in {"format_registry", "layout_map"}:
        return None
    detected = {}
    for field, column in (recognition.get("columns") or {}).items():
        try:
            detected[str(field)] = int(column)
        except (TypeError, ValueError):
            continue
    adapter = str(recognition.get("adapter") or "")
    kind = raw.get("document_type")
    for item in format_registry.load_formats():
        if str(item.get("id") or "") != adapter or item.get("family") != kind:
            continue
        pinned = item.get("columns")
        if not isinstance(pinned, dict):
            return None
        shifted: list[str] = []
        for field, column in pinned.items():
            try:
                expected = int(column)
            except (TypeError, ValueError):
                continue
            actual = detected.get(str(field))
            if actual is not None and actual != expected:
                shifted.append(str(field))
        return shifted or None
    return None


def _proposal_for(raw: dict[str, Any]) -> dict[str, Any] | None:
    existing = raw.get("layout_proposal")
    if isinstance(existing, dict) and len(existing.get("headers") or []) >= 2:
        return finish_proposal(existing)
    proposal = layout_proposal_from_extract(raw)
    if len(proposal.get("headers") or []) >= 2:
        return finish_proposal(proposal)
    sheets = _sheets_from_words(raw)
    if not sheets:
        return None
    synthesized = layout_proposal_from_extract({"source_format": raw.get("source_format"), "workbook": {"sheets": sheets}})
    if len(synthesized.get("headers") or []) < 2:
        return None
    return finish_proposal(synthesized)


def _sheets_from_words(raw: dict[str, Any]) -> list[dict[str, Any]]:
    pages = raw.get("pages") if isinstance(raw.get("pages"), list) else []
    sheets: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        lines: dict[int, list[dict[str, Any]]] = {}
        for word in page.get("words") or []:
            if not isinstance(word, dict) or not word.get("text"):
                continue
            try:
                top = int(round(float(word["top"]) / 3.0) * 3)
                x0 = float(word["x0"])
            except (KeyError, TypeError, ValueError):
                continue
            lines.setdefault(top, []).append(word)
        ordered = [sorted(lines[top], key=lambda item: float(item["x0"])) for top in sorted(lines) if len(lines[top]) >= 2]
        if len(ordered) < 2:
            continue
        header_mids = [_mid(word) for word in ordered[0]]
        aligned = sum(
            any(abs(_mid(word) - mid) <= 40 for mid in header_mids)
            for word in ordered[1]
        )
        if aligned < 2:
            continue
        rows = []
        for row_number, line in enumerate(ordered, start=1):
            cells = [
                {
                    "column": column,
                    "value": str(word.get("text") or ""),
                    "formula": None,
                    "calculated_value": None,
                }
                for column, word in enumerate(line, start=1)
                if str(word.get("text") or "").strip()
            ]
            if cells:
                rows.append({"row_number": row_number, "cells": cells})
        if rows:
            sheets.append({"name": f"page-{int(page.get('page_number') or 1)}", "rows": rows})
    return sheets


def _mid(word: dict[str, Any]) -> float:
    return (float(word["x0"]) + float(word["x1"])) / 2


def _guess_family(mapped: set[str], folded: list[str]) -> str | None:
    folded_set = set(folded)
    if {"date", "inflow", "outflow"} <= mapped or {"date", "inflow", "outflow", "balance"} <= mapped:
        return "cash"
    if {"code", "description"} <= mapped and not ({"amount", "inflow", "outflow"} & mapped):
        return "chart"
    history_headers = {"DATA", "DEBITO", "CREDITO", "VALOR", "COMPLEMENTO"} <= folded_set and "SALDO" not in folded_set
    if history_headers or {"date", "debit_account", "credit_account", "amount", "complement"} <= mapped:
        return "history"
    if {"date", "description"} <= mapped or {"date", "credit", "debit"} <= mapped:
        return "bank"
    return None


def _missing(family: str | None, mapped: set[str]) -> list[str]:
    if family not in _REQUIRED:
        return ["kind"]
    best = min(
        _REQUIRED[family],
        key=lambda required: (len(required - mapped), -len(required & mapped), sorted(required)),
    )
    return sorted(best - mapped)


def _unreadable(raw: dict[str, Any]) -> bool:
    if raw.get("source_format") != "pdf" or raw.get("document_type") not in {None, ""}:
        return False
    pages = raw.get("pages")
    if not isinstance(pages, list) or not pages:
        return False
    for page in pages:
        if not isinstance(page, dict):
            continue
        metrics = page.get("metrics") if isinstance(page.get("metrics"), dict) else {}
        if metrics.get("has_useful_text"):
            return False
        text = page.get("text") if isinstance(page.get("text"), dict) else {}
        blob = f"{text.get('basic') or ''} {text.get('layout') or ''}".strip()
        if len(blob) >= 20:
            return False
        words = page.get("words") if isinstance(page.get("words"), list) else []
        if len(words) >= 4:
            return False
    return True


def _unreadable_proposal() -> dict[str, Any]:
    proposal = {
        "sheet": "",
        "headerRow": 0,
        "headers": [],
        "columns": [],
        "samples": {},
        "suggestions": {},
        "confidence": {},
        "family": None,
        "missing": ["text"],
        "gap": "unreadable_text",
        "question": _QUESTIONS["text"],
    }
    proposal["fingerprint"] = hashlib.sha256(b"[]").hexdigest()
    return proposal
