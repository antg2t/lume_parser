from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from lume_ingestion.format_registry import header_titles_from_extract, layout_proposal_from_extract, match_extract, match_from_layout_map
from lume_ingestion.pipeline import run_pipeline


def _xlsx(tmp_path: Path, headers: list[str], rows: list[list[object]], name: str) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    path = tmp_path / name
    workbook.save(path)
    return path


def test_column_titles_reuse_known_itau_current_account(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LUME_FORMAT_REGISTRY", str(tmp_path / "learned.json"))
    path = _xlsx(
        tmp_path,
        ["Data", "Lançamento", "Valor (R$)", "Saldo (R$)"],
        [["02/01/2026", "PIX ENVIADO FORNECEDOR", -20, 80], ["02/01/2026", "S A L D O", None, 80]],
        "itau.xlsx",
    )
    result = run_pipeline(path, tmp_path / "out")
    assert result.success, result.errors
    assert result.document_type == "bank_statement"
    assert result.data["transactions"][0]["amount"] == "-20.00"


def test_new_exact_header_titles_parse_without_minting_host_global_json(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "learned.json"
    monkeypatch.setenv("LUME_FORMAT_REGISTRY", str(store))
    path = _xlsx(
        tmp_path,
        ["Data", "Histórico", "Valor"],
        [["03/01/2026", "TARIFA PACOTE", -12.5]],
        "novo.xlsx",
    )
    first = run_pipeline(path, tmp_path / "out1")
    assert first.success, first.errors
    assert first.data["transactions"][0]["description"] == "TARIFA PACOTE"
    assert not store.exists()

    second = run_pipeline(path, tmp_path / "out2")
    assert second.success, second.errors
    raw = (tmp_path / "out2" / "raw.json").read_text(encoding="utf-8") if (tmp_path / "out2" / "raw.json").exists() else ""
    # Second pass still extracts; minted flag may be false after remember.
    assert second.data["transactions"][0]["amount"] == "-12.50"


def test_pdf_table_labels_match_the_same_registry_as_xlsx() -> None:
    raw = {
        "source_format": "pdf",
        "pages": [{
            "page_number": 1,
            "tables": [[["Data", "Lançamento", "Valor (R$)", "Saldo (R$)"], ["02/01/2026", "PIX ITAU", "-10,00", "90,00"]]],
            "text": {"layout": "Extrato de Conta Corrente Itaú"},
        }],
    }
    titles = header_titles_from_extract(raw)
    assert "DATA" in titles
    matched = match_extract(raw, "extrato-itau.pdf")
    assert matched is not None
    assert matched.family == "bank_statement"
    assert "date" in matched.columns and "amount" in matched.columns


def test_unknown_pdf_table_proposes_samples_and_accepts_the_confirmed_map() -> None:
    raw = {
        "source_format": "pdf",
        "pages": [{
            "page_number": 1,
            "tables": [[
                ["Dt movimento", "Desc", "Vl"],
                ["13/02/2026", "PIX UNIVERSO", "-150,09"],
            ]],
            "text": {"layout": "Movimentos"},
        }],
    }

    proposal = layout_proposal_from_extract(raw)
    assert proposal["headers"] == ["Dt movimento", "Desc", "Vl"]
    assert proposal["samples"]["2"] == ["PIX UNIVERSO"]
    matched = match_from_layout_map(raw, {
        "family": "bank",
        "sheet": "page-1-table-0",
        "headerRow": 1,
        "columns": {"date": 1, "description": 2, "amount": 3},
    })
    assert matched.family == "bank_statement"
    assert matched.extractor == "layout_map"


def test_cash_titles_extract_without_persisting_a_host_global_format(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "learned.json"
    monkeypatch.setenv("LUME_FORMAT_REGISTRY", str(store))
    path = _xlsx(
        tmp_path,
        ["Data", "Entrada", "Saída", "Saldo", "Centro"],
        [["2026-01-02", 10, 0, 10, "caixa"], ["2026-01-03", 0, 4, 6, "caixa"]],
        "caixa.xlsx",
    )
    first = run_pipeline(path, tmp_path / "out1")
    assert first.success, first.errors
    assert first.document_type == "cash_ledger"
    assert first.data["ledgers"][0]["entries"][0]["inflow"] == "10.00"
    assert not store.exists()
    second = run_pipeline(path, tmp_path / "out2")
    assert second.success, second.errors


def test_unmapped_columns_fail_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LUME_FORMAT_REGISTRY", str(tmp_path / "learned.json"))
    path = _xlsx(tmp_path, ["foo", "bar"], [["a", "b"]], "lixo.xlsx")
    result = run_pipeline(path, tmp_path / "out")
    assert not result.success
    assert result.errors[0].code == "spreadsheet_columns_not_mapped"


def test_csv_standard_columns_use_the_same_registry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LUME_FORMAT_REGISTRY", str(tmp_path / "learned.json"))
    path = tmp_path / "movimentos.csv"
    path.write_bytes(
        "\ufeffData;Histórico;Valor\n13/02/2026;PIX UNIVERSO;-150,09\n".encode("utf-8")
    )

    result = run_pipeline(path, tmp_path / "out")

    assert result.success, result.errors
    assert result.source_format == "csv"
    assert result.document_type == "bank_statement"
    assert result.data["transactions"][0]["description"] == "PIX UNIVERSO"
    assert result.data["transactions"][0]["amount"] == "-150.09"


def test_unknown_tabular_titles_return_samples_and_suggestions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LUME_FORMAT_REGISTRY", str(tmp_path / "learned.json"))
    path = _xlsx(
        tmp_path,
        ["Dt movimento", "Desc", "Vl"],
        [["13/02/2026", "PIX UNIVERSO", "-150,09"], ["14/02/2026", "TARIFA", "-12,00"]],
        "novo-layout.xlsx",
    )

    result = run_pipeline(path, tmp_path / "out")

    assert not result.success
    assert result.errors[0].code == "spreadsheet_columns_not_mapped"
    proposal = result.errors[0].details["layout"]
    assert proposal["headers"] == ["Dt movimento", "Desc", "Vl"]
    assert proposal["samples"]["1"][0] == "13/02/2026"
    assert proposal["suggestions"] == {"1": "date", "2": "description", "3": "amount"}
    assert proposal["family"] == "bank"
    assert len(proposal["fingerprint"]) == 64


def test_confirmed_cash_layout_does_not_require_balance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LUME_FORMAT_REGISTRY", str(tmp_path / "learned.json"))
    path = _xlsx(
        tmp_path,
        ["Dia", "Recebido", "Pago", "Memo"],
        [["13/02/2026", "120,00", "", "Venda"], ["14/02/2026", "", "20,00", "Tarifa"]],
        "caixa-sem-saldo.xlsx",
    )

    result = run_pipeline(
        path,
        tmp_path / "out",
        layout_map={
            "family": "cash",
            "headerRow": 1,
            "sheet": "Sheet",
            "columns": {"date": 1, "inflow": 2, "outflow": 3, "description": 4},
        },
    )

    assert result.success, result.errors
    assert result.document_type == "cash_ledger"
    assert result.data["ledgers"][0]["entries"][0]["inflow"] == "120.00"
    assert result.data["ledgers"][0]["entries"][1]["outflow"] == "20.00"
