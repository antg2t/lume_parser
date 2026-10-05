"""Paused extrato keeps Crédito and Débito as the movement."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from lume_ingestion.pipeline import run_pipeline


def test_saldo_title_keeps_credito_as_bank_amount(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LUME_FORMAT_REGISTRY", str(tmp_path / "learned.json"))
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Data", "Lançamentoss", "Dcto.", "Crédito (R$)", "Débito (R$)", "Saldo (R$)"])
    sheet.append(["02/03/2026", "TED CLIENTE", "1", 90, None, 90])
    sheet.append(["03/03/2026", "TED FORNECEDOR", "2", None, 180, -90])
    path = tmp_path / "lancamentoss.xlsx"
    workbook.save(path)

    result = run_pipeline(path, tmp_path / "out")

    assert not result.success
    layout = result.errors[0].details["layout"]
    suggested = set(layout["suggestions"].values())
    assert "credit" in suggested
    assert "debit" in suggested
    assert "credit_account" not in suggested
    assert "debit_account" not in suggested
    assert result.errors[0].message != "Qual coluna é o valor?"
