from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from lume_ingestion.artifacts import read_json
from lume_ingestion.parsers.nfse_pdf import PageLayout, normalize_danfse
from lume_ingestion.pipeline import run_pipeline


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
PDFS = sorted(DOCS_ROOT.glob("Nota fiscal/pdf/*.pdf"))
GOLDENS = json.loads((Path(__file__).parent / "goldens" / "nfse_pdf.json").read_text(encoding="utf-8"))


def nested(value: dict, dotted_path: str):
    current = value
    for part in dotted_path.split("."):
        current = current[part]
    return current


@pytest.mark.integration
@pytest.mark.parametrize("pdf_path", PDFS, ids=lambda path: path.name)
def test_danfse_collection_matches_manual_goldens(pdf_path: Path, tmp_path: Path) -> None:
    result = run_pipeline(pdf_path, tmp_path / "output")

    assert result.success, result.errors
    assert result.source_format == "pdf"
    assert result.document_type == "nfse"
    assert result.classification == "textual"
    assert result.requires_ocr is False
    expected = GOLDENS[pdf_path.name]
    for field, expected_value in expected.items():
        assert nested(result.data, field) == expected_value, field
    assert result.data["service"]["description"]

    raw = read_json(result.outputs.raw_json)
    assert raw["document_recognition"]["adapter"] == "nfse-national-danfse-v2"
    assert raw["document_recognition"]["key"] == expected["key"]
    assert raw["document_type"] == "nfse"
    assert "basic" in raw["pages"][0]["text"]  # pypdf remains the diagnostic source.
    for field, evidence in result.data["provenance"].items():
        assert evidence["page_number"] == 1, field
        assert evidence["xpath"] is None, field


def test_classification_uses_printed_signals_not_path_or_filename(tmp_path: Path) -> None:
    renamed = tmp_path / "arbitrary-name.pdf"
    shutil.copyfile(PDFS[0], renamed)

    result = run_pipeline(renamed, tmp_path / "output")

    assert result.success
    assert result.document_type == "nfse"
    assert result.data["key"] == PDFS[0].stem


def test_generic_pdf_is_not_promoted_to_nfse(tmp_path: Path) -> None:
    generic = next(DOCS_ROOT.glob("Extrato/pdf/*.pdf"))

    result = run_pipeline(generic, tmp_path / "output")

    assert result.success
    assert result.document_type != "nfse"


def test_spatial_parser_tolerates_text_diagnostics_and_word_order_variants(tmp_path: Path) -> None:
    original = run_pipeline(PDFS[0], tmp_path / "output")
    raw = read_json(original.outputs.raw_json)
    variant = copy.deepcopy(raw)
    page = variant["pages"][0]
    page["text"]["basic"] = page["text"]["basic"].replace(" ", "") * 2
    page["text"]["layout"] = page["text"]["layout"].replace(" ", "") * 2
    page["words"] = list(reversed(page["words"])) + page["words"][:10]

    parsed, warnings = normalize_danfse(variant)

    assert not warnings
    assert parsed.key == original.data["key"]
    assert str(parsed.amounts.gross) == original.data["amounts"]["gross"]
    assert parsed.service.description == original.data["service"]["description"]


def test_missing_printed_required_field_is_reported_without_invention(tmp_path: Path) -> None:
    original = run_pipeline(PDFS[0], tmp_path / "output")
    variant = copy.deepcopy(read_json(original.outputs.raw_json))
    page = variant["pages"][0]
    layout = PageLayout(page)
    description = layout.find("DESCRIÇÃO DO SERVIÇO")
    municipal = layout.find("TRIBUTAÇÃO MUNICIPAL (ISSQN)", after=description or -1)
    assert description is not None and municipal is not None
    top = layout.lines[description].top
    bottom = layout.lines[municipal].top
    page["words"] = [word for word in page["words"] if not top < float(word["top"]) < bottom]

    parsed, warnings = normalize_danfse(variant)

    assert parsed.service.description is None
    assert any(warning.code == "missing_required_evidence" and warning.details["field"] == "service.description" for warning in warnings)
