"""RAW extraction for delimited text tables."""

from __future__ import annotations

import csv
from pathlib import Path
from time import perf_counter
from typing import Any

from openpyxl.utils import get_column_letter

from lume_ingestion.errors import IngestionFailure
from lume_ingestion.format_registry import layout_proposal_from_extract, match_extract
from lume_ingestion.models import SourceFile, Warning


class CsvParser:
    name = "csv-stdlib"
    version = "0.1.0"

    def extract(
        self,
        path: Path,
        source: SourceFile,
        warnings: list[Warning],
        limits: Any = None,
    ) -> dict[str, Any]:
        del limits
        started = perf_counter()
        payload = path.read_bytes()
        text = None
        encoding_used = ""
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                text = payload.decode(encoding)
                encoding_used = encoding
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise IngestionFailure("invalid_csv", "O CSV nao possui encoding suportado.")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=";,\t,")
            parsed = list(csv.reader(text.splitlines(), dialect))
        except csv.Error as exc:
            raise IngestionFailure("invalid_csv", "O CSV nao possui separador consistente.", reason=str(exc)) from exc
        rows: list[dict[str, Any]] = []
        max_column = 0
        for row_number, values in enumerate(parsed, 1):
            cells = []
            for column, value in enumerate(values, 1):
                rendered = value.strip()
                if not rendered:
                    continue
                max_column = max(max_column, column)
                cells.append({
                    "coordinate": f"{get_column_letter(column)}{row_number}",
                    "column": column,
                    "value": rendered,
                    "formula": None,
                    "calculated_value": None,
                })
            if cells:
                rows.append({"row_number": row_number, "cells": cells})
        if not rows:
            raise IngestionFailure("invalid_csv", "O CSV nao possui linhas utilizaveis.")
        raw = {
            "schema_version": "1.0",
            "source": source.model_dump(mode="json"),
            "source_format": "csv",
            "document_type": None,
            "parser": self.name,
            "parser_version": self.version,
            "extraction_duration_ms": round((perf_counter() - started) * 1000),
            "workbook": {"sheet_count": 1, "sheets": [{
                "name": "CSV",
                "dimensions": {"max_row": len(parsed), "max_column": max_column},
                "merged_cells": [],
                "rows": rows,
            }], "formula_count": 0},
            "csv": {"delimiter": dialect.delimiter, "encoding": encoding_used},
            "warnings": [warning.model_dump(mode="json") for warning in warnings],
            "errors": [],
        }
        matched = match_extract(raw, source.name)
        if matched:
            raw["document_type"] = matched.family
            raw["document_recognition"] = matched.as_recognition()
        else:
            raw["layout_proposal"] = layout_proposal_from_extract(raw)
        return raw
