"""Bradesco PDF statement (bradesco-bank-statement-v1)."""

from lume_ingestion.bank_statement import recognize_bradesco_bank_statement
from lume_ingestion.layouts.seeded import SeededFormat

_FORMAT = SeededFormat(
    "bradesco-bank-statement-v1",
    "bank_statement",
    "bank",
    recognizer=recognize_bradesco_bank_statement,
)

recognize = _FORMAT.recognize
default_guess = _FORMAT.default_guess
recipe_matches = _FORMAT.recipe_matches
force_proposal = _FORMAT.force_proposal
