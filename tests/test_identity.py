from __future__ import annotations

from lume_ingestion.identity import extract_tax_id, party_from_continuation, split_document


def test_extracts_formatted_and_labeled_tax_ids() -> None:
    assert extract_tax_id("TJ CONTABIL", "65.087.421/0001-47") == "65.087.421/0001-47"
    assert extract_tax_id("CNPJ05075384000125") == "05.075.384/0001-25"
    assert extract_tax_id("29304072808") == "293.040.728-08"
    assert extract_tax_id("MARCELO PARANHOS", "29304072808") == "293.040.728-08"


def test_does_not_treat_phone_or_cep_as_tax_id() -> None:
    assert extract_tax_id("(11) 98888-1234") is None
    assert extract_tax_id("01310-100") is None
    assert extract_tax_id("0000001044") is None


def test_splits_labeled_or_trailing_document_from_movement() -> None:
    assert split_document("PIX ENVIADO 1044") == ("PIX ENVIADO", "1044")
    assert split_document("BOLETO PAGO NF 1001") == ("BOLETO PAGO", "1001")
    assert split_document("PIX ENVIADO") == ("PIX ENVIADO", None)
    assert split_document("BOLETOS RECEBIDOS 31/07S") == ("BOLETOS RECEBIDOS 31/07S", None)


def test_does_not_use_amount_as_document() -> None:
    assert split_document("PIX ENVIADO -1.234,56")[1] is None


def test_bradesco_remet_dest_is_the_company() -> None:
    assert party_from_continuation("REMET.FLSMIDTH INDUSTRIAL") == "FLSMIDTH INDUSTRIAL"
    assert party_from_continuation("DEST. VANGUARDA REFRATARIO") == "VANGUARDA REFRATARIO"
    assert party_from_continuation("PIX ENVIADO") is None
    assert party_from_continuation("AIR LIQUIDE BRASIL LTDA") == "AIR LIQUIDE BRASIL LTDA"
