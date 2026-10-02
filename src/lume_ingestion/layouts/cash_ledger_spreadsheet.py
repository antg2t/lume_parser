"""Cash ledger spreadsheet (cash-ledger-spreadsheet-v1)."""

from lume_ingestion.layouts.seeded import SeededFormat

_FORMAT = SeededFormat("cash-ledger-spreadsheet-v1", "cash_ledger", "cash")

recognize = _FORMAT.recognize
default_guess = _FORMAT.default_guess
recipe_matches = _FORMAT.recipe_matches
force_proposal = _FORMAT.force_proposal
