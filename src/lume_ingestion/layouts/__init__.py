"""One layout contract per format the parser already knows.

Cash, extrato, plano and historico do not share recipes.  A file is applied
silently only when its own saved recipe still matches; recognition alone is
not permission to skip the column handshake.
"""

from lume_ingestion.layouts.accounting_history import (
    default_guess as history_guess,
    force_proposal as history_force_proposal,
    recipe_matches as history_recipe_matches,
    recognize as recognize_history,
)
from lume_ingestion.layouts.bradesco_bank_statement import (
    default_guess as bradesco_pdf_guess,
    force_proposal as bradesco_pdf_force_proposal,
    recipe_matches as bradesco_pdf_recipe_matches,
    recognize as recognize_bradesco_pdf,
)
from lume_ingestion.layouts.bradesco_net_empresa import (
    default_guess as bradesco_sheet_guess,
    force_proposal as bradesco_sheet_force_proposal,
    recipe_matches as bradesco_sheet_recipe_matches,
    recognize as recognize_bradesco_sheet,
)
from lume_ingestion.layouts.entry import apply_entry
from lume_ingestion.layouts.cash_ledger_report import (
    apply_cash_pdf_handshake,
    default_guess as cash_pdf_guess,
    force_proposal as cash_pdf_force_proposal,
    recipe_matches as cash_pdf_recipe_matches,
    recognize as recognize_cash_pdf,
)
from lume_ingestion.layouts.cash_ledger_spreadsheet import (
    default_guess as cash_sheet_guess,
    force_proposal as cash_sheet_force_proposal,
    recipe_matches as cash_sheet_recipe_matches,
    recognize as recognize_cash_sheet,
)
from lume_ingestion.layouts.chart_of_accounts import (
    default_guess as chart_guess,
    force_proposal as chart_force_proposal,
    recipe_matches as chart_recipe_matches,
    recognize as recognize_chart,
)
from lume_ingestion.layouts.itau_current_account_spreadsheet import (
    default_guess as itau_account_guess,
    force_proposal as itau_account_force_proposal,
    recipe_matches as itau_account_recipe_matches,
    recognize as recognize_itau_account,
)
from lume_ingestion.layouts.itau_digital_bank_statement import (
    default_guess as itau_pdf_guess,
    force_proposal as itau_pdf_force_proposal,
    recipe_matches as itau_pdf_recipe_matches,
    recognize as recognize_itau_pdf,
)
from lume_ingestion.layouts.itau_spreadsheet_bank_statement import (
    default_guess as itau_sheet_guess,
    force_proposal as itau_sheet_force_proposal,
    recipe_matches as itau_sheet_recipe_matches,
    recognize as recognize_itau_sheet,
)

FORMATS = (
    {
        "id": "cash-ledger-report-v1",
        "family": "cash_ledger",
        "recognize": recognize_cash_pdf,
        "default_guess": cash_pdf_guess,
        "recipe_matches": cash_pdf_recipe_matches,
        "force_proposal": cash_pdf_force_proposal,
    },
    {
        "id": "cash-ledger-spreadsheet-v1",
        "family": "cash_ledger",
        "recognize": recognize_cash_sheet,
        "default_guess": cash_sheet_guess,
        "recipe_matches": cash_sheet_recipe_matches,
        "force_proposal": cash_sheet_force_proposal,
    },
    {
        "id": "itau-spreadsheet-bank-statement-v1",
        "family": "bank_statement",
        "recognize": recognize_itau_sheet,
        "default_guess": itau_sheet_guess,
        "recipe_matches": itau_sheet_recipe_matches,
        "force_proposal": itau_sheet_force_proposal,
    },
    {
        "id": "itau-current-account-spreadsheet-v1",
        "family": "bank_statement",
        "recognize": recognize_itau_account,
        "default_guess": itau_account_guess,
        "recipe_matches": itau_account_recipe_matches,
        "force_proposal": itau_account_force_proposal,
    },
    {
        "id": "bradesco-net-empresa-xls-v1",
        "family": "bank_statement",
        "recognize": recognize_bradesco_sheet,
        "default_guess": bradesco_sheet_guess,
        "recipe_matches": bradesco_sheet_recipe_matches,
        "force_proposal": bradesco_sheet_force_proposal,
    },
    {
        "id": "itau-digital-bank-statement-v1",
        "family": "bank_statement",
        "recognize": recognize_itau_pdf,
        "default_guess": itau_pdf_guess,
        "recipe_matches": itau_pdf_recipe_matches,
        "force_proposal": itau_pdf_force_proposal,
    },
    {
        "id": "bradesco-bank-statement-v1",
        "family": "bank_statement",
        "recognize": recognize_bradesco_pdf,
        "default_guess": bradesco_pdf_guess,
        "recipe_matches": bradesco_pdf_recipe_matches,
        "force_proposal": bradesco_pdf_force_proposal,
    },
    {
        "id": "chart-of-accounts",
        "family": "chart_of_accounts",
        "recognize": recognize_chart,
        "default_guess": chart_guess,
        "recipe_matches": chart_recipe_matches,
        "force_proposal": chart_force_proposal,
    },
    {
        "id": "accounting-history",
        "family": "accounting_history",
        "recognize": recognize_history,
        "default_guess": history_guess,
        "recipe_matches": history_recipe_matches,
        "force_proposal": history_force_proposal,
    },
)


def recipe_for(raw: dict, formats: list[dict] | None = None) -> dict | None:
    """Return the saved recipe of the recognized format only.

    A cash recipe never satisfies plano or historico, and the reverse is also
    refused.  Recognition of a different family is not a match.
    """

    for spec in FORMATS:
        if not spec["recognize"](raw):
            continue
        matched = spec["recipe_matches"](raw, formats)
        if matched is not None and matched.get("family") != spec["family"]:
            return None
        return matched
    return None


__all__ = ["FORMATS", "apply_cash_pdf_handshake", "apply_entry", "recipe_for"]
