import pytest

from lume_ingestion.global_account_model import (
    _validate_prediction_period,
    ancestor_codes,
    feature_text,
    normalize_text,
    semantic_catalog,
)
from lume_ingestion.models import ChartOfAccountsEntry, SpreadsheetOrigin


def test_semantic_account_identity_does_not_depend_on_local_codes() -> None:
    origin = SpreadsheetOrigin(sheet="plano", row_number=1)
    first = (
        ChartOfAccountsEntry(code="4.0.0.00.000", description="Despesas", origin=origin),
        ChartOfAccountsEntry(code="4.1.0.00.000", description="Despesas Financeiras", origin=origin),
        ChartOfAccountsEntry(code="4.1.1.00.000", description="Encargos Financeiros", origin=origin),
        ChartOfAccountsEntry(code="4.1.1.01.000", description="Despesas Bancarias", origin=origin),
        ChartOfAccountsEntry(code="4.1.1.01.001", reduced_code="437", description="Tarifas Bancarias", origin=origin),
    )
    second = tuple(
        row.model_copy(update={"reduced_code": "890"}) if row.reduced_code else row
        for row in first
    )

    first_account = semantic_catalog(first)["437"]
    second_account = semantic_catalog(second)["890"]

    assert first_account.key == second_account.key
    assert first_account.path == second_account.path
    assert normalize_text("NF 12345 em 01/07/2026") == "nf numero em data"
    assert feature_text("Tarifa", "outflow") == "direcao_outflow tarifa"
    assert ancestor_codes("1.1.1.02.004")[-2:] == ["1.1.1.02.000", "1.1.1.02.004"]
    assert ancestor_codes("11102004")[-2:] == ["11102000", "11102004"]

    _validate_prediction_period({"trained_before_period": "2026-08"}, "2026-08")
    with pytest.raises(ValueError, match="unsafe"):
        _validate_prediction_period({"trained_before_period": "2026-09"}, "2026-08")
