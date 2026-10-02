"""Shared recipe contract for formats stored in the label registry.

Each format module binds one id.  A recipe from another family never matches,
even when the titles would otherwise look familiar.
"""

from __future__ import annotations

from typing import Any, Callable

from lume_ingestion.format_registry import (
    REUSE_MIN,
    fold_text,
    header_titles_from_extract,
    jaccard,
    layout_proposal_from_extract,
    load_formats,
)


class SeededFormat:
    def __init__(
        self,
        format_id: str,
        family: str,
        proposal_family: str | None,
        recognizer: Callable[[list[dict[str, Any]]], dict[str, Any] | None] | None = None,
    ) -> None:
        self.format_id = format_id
        self.family = family
        self.proposal_family = proposal_family
        self._recognizer = recognizer

    def _formats(self, formats: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        return load_formats() if formats is None else formats

    def _record(self, formats: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        for item in self._formats(formats):
            if item.get("id") == self.format_id and item.get("family") == self.family:
                return item
        return None

    def recognize(self, raw: dict[str, Any]) -> bool:
        if self._recognizer is not None:
            pages = raw.get("pages") if isinstance(raw.get("pages"), list) else []
            return self._recognizer(pages) is not None
        record = self._record(None)
        if record is None:
            return False
        known = {fold_text(header) for header in record.get("headers") or [] if fold_text(str(header))}
        incoming = set(header_titles_from_extract(raw))
        return jaccard(incoming, known) >= REUSE_MIN

    def default_guess(self, raw: dict[str, Any]) -> dict[str, Any]:
        proposal = layout_proposal_from_extract(raw)
        if self.proposal_family and not proposal.get("family"):
            proposal["family"] = self.proposal_family
        proposal["format_id"] = self.format_id
        return proposal

    def recipe_matches(self, raw: dict[str, Any], formats: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        record = self._record(formats)
        if record is None or record.get("family") != self.family or not self.recognize(raw):
            return None
        if self._recognizer is not None:
            # Printed extrato adapters keep their own column bands.  The saved
            # recipe is this format's record, never a cash or plano recipe.
            return record
        known = {fold_text(header) for header in record.get("headers") or [] if fold_text(str(header))}
        incoming = set(header_titles_from_extract(raw))
        if jaccard(incoming, known) < REUSE_MIN:
            return None
        return record

    def force_proposal(self, raw: dict[str, Any], formats: list[dict[str, Any]] | None = None) -> bool:
        # Title-mapped spreadsheets already pause when the required aliases do
        # not close.  Forcing another proposal here would ask twice.
        del raw, formats
        return False
