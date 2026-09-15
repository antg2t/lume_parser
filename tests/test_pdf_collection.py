from __future__ import annotations

from pathlib import Path

import pytest

from lume_ingestion.artifacts import read_json
from lume_ingestion.pipeline import run_pipeline, semantic_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
PDFS = sorted(
    [
        *DOCS_ROOT.glob("Nota fiscal/pdf/*.pdf"),
        *DOCS_ROOT.glob("Controle de caixa/pdf/*.pdf"),
        *DOCS_ROOT.glob("Extrato/pdf/*.pdf"),
    ]
)
NFSE_PDFS = sorted(DOCS_ROOT.glob("Nota fiscal/pdf/*.pdf"))
CASH_LEDGER_PDFS = sorted(DOCS_ROOT.glob("Controle de caixa/pdf/*.pdf"))
BANK_STATEMENT_PDFS = sorted(DOCS_ROOT.glob("Extrato/pdf/*.pdf"))


@pytest.mark.integration
@pytest.mark.parametrize("pdf_path", PDFS, ids=lambda path: path.name)
def test_current_pdf_collection_is_textual(pdf_path: Path, tmp_path: Path) -> None:
    result = run_pipeline(pdf_path, tmp_path / "output")

    assert result.success, result.errors
    assert result.classification == "textual"
    assert result.requires_ocr is False
    assert result.outputs.raw_json and Path(result.outputs.raw_json).is_file()
    assert result.outputs.normalized_json and Path(result.outputs.normalized_json).is_file()
    assert result.outputs.result_json and Path(result.outputs.result_json).is_file()
    if pdf_path in NFSE_PDFS:
        assert result.document_type == "nfse"
        assert result.data["key"] == pdf_path.stem
        assert len(result.outputs.page_texts) == 1
    elif pdf_path in CASH_LEDGER_PDFS:
        assert result.document_type == "cash_ledger"
        assert sum(len(ledger["entries"]) for ledger in result.data["ledgers"]) == 261
        assert len(result.outputs.page_texts) == 12
    elif pdf_path in BANK_STATEMENT_PDFS:
        assert result.document_type == "bank_statement"
        expected = {
            "Itaú": (176, 22, 6),
            "Bradesco": (88, 16, 3),
        }[result.data["bank"]]
        assert (
            len(result.data["transactions"]),
            len(result.data["daily_balances"]),
            len(result.outputs.page_texts),
        ) == expected
    else:
        assert result.document_type is None
        assert result.data["page_count"] == result.data["useful_page_count"]
        assert len(result.outputs.page_texts) == result.data["page_count"]
    assert all(Path(path).read_text(encoding="utf-8").strip() for path in result.outputs.page_texts)


@pytest.mark.integration
def test_same_input_has_same_semantic_output(tmp_path: Path) -> None:
    if not PDFS:
        pytest.skip("O acervo local de PDFs nao esta disponivel.")
    pdf_path = PDFS[0]

    first = run_pipeline(pdf_path, tmp_path / "output")
    first_raw = read_json(first.outputs.raw_json)
    first_result = read_json(first.outputs.result_json)
    second = run_pipeline(pdf_path, tmp_path / "output")
    second_raw = read_json(second.outputs.raw_json)
    second_result = read_json(second.outputs.result_json)

    assert semantic_payload(first_raw) == semantic_payload(second_raw)
    assert semantic_payload(first_result) == semantic_payload(second_result)


def test_collection_keeps_original_r00_baseline() -> None:
    # O acervo local pode crescer; a regressao garante que a base original
    # da R00 nao desapareceu sem tornar novas fixtures uma falha.
    assert len(PDFS) >= 19
