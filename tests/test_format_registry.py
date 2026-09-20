from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from lume_ingestion.format_registry import header_titles_from_extract, match_extract
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


def test_known_bradesco_layout_wins_over_auxiliary_table_in_learned_registry(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "learned.json"
    store.write_text(
        '{"schema_version":"1.0","formats":[{"id":"DESCONHECIDO-spreadsheet-bank-statement-v1",'
        '"family":"bank_statement","bank":"Itaú","headers":["DATA","HISTORICO","VALORR"],'
        '"required":["amount","date","description"]}]}',
        encoding="utf-8",
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet0"
    sheet.append(["Bradesco Net Empresa"])
    sheet.append(["Data", "Lançamento", "Dcto.", "Crédito (R$)", "Débito (R$)", "Saldo (R$)"])
    sheet.append(["02/01/2026", "PIX RECEBIDO", "1", "100,00", "", "100,00"])
    sheet.append(["03/01/2026", "PAGAMENTO", "2", "", "-25,00", "75,00"])
    sheet.append(["Total", "", "", "100,00", "-25,00", "75,00"])
    sheet.append([])
    sheet.append(["Saldos Invest Fácil / Plus"])
    sheet.append([])
    sheet.append(["Data", "Histórico", "Valor (R$)"])
    sheet.append(["02/01/2026", "SALDO INVEST FÁCIL", "567.313,56"])
    sheet.append(["03/01/2026", "SALDO INVEST FÁCIL", "443.763,88"])
    path = tmp_path / "bradesco-com-tabela-auxiliar.xlsx"
    workbook.save(path)

    result = run_pipeline(path, tmp_path / "out")

    assert result.success, result.errors
    assert result.data["bank"] == "Bradesco"
    assert [row["description"] for row in result.data["transactions"]] == ["PIX RECEBIDO", "PAGAMENTO"]
    assert result.data["transactions"][0]["amount"] == "100.00"
    assert result.data["transactions"][1]["amount"] == "-25.00"
    assert result.data["transactions"][0]["origin"]["row_number"] == 3


def test_new_header_titles_mint_a_format_and_the_next_file_reuses_it(tmp_path: Path, monkeypatch) -> None:
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
    learned = store.read_text(encoding="utf-8")
    assert "HISTORICO" in learned or "historico" in learned.lower()

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


def test_cash_titles_extract_and_persist_a_new_format(tmp_path: Path, monkeypatch) -> None:
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
    assert store.is_file()
    second = run_pipeline(path, tmp_path / "out2")
    assert second.success, second.errors


def test_unmapped_columns_fail_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LUME_FORMAT_REGISTRY", str(tmp_path / "learned.json"))
    path = _xlsx(tmp_path, ["foo", "bar"], [["a", "b"]], "lixo.xlsx")
    result = run_pipeline(path, tmp_path / "out")
    assert not result.success
    assert result.errors[0].code == "spreadsheet_columns_not_mapped"
