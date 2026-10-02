"""Bradesco Net Empresa spreadsheet (bradesco-net-empresa-xls-v1)."""

from lume_ingestion.layouts.seeded import SeededFormat

_FORMAT = SeededFormat("bradesco-net-empresa-xls-v1", "bank_statement", "bank")

recognize = _FORMAT.recognize
default_guess = _FORMAT.default_guess
recipe_matches = _FORMAT.recipe_matches
force_proposal = _FORMAT.force_proposal
