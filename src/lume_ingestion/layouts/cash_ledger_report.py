"""Cash-ledger PDF (cash-ledger-report-v1).

Printed labels recognize the report.  They do not authorize the fixed x-bands.
Those bands are the default guess.  A saved cash recipe is applied only while
every header midpoint still sits inside its band.  Plano and extrato recipes
are ignored.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from lume_ingestion.cash_ledger import _pdf_lines, fold_text, recognize_cash_ledger_pdf
from lume_ingestion.format_registry import layout_proposal_from_extract, load_formats

FORMAT_ID = "cash-ledger-report-v1"
FAMILY = "cash_ledger"

# Same cuts normalize_cash_ledger_pdf uses.  Here they are a suggestion.
BANDS: dict[str, tuple[float, float]] = {
    "date": (0.0, 76.0),
    "issue_date": (76.0, 136.0),
    "document": (136.0, 223.0),
    "counterparty": (223.0, 465.0),
    "notes": (465.0, 650.0),
    "inflow": (650.0, 710.0),
    "outflow": (710.0, 765.0),
    "balance": (765.0, 830.0),
}

_LABELS = {
    "DATA": "date",
    "EMISSAO": "issue_date",
    "DATAEMISSAO": "issue_date",
    "DOC": "document",
    "DOCUMENTO": "document",
    "DCTO": "document",
    "CLIENTEFORNECEDOR": "counterparty",
    "CLIENTE": "counterparty",
    "FORNECEDOR": "counterparty",
    "ANOTACOES": "notes",
    "ANOTACAO": "notes",
    "OBSERVACAO": "notes",
    "OBSERVACOES": "notes",
    "ENTRADA": "inflow",
    "SAIDA": "outflow",
    "SALDO": "balance",
}


def _pages(raw: dict[str, Any]) -> list[dict[str, Any]]:
    pages = raw.get("pages")
    return [page for page in pages if isinstance(page, dict)] if isinstance(pages, list) else []


def recognize(raw: dict[str, Any]) -> bool:
    if raw.get("source_format") not in {None, "pdf"}:
        return False
    return recognize_cash_ledger_pdf(_pages(raw)) is not None


def _header_hits(raw: dict[str, Any]) -> list[dict[str, Any]]:
    best: list[dict[str, Any]] = []
    for page in _pages(raw):
        for line in _pdf_lines(page):
            hits: list[dict[str, Any]] = []
            seen: set[str] = set()
            for word in line:
                folded = fold_text(str(word.get("text") or ""))
                field = _LABELS.get(folded)
                if field is None or field in seen:
                    continue
                try:
                    x0 = float(word["x0"])
                    x1 = float(word["x1"])
                    top = float(word["top"])
                except (KeyError, TypeError, ValueError):
                    continue
                seen.add(field)
                hits.append({
                    "field": field,
                    "folded": folded,
                    "text": str(word.get("text") or ""),
                    "mid": (x0 + x1) / 2,
                    "top": top,
                    "page": page,
                })
            if len(hits) > len(best):
                best = hits
    best.sort(key=lambda hit: hit["mid"])
    return best


def _inside(hit: dict[str, Any], bands: dict[str, tuple[float, float]] | None = None) -> bool:
    left, right = (bands or BANDS)[hit["field"]]
    return left <= float(hit["mid"]) < right


def _recipe_bands(record: dict[str, Any]) -> dict[str, tuple[float, float]]:
    supplied = record.get("bands")
    if not isinstance(supplied, dict):
        return BANDS
    bands = dict(BANDS)
    for field, span in supplied.items():
        if field not in BANDS or not isinstance(span, (list, tuple)) or len(span) != 2:
            continue
        try:
            bands[field] = (float(span[0]), float(span[1]))
        except (TypeError, ValueError):
            continue
    return bands


def _cash_pdf_recipes(formats: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    chosen = load_formats() if formats is None else formats
    recipes = []
    for item in chosen:
        if item.get("family") != FAMILY:
            continue
        sources = item.get("source_formats") or []
        if item.get("id") != FORMAT_ID and "pdf" not in sources:
            continue
        recipes.append(item)
    recipes.sort(key=lambda item: (item.get("id") != FORMAT_ID, -len(item.get("headers") or [])))
    return recipes


def recipe_matches(raw: dict[str, Any], formats: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Saved cash recipe whose header labels are present and still inside the bands."""

    if not recognize(raw):
        return None
    hits = _header_hits(raw)
    if not hits:
        return None
    detected = {hit["folded"] for hit in hits}
    for record in _cash_pdf_recipes(formats):
        known = {fold_text(str(header)) for header in record.get("headers") or [] if fold_text(str(header))}
        if not known or not known <= detected:
            continue
        bands = _recipe_bands(record)
        if any(not _inside(hit, bands) for hit in hits):
            continue
        return record
    return None


def force_proposal(raw: dict[str, Any], formats: list[dict[str, Any]] | None = None) -> bool:
    """First time, or a header that drifted out of its band, needs one confirm."""

    if not recognize(raw):
        return False
    return recipe_matches(raw, formats) is None


def default_guess(raw: dict[str, Any]) -> dict[str, Any]:
    """Column proposal prefilled with the band guess, including drifted headers."""

    hits = _header_hits(raw)
    if not hits:
        proposal = layout_proposal_from_extract(raw)
        proposal["family"] = proposal.get("family") or "cash"
        proposal["format_id"] = FORMAT_ID
        return proposal
    page = hits[0]["page"]
    header_top = min(float(hit["top"]) for hit in hits)
    suggestions: dict[str, str] = {}
    confidence: dict[str, float] = {}
    headers: list[str] = []
    columns: list[int] = []
    samples: dict[str, list[str]] = {}
    outside: list[str] = []
    for index, hit in enumerate(hits, start=1):
        column = str(index)
        headers.append(hit["text"])
        columns.append(index)
        suggestions[column] = hit["field"]
        inside = _inside(hit)
        confidence[column] = 1.0 if inside else 0.55
        if not inside:
            outside.append(hit["field"])
        values: list[str] = []
        for line in _pdf_lines(page):
            if float(line[0]["top"]) <= header_top + 2:
                continue
            words = []
            for word in line:
                try:
                    mid = (float(word["x0"]) + float(word["x1"])) / 2
                except (KeyError, TypeError, ValueError):
                    continue
                if abs(mid - float(hit["mid"])) <= 40:
                    words.append(str(word.get("text") or ""))
            rendered = " ".join(part for part in words if part).strip()
            if rendered:
                values.append(rendered[:160])
            if len(values) >= 5:
                break
        samples[column] = values
    folded = [fold_text(title) for title in headers]
    return {
        "sheet": f"page-{int(page.get('page_number') or 1)}",
        "headerRow": 1,
        "headers": headers,
        "columns": columns,
        "samples": samples,
        "suggestions": suggestions,
        "confidence": confidence,
        "family": "cash",
        "format_id": FORMAT_ID,
        "outside_bands": outside,
        "fingerprint": hashlib.sha256(
            json.dumps(folded, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def apply_cash_pdf_handshake(raw: dict[str, Any]) -> None:
    """Keep a recognized cash PDF paused unless a saved recipe still matches."""

    if raw.get("source_format") != "pdf" or raw.get("document_type") != FAMILY:
        return
    recognition = raw.get("document_recognition")
    if isinstance(recognition, dict) and recognition.get("extractor") in {"format_registry", "layout_map"}:
        return
    if not force_proposal(raw):
        return
    raw["layout_proposal"] = default_guess(raw)
    raw["document_type"] = None
