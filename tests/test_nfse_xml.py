from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lume_ingestion.artifacts import read_json
from lume_ingestion.nfse import _check_digit
from lume_ingestion.pipeline import inspect, run_pipeline


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
XMLS = sorted(DOCS_ROOT.glob("Nota fiscal/xml/*.xml"))
GOLDENS = json.loads((Path(__file__).parent / "goldens" / "nfse_xml.json").read_text(encoding="utf-8"))


def nested(value: dict, dotted_path: str):
    current = value
    for part in dotted_path.split("."):
        current = current[part]
    return current


def write_variant(tmp_path: Path, source: Path, transform) -> Path:
    destination = tmp_path / source.name
    destination.write_text(transform(source.read_text(encoding="utf-8")), encoding="utf-8")
    return destination


@pytest.mark.integration
@pytest.mark.parametrize("xml_path", XMLS, ids=lambda path: path.name)
def test_nfse_collection_matches_manual_goldens(xml_path: Path, tmp_path: Path) -> None:
    result = run_pipeline(xml_path, tmp_path / "output")

    assert result.success, result.errors
    assert result.document_type == "nfse"
    assert result.source_format == "xml"
    assert result.outputs.page_texts == []
    expected = GOLDENS[xml_path.name]
    for field, expected_value in expected.items():
        assert nested(result.data, field) == expected_value, field

    raw = read_json(result.outputs.raw_json)
    normalized = read_json(result.outputs.normalized_json)
    assert raw["xml"]["original"] == xml_path.read_text(encoding="utf-8")
    assert raw["xml"]["signature"]["name"] == "Signature"
    assert raw["xml"]["digest_values"]
    assert normalized["fiscal_document"] == result.data


def test_xml_is_detected_by_content_and_preserves_accents(tmp_path: Path) -> None:
    source = next(path for path in XMLS if "260750803" in path.name)
    disguised = tmp_path / "nota.data"
    disguised.write_bytes(source.read_bytes())

    detection = inspect(disguised)
    result = run_pipeline(disguised, tmp_path / "output")

    assert detection.format == "xml"
    assert detection.warnings[0].code == "extension_content_mismatch"
    assert result.success
    assert result.data["customer"]["address"]["district"] == "Chácaras São Bento"
    assert "computação" in result.data["service"]["national_tax_description"]


def test_optional_fields_are_explicit_null_and_have_xpath(tmp_path: Path) -> None:
    result = run_pipeline(XMLS[0], tmp_path / "output")

    assert result.success
    assert result.data["intermediary"] is None
    assert result.data["amounts"]["conditional_discount"] is None
    assert result.data["provenance"]["intermediary"]["xpath"].endswith("/nfse:interm")
    assert result.data["provenance"]["amounts.conditional_discount"]["xpath"].endswith("/nfse:vDescCond")
    for field, source in result.data["provenance"].items():
        assert source["xpath"] or source["derived_from"] or source["justification"], field


def test_ibs_cbs_is_complete_and_optional(tmp_path: Path) -> None:
    with_ibs = next(path for path in XMLS if "260750803" in path.name)
    result = run_pipeline(with_ibs, tmp_path / "output")

    assert result.success
    ibs_cbs = result.data["taxes"]["ibs_cbs"]
    assert ibs_cbs["taxable_base"] == "117.38"
    assert ibs_cbs["ibs_total"] == "0.12"
    assert ibs_cbs["ibs_state"] == "0.11"
    assert ibs_cbs["ibs_municipal"] == "0.00"
    assert ibs_cbs["cbs"] == "1.05"
    assert ibs_cbs["declaration"]["summary"]["name"] == "IBSCBS"
    assert ibs_cbs["declaration"]["declaration"]["name"] == "IBSCBS"

    without_ibs = run_pipeline(XMLS[0], tmp_path / "without-output")
    assert without_ibs.success
    assert without_ibs.data["taxes"]["ibs_cbs"] is None


@pytest.mark.parametrize(
    ("content", "error_code"),
    [
        (b'<?xml version="1.0"?><NFSe', "invalid_xml"),
        (b'<NFSe xmlns="urn:unknown" versao="1.01"/>', "unknown_xml_namespace"),
        (
            b'<?xml version="1.0"?><!DOCTYPE NFSe [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b'<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.01">&xxe;</NFSe>',
            "unsafe_xml",
        ),
    ],
)
def test_rejects_invalid_or_unsafe_xml(content: bytes, error_code: str, tmp_path: Path) -> None:
    source = tmp_path / "invalid.xml"
    source.write_bytes(content)

    result = run_pipeline(source, tmp_path / "output")

    assert result.success is False
    assert result.errors[0].code == error_code


def test_rejects_unknown_version(tmp_path: Path) -> None:
    source = write_variant(tmp_path, XMLS[0], lambda text: text.replace('versao="1.01"', 'versao="9.99"', 1))

    result = run_pipeline(source, tmp_path / "output")

    assert result.success is False
    assert result.errors[0].code == "unsupported_nfse_version"


def test_rejects_consistent_checksum_but_inconsistent_key(tmp_path: Path) -> None:
    text = XMLS[0].read_text(encoding="utf-8")
    old_key = re.search(r'<infNFSe Id="NFS(\d{50})"', text).group(1)
    # A posicao 35 pertence ao numero da NFS-e; o DV e recalculado para
    # distinguir chave estruturalmente valida de chave coerente com o XML.
    changed_number_digit = "0" if old_key[35] != "0" else "1"
    body = old_key[:35] + changed_number_digit + old_key[36:-1]
    new_key = body + str(_check_digit(body))
    source = tmp_path / "inconsistent.xml"
    source.write_text(text.replace(f'Id="NFS{old_key}"', f'Id="NFS{new_key}"', 1), encoding="utf-8")

    result = run_pipeline(source, tmp_path / "output")

    assert result.success is False
    assert result.errors[0].code == "inconsistent_nfse_key"


def test_rejects_non_numeric_value_and_invalid_date(tmp_path: Path) -> None:
    numeric = write_variant(tmp_path, XMLS[0], lambda text: text.replace("<vServ>309.64</vServ>", "<vServ>not-a-number</vServ>"))
    numeric_result = run_pipeline(numeric, tmp_path / "numeric-output")
    assert numeric_result.success is False
    assert numeric_result.errors[0].code == "invalid_numeric_value"

    dated = write_variant(tmp_path, XMLS[0], lambda text: text.replace("<dCompet>2026-09-01</dCompet>", "<dCompet>yesterday</dCompet>"))
    date_result = run_pipeline(dated, tmp_path / "date-output")
    assert date_result.success is False
    assert date_result.errors[0].code == "invalid_fiscal_document"


def test_unknown_group_is_preserved_in_raw_and_reported(tmp_path: Path) -> None:
    source = write_variant(
        tmp_path,
        XMLS[0],
        lambda text: text.replace("<DPS versao=", "<futureGroup><futureValue>á</futureValue></futureGroup><DPS versao=", 1),
    )

    result = run_pipeline(source, tmp_path / "output")
    raw = read_json(result.outputs.raw_json)

    assert result.success
    assert result.data["metadata"]["unknown_groups"] == ["futureGroup"]
    assert "<futureValue>á</futureValue>" in raw["xml"]["original"]
