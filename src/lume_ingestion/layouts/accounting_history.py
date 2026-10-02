"""Historico contabil.  A cash recipe cannot close this contract."""

from __future__ import annotations

from typing import Any

from lume_ingestion.accounting import recognize_xlsx_document_type
from lume_ingestion.format_registry import layout_proposal_from_extract

FAMILY = "accounting_history"
FORMAT_ID = "accounting-history"


def _sheets(raw: dict[str, Any]) -> list[dict[str, Any]]:
    workbook = raw.get("workbook") if isinstance(raw.get("workbook"), dict) else {}
    sheets = workbook.get("sheets") if isinstance(workbook, dict) else None
    return [sheet for sheet in sheets or [] if isinstance(sheet, dict)]


def recognize(raw: dict[str, Any]) -> bool:
    return recognize_xlsx_document_type(_sheets(raw)) == FAMILY


def default_guess(raw: dict[str, Any]) -> dict[str, Any]:
    proposal = layout_proposal_from_extract(raw)
    proposal["format_id"] = FORMAT_ID
    proposal["family"] = None
    return proposal


def recipe_matches(raw: dict[str, Any], formats: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    if not recognize(raw):
        return None
    for item in formats or []:
        if item.get("family") == FAMILY:
            return item
    return None


def force_proposal(raw: dict[str, Any], formats: list[dict[str, Any]] | None = None) -> bool:
    del raw, formats
    return False
