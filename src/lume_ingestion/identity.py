"""Shared extraction of CNPJ/CPF and document numbers from printed fields.

Adapters keep these values on dedicated payload keys. They never invent an
identifier and they never concatenate one into ``text``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def fold_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    without_marks = "".join(character for character in decomposed if unicodedata.category(character) != "Mn")
    return re.sub(r"[^A-Z0-9]", "", without_marks.upper())


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).replace("\u00a0", " ").replace("\xad", "-")
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered or None

_FORMATTED_TAX_ID = re.compile(
    r"\b(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2})\b"
)
_LABELED_DIGITS = re.compile(r"(?i)\b(?:CNPJ|CPF)\s*:?\s*(\d{11,14})\b")
_COMPACT_CNPJ = re.compile(r"\b(\d{14})\b")
_COMPACT_CPF = re.compile(r"\b(\d{11})\b")
_PHONE_HINT = re.compile(r"\(?\d{2}\)?\s*9\d{4}-?\d{4}")
_PREFIXED_PARTY = re.compile(r"(?i)^\s*(REMET\.?|DEST\.?|DESTINO\.?|BENEF\.?)\s*")
_LABELED_DOCUMENT = re.compile(
    r"(?i)(?:^|\s)(?:NF|N[ºO°]|DOC(?:UMENTO)?)\s*\.?\s*(\d{3,12})\s*$"
)
_TRAILING_DIGITS = re.compile(r"(?<![A-Z0-9])(\d{4,12})$")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_MOVEMENT_HEAD = re.compile(
    r"^(PIX|BOLETO|TED|DOC|PAGAMENTOS|TARIFA|IOF|SISPAG|DEBITO|CREDITO|RESGATE|APLIC)\b",
    re.I,
)
_GENERIC_PARTY = re.compile(
    r"^(PIX(\s+(ENVIADO|TRANSF|RECEBIDO))?|TED|DOC|TARIFA|TRANSFERÊNCIA( AVULSA)?|SALDO ANTERIOR)$",
    re.I,
)


def format_tax_id(digits: str) -> str:
    compact = re.sub(r"\D", "", digits)
    if len(compact) == 14:
        return f"{compact[:2]}.{compact[2:5]}.{compact[5:8]}/{compact[8:12]}-{compact[12:]}"
    if len(compact) == 11:
        return f"{compact[:3]}.{compact[3:6]}.{compact[6:9]}-{compact[9:]}"
    return compact


def _extract_tax_id_from(rendered: str) -> str | None:
    formatted = _FORMATTED_TAX_ID.search(rendered)
    if formatted:
        return formatted.group(1)
    labeled = _LABELED_DIGITS.search(rendered)
    if labeled:
        return format_tax_id(labeled.group(1))
    folded = fold_text(rendered)
    labeled_fold = re.search(r"(?:CNPJ|CPF)(\d{11,14})", folded)
    if labeled_fold:
        return format_tax_id(labeled_fold.group(1))
    compact_cnpj = _COMPACT_CNPJ.search(rendered)
    if compact_cnpj:
        return format_tax_id(compact_cnpj.group(1))
    for token in re.findall(r"\b\d{11,14}\b", rendered):
        if len(token) == 14:
            return format_tax_id(token)
        if not _PHONE_HINT.search(rendered):
            return format_tax_id(token)
    digits_only = re.sub(r"\D", "", rendered)
    if len(digits_only) == 14:
        return format_tax_id(digits_only)
    stripped = re.sub(r"[\s.\-]", "", rendered)
    if len(digits_only) == 11 and re.fullmatch(r"\d{11}", stripped or ""):
        if _PHONE_HINT.search(rendered):
            return None
        return format_tax_id(digits_only)
    return None


def extract_tax_id(*parts: Any) -> str | None:
    """Return a CNPJ/CPF printed in the parts, or None.

    Formatted and labeled values win. A bare 14-digit run is a CNPJ. A bare
    11-digit run is a CPF only when it is the whole field (cash-ledger notes),
    never a phone or CEP.
    """

    cleaned = [clean_text(part) for part in parts]
    for rendered in cleaned:
        if rendered:
            found = _extract_tax_id_from(rendered)
            if found:
                return found
    joined = clean_text(" ".join(part for part in cleaned if part))
    return _extract_tax_id_from(joined) if joined else None


def split_document(description: str | None) -> tuple[str | None, str | None]:
    """Peel a printed document number off the end of a description.

    Leaves the movement text intact. Years, tax ids and amounts stay put.
    """

    rendered = clean_text(description)
    if not rendered:
        return None, None
    labeled = _LABELED_DOCUMENT.search(rendered)
    if labeled:
        rest = clean_text(rendered[: labeled.start()] + rendered[labeled.end() :])
        return rest, labeled.group(1)
    trailing = _TRAILING_DIGITS.search(rendered)
    if trailing:
        token = trailing.group(1)
        if _YEAR.fullmatch(token) or len(token) in {11, 14}:
            return rendered, None
        rest = clean_text(rendered[: trailing.start()])
        if rest and _MOVEMENT_HEAD.match(rest):
            return rest, token
    return rendered, None


def strip_tax_id(value: str | None, tax_id: str | None) -> str | None:
    if not value or not tax_id:
        return value
    digits = re.sub(r"\D", "", tax_id)
    stripped = re.sub(re.escape(tax_id), " ", value)
    if digits:
        stripped = re.sub(re.escape(digits), " ", stripped)
    return clean_text(stripped)


def party_from_continuation(line: str | None) -> str | None:
    """Company printed on the line after a Bradesco movement (REMET./DEST.)."""

    rendered = clean_text(line)
    if not rendered:
        return None
    stripped = _PREFIXED_PARTY.sub("", rendered).strip(" .-")
    candidate = clean_text(stripped) or rendered
    if _GENERIC_PARTY.fullmatch(candidate or ""):
        return None
    if extract_tax_id(candidate) and not _PREFIXED_PARTY.search(rendered):
        # A continuation that is only a tax id is not a company name.
        if re.fullmatch(r"[\d.\-/]+", candidate.replace(" ", "")):
            return None
    if _PREFIXED_PARTY.search(rendered):
        return candidate
    folded = fold_text(candidate)
    if any(token in folded for token in ("LTDA", "EIRELI", "S/A", "SA", "ME", "EPP")):
        return candidate
    return None
