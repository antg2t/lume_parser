"""Column handshake for a recognized cash-ledger PDF.

Fixed x-bands stay a suggestion.  Without a saved cash recipe, or when a
header midpoint leaves its band, the pipeline pauses with a prefilled proposal.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from lume_ingestion.layouts import recipe_for
from lume_ingestion.layouts import cash_ledger_report
from lume_ingestion.pipeline import run_pipeline

CASH_RECIPE = {
    "id": "cash-ledger-report-v1",
    "family": "cash_ledger",
    "bank": None,
    "source_formats": ["pdf"],
    "headers": ["DATA", "ENTRADA", "SAIDA", "SALDO"],
    "labels": ["controledefechamentodecaixa", "saldoinicial", "entrada"],
    "required": ["date", "inflow", "outflow", "balance"],
}

# Starts measured for Helvetica 8pt so header midpoints fall inside the bands
# used by normalize_cash_ledger_pdf (date 0-76 ... balance 765-830).
_HEADER_X = {
    "DATA": 35.33,
    "EMISSAO": 90.77,
    "DOC.": 171.00,
    "CLIENTE/FORNECEDOR": 295.55,
    "ANOTACOES": 524.88,
    "ENTRADA": 647.89,
    "SAIDA": 715.00,
    "SALDO": 769.44,
}
_VALUE_X = {
    "date": 18.00,
    "issue": 86.00,
    "document": 169.00,
    "counterparty": 312.00,
    "notes": 529.00,
    "inflow": 670.00,
    "outflow": 727.00,
    "balance": 780.00,
}


def _pdf(path: Path, tokens: list[tuple[float, float, str]]) -> None:
    lines = ["BT", "/F1 8 Tf"]
    for x, top, text in tokens:
        y = 595 - top
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines.append(f"1 0 0 1 {x:.2f} {y:.2f} Tm ({escaped}) Tj")
    lines.append("ET")
    stream = ("\n".join(lines) + "\n").encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def _cash_pdf(path: Path, *, saldo_x: float | None = None, rows: list[tuple[str, str, str]] | None = None) -> None:
    """Vanguarda-like labels: Controle de Fechamento de Caixa, with one or more movements."""

    header_x = dict(_HEADER_X)
    if saldo_x is not None:
        header_x["SALDO"] = saldo_x
    tokens = [
        (300, 21, "Controle de Fechamento de Caixa"),
        (40, 48, "SALDO INICIAL"),
        (70, 66, "237 -BRADESCO"),
    ]
    for label, x in header_x.items():
        tokens.append((x, 91, label))
    movements = rows or [("10,00", "0,00", "10,00")]
    for index, (inflow, outflow, balance) in enumerate(movements):
        top = 120 + index * 14
        tokens.extend([
            (_VALUE_X["date"], top, "03/08/2026"),
            (_VALUE_X["issue"], top, "03/08/2026"),
            (_VALUE_X["document"], top, "12972"),
            (_VALUE_X["counterparty"], top, "FORNECEDOR"),
            (_VALUE_X["notes"], top, "NOTA"),
            (_VALUE_X["inflow"], top, inflow),
            (_VALUE_X["outflow"], top, outflow),
            (_VALUE_X["balance"], top, balance),
        ])
    _pdf(path, tokens)


def _only(recipes):
    def _load():
        return recipes
    return _load


def test_recognized_cash_pdf_without_recipe_proposes_prefilled_columns(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cash_ledger_report, "load_formats", _only([]))
    path = tmp_path / "vanguarda-like.pdf"
    _cash_pdf(path)

    result = run_pipeline(path, tmp_path / "out")

    assert not result.success
    assert result.errors[0].code == "spreadsheet_columns_not_mapped"
    proposal = result.errors[0].details["layout"]
    assert proposal["family"] == "cash"
    assert "DATA" in proposal["headers"]
    assert "ENTRADA" in proposal["headers"]
    assert "SALDO" in proposal["headers"]
    guessed = set(proposal["suggestions"].values())
    assert {"date", "inflow", "outflow", "balance"} <= guessed
    assert proposal["samples"]
    assert "ledgers" not in result.data


def test_matching_cash_recipe_parses_without_a_pause(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cash_ledger_report, "load_formats", _only([CASH_RECIPE]))
    path = tmp_path / "vanguarda-like.pdf"
    _cash_pdf(path)

    result = run_pipeline(path, tmp_path / "out")

    assert result.success, result.errors
    assert result.document_type == "cash_ledger"
    assert result.data["ledgers"][0]["entries"][0]["inflow"] == "10.00"
    assert result.data["ledgers"][0]["entries"][0]["balance"] == "10.00"


def test_header_outside_the_fixed_band_requires_a_proposal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cash_ledger_report, "load_formats", _only([CASH_RECIPE]))
    path = tmp_path / "saldo-shifted.pdf"
    # SALDO label leaves 765-830.  The amount underneath still sits in the old band.
    _cash_pdf(path, saldo_x=400)

    result = run_pipeline(path, tmp_path / "out")

    assert not result.success
    assert result.errors[0].code == "spreadsheet_columns_not_mapped"
    proposal = result.errors[0].details["layout"]
    assert "balance" in proposal["outside_bands"]
    assert "balance" in proposal["suggestions"].values()
    assert "ledgers" not in result.data


def test_unclosed_balance_does_not_succeed_as_if_mapped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cash_ledger_report, "load_formats", _only([CASH_RECIPE]))
    path = tmp_path / "balance-open.pdf"
    _cash_pdf(path, rows=[("10,00", "0,00", "10,00"), ("5,00", "0,00", "10,00")])

    result = run_pipeline(path, tmp_path / "out")

    assert not result.success
    assert result.errors[0].code == "spreadsheet_columns_not_mapped"
    assert result.errors[0].details["layout"]["family"] == "cash"
    assert "ledgers" not in result.data


def test_chart_and_history_do_not_reuse_the_cash_recipe(tmp_path: Path) -> None:
    chart = Workbook()
    sheet = chart.active
    sheet.append(["Conta", "Descricao"])
    sheet.append(["1.1.01", "Caixa"])
    chart_path = tmp_path / "plano.xlsx"
    chart.save(chart_path)

    history = Workbook()
    history_sheet = history.active
    history_sheet.append(["Data", "Debito", "Credito", "Valor", "Complemento"])
    history_sheet.append(["2026-01-02", "1.1.01", "2.1.01", 10, "nota"])
    history_path = tmp_path / "historico.xlsx"
    history.save(history_path)

    chart_result = run_pipeline(chart_path, tmp_path / "plano")
    history_result = run_pipeline(history_path, tmp_path / "hist")

    assert chart_result.success, chart_result.errors
    assert chart_result.document_type == "chart_of_accounts"
    assert history_result.success, history_result.errors
    assert history_result.document_type == "accounting_history"
    assert recipe_for({"source_format": "xlsx", "workbook": {"sheets": []}}, [CASH_RECIPE]) is None
    assert cash_ledger_report.recipe_matches(
        {"source_format": "xlsx", "document_type": "chart_of_accounts", "pages": []},
        [CASH_RECIPE],
    ) is None
