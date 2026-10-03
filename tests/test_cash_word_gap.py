"""Cash-report words keep a space when the PDF gap is wider than the letters.

The default reader cuts words at 3 points. This report draws about 2.6 between
words and 0 inside a word, so the name arrives as BancoBradescoS/A. Only a
recognized cash report is resplit. A bank statement keeps the default words.
"""

from __future__ import annotations

from lume_ingestion.cash_ledger import apply_cash_glyph_gaps, split_words_on_letter_gap


def _glyphs(pieces: list[str], gaps: list[float], *, top: float = 120.0, size: float = 5.0, x: float = 223.0) -> list[dict]:
    chars: list[dict] = []
    for index, piece in enumerate(pieces):
        if index:
            x += gaps[index - 1]
        for character in piece:
            chars.append({
                "text": character,
                "x0": x,
                "x1": x + size,
                "top": top,
                "bottom": top + 8,
                "doctop": top,
            })
            x += size
        x = chars[-1]["x1"]
    return chars


def test_cash_name_splits_where_the_gap_is_wider_than_the_letters() -> None:
    chars = _glyphs(
        ["00041\u00adBanco", "Bradesco", "S/A"],
        [2.64, 2.62],
    )

    words = [word["text"] for word in split_words_on_letter_gap(chars)]

    assert words == ["00041-Banco", "Bradesco", "S/A"]


def test_tight_letters_and_a_wide_tracked_line_stay_one_word() -> None:
    tight = _glyphs(["Banco"], [], size=5.0)
    # A 0.4pt kern inside the word is still a letter, not a space.
    tight[2]["x0"] += 0.4
    tight[2]["x1"] += 0.4
    for char in tight[3:]:
        char["x0"] += 0.4
        char["x1"] += 0.4
    tracked = _glyphs(list("BANCO"), [2.2, 2.2, 2.2, 2.2])

    assert [word["text"] for word in split_words_on_letter_gap(tight)] == ["Banco"]
    assert [word["text"] for word in split_words_on_letter_gap(tracked)] == ["BANCO"]


def test_notes_column_uses_the_same_gap() -> None:
    chars = _glyphs(["ENCARGOS", "C", "GARANTIDA"], [2.60, 2.58], x=465.0)

    assert [word["text"] for word in split_words_on_letter_gap(chars)] == ["ENCARGOS", "C", "GARANTIDA"]


def test_bank_statement_keeps_the_glued_token() -> None:
    page = {"words": [{"text": "BancoBradescoS/A", "x0": 10, "x1": 80, "top": 10}]}
    chars = _glyphs(["Banco", "Bradesco", "S/A"], [2.64, 2.62])

    apply_cash_glyph_gaps([page], [chars])

    assert page["words"][0]["text"] == "BancoBradescoS/A"


def test_cash_report_resplits_and_a_recovered_page_does_not() -> None:
    labels = [
        "Controle de Fechamento de Caixa",
        "SALDO INICIAL",
        "DATA",
        "EMISSAO",
        "ENTRADA",
        "SALDO",
    ]
    page = {"words": [{"text": label, "x0": 10, "x1": 40, "top": 10} for label in labels]}
    page["words"].append({"text": "BancoBradescoS/A", "x0": 223, "x1": 330, "top": 120})
    chars = _glyphs(["Banco", "Bradesco", "S/A"], [2.64, 2.62])
    recovered = {
        "recovery": "ocr",
        "words": [{"text": "BancoBradescoS/A", "x0": 223, "x1": 330, "top": 120}],
    }

    apply_cash_glyph_gaps([page, recovered], [chars, chars])

    assert [word["text"] for word in page["words"]] == ["Banco", "Bradesco", "S/A"]
    assert recovered["words"][0]["text"] == "BancoBradescoS/A"
