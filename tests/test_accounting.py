from __future__ import annotations

from pathlib import Path

import pytest

from lume_ingestion.pipeline import run_pipeline


DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs"


@pytest.mark.integration
@pytest.mark.parametrize("name", ["Plano de Contas.xlsx", "Plano de Contas Completo_v2.xlsx"])
def test_chart_of_accounts_layouts_are_normalized(name: str, tmp_path: Path) -> None:
    result = run_pipeline(DOCS_ROOT / "Plano de conta" / name, tmp_path / "output")

    assert result.success, result.errors
    assert result.document_type == "chart_of_accounts"
    assert len(result.data["accounts"]) == 1648
    assert result.data["accounts"][0]["description"] == "Ativo"
    assert result.data["accounts"][-1]["reduced_code"] == "4001172"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("name", "count"),
    [
        ("LUFTKLIM_2026_Lctos.xlsx", 7060),
        ("MARTINE_2026_Lctos_Jan_Jul.xlsx", 3622),
        ("Vanguarda Consolidado_2026_Documento que o sistema deve produzir.xlsx", 275),
    ],
)
def test_accounting_history_layouts_are_normalized(name: str, count: int, tmp_path: Path) -> None:
    result = run_pipeline(DOCS_ROOT / "Historico contabil" / name, tmp_path / "output")

    assert result.success, result.errors
    assert result.document_type == "accounting_history"
    assert len(result.data["entries"]) == count
    assert result.data["entries"][0]["origin"]["row_number"] == 2
