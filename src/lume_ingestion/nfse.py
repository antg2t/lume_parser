from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from lume_ingestion.errors import IngestionFailure
from lume_ingestion.models import FieldProvenance, FiscalDocument
from lume_ingestion.parsers.nfse_xml import (
    NFSE_NAMESPACE,
    SIGNATURE_NAMESPACE,
    element_to_raw,
    parse_xml,
)


class XmlValues:
    def __init__(self, root: Any) -> None:
        self.root = root

    @staticmethod
    def xpath(relative: str) -> str:
        parts = relative.split("/")
        rendered = "/".join(part if part.startswith("@") else f"nfse:{part}" for part in parts)
        return f"/nfse:NFSe/{rendered}"

    def first(self, candidates: list[str]) -> tuple[str | None, str]:
        for relative in candidates:
            parts = relative.split("/")
            attribute = parts.pop()[1:] if parts and parts[-1].startswith("@") else None
            query = "./" + "/".join(f"{{{NFSE_NAMESPACE}}}{part}" for part in parts)
            element = self.root if not parts else self.root.find(query)
            if element is None:
                continue
            value = element.attrib.get(attribute) if attribute else element.text
            if value is not None and value.strip():
                return value.strip(), self.xpath(relative)
        return None, self.xpath(candidates[0])

    def element(self, relative: str) -> Any | None:
        query = "./" + "/".join(f"{{{NFSE_NAMESPACE}}}{part}" for part in relative.split("/"))
        return self.root.find(query)


class DocumentBuilder:
    def __init__(self, values: XmlValues) -> None:
        self.values = values
        self.provenance: dict[str, FieldProvenance] = {}

    def text(self, field: str, *candidates: str, transformation: str | None = None) -> str | None:
        value, xpath = self.values.first(list(candidates))
        self.provenance[field] = FieldProvenance(xpath=xpath, transformation=transformation)
        return value

    def money(self, field: str, *candidates: str) -> Decimal | None:
        value = self.text(field, *candidates, transformation="Decimal exato a partir do texto XML")
        if value is None:
            return None
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise IngestionFailure(
                "invalid_numeric_value",
                "Um campo monetario ou percentual da NFS-e nao e numerico.",
                field=field,
                xpath=self.provenance[field].xpath,
                value=value,
            ) from exc
        if not parsed.is_finite():
            raise IngestionFailure(
                "invalid_numeric_value",
                "Um campo monetario ou percentual da NFS-e nao e finito.",
                field=field,
                xpath=self.provenance[field].xpath,
                value=value,
            )
        return parsed

    def derived(
        self,
        field: str,
        value: Any,
        *derived_from: str,
        transformation: str | None = None,
        justification: str | None = None,
    ) -> Any:
        self.provenance[field] = FieldProvenance(
            derived_from=[self.values.xpath(path) for path in derived_from],
            transformation=transformation,
            justification=justification,
        )
        return value


def _party(builder: DocumentBuilder, field: str, bases: list[str]) -> dict[str, Any]:
    def paths(suffix: str) -> list[str]:
        return [f"{base}/{suffix}" for base in bases]

    tax_candidates = [
        f"{base}/{identifier}"
        for base in bases
        for identifier in ("CNPJ", "CPF", "NIF", "cNaoNIF")
    ]
    tax_id = builder.text(f"{field}.tax_id", *tax_candidates)
    tax_xpath = builder.provenance[f"{field}.tax_id"].xpath or ""
    if tax_id is None:
        tax_id_type = None
    elif tax_xpath.endswith("/nfse:CNPJ"):
        tax_id_type = "CNPJ"
    elif tax_xpath.endswith("/nfse:CPF"):
        tax_id_type = "CPF"
    elif tax_xpath.endswith("/nfse:NIF"):
        tax_id_type = "NIF"
    else:
        tax_id_type = "unknown"
    builder.provenance[f"{field}.tax_id_type"] = FieldProvenance(
        derived_from=[tax_xpath] if tax_xpath else [],
        transformation="Tipo inferido pelo nome do elemento que contem o identificador",
    )

    address_bases = [f"{base}/enderNac" for base in bases] + [f"{base}/end" for base in bases]
    municipality_paths: list[str] = []
    postal_paths: list[str] = []
    for base in address_bases:
        municipality_paths.extend([f"{base}/cMun", f"{base}/endNac/cMun"])
        postal_paths.extend([f"{base}/CEP", f"{base}/endNac/CEP"])
    address = {
        "municipality_code": builder.text(f"{field}.address.municipality_code", *municipality_paths),
        "street": builder.text(f"{field}.address.street", *paths("enderNac/xLgr"), *paths("end/xLgr")),
        "number": builder.text(f"{field}.address.number", *paths("enderNac/nro"), *paths("end/nro")),
        "complement": builder.text(f"{field}.address.complement", *paths("enderNac/xCpl"), *paths("end/xCpl")),
        "district": builder.text(f"{field}.address.district", *paths("enderNac/xBairro"), *paths("end/xBairro")),
        "state": builder.text(f"{field}.address.state", *paths("enderNac/UF"), *paths("end/UF")),
        "postal_code": builder.text(f"{field}.address.postal_code", *postal_paths),
    }
    return {
        "tax_id": tax_id,
        "tax_id_type": tax_id_type,
        "name": builder.text(f"{field}.name", *paths("xNome")),
        "trade_name": builder.text(f"{field}.trade_name", *paths("xFant")),
        "municipal_registration": builder.text(f"{field}.municipal_registration", *paths("IM")),
        "phone": builder.text(f"{field}.phone", *paths("fone")),
        "email": builder.text(f"{field}.email", *paths("email")),
        "address": address,
    }


def _optional_party(builder: DocumentBuilder, field: str, bases: list[str]) -> dict[str, Any] | None:
    if not any(builder.values.element(base) is not None for base in bases):
        builder.provenance[field] = FieldProvenance(xpath=builder.values.xpath(bases[0]))
        return None
    return _party(builder, field, bases)


def _signature_text(root: Any, name: str) -> tuple[str | None, str]:
    element = root.find(f".//{{{SIGNATURE_NAMESPACE}}}{name}")
    value = (element.text or "").strip() if element is not None else None
    return value or None, f"/nfse:NFSe/ds:Signature//ds:{name}"


def _raw_ibs_cbs(values: XmlValues) -> dict[str, Any] | None:
    summary = values.element("infNFSe/IBSCBS")
    declaration = values.element("infNFSe/DPS/infDPS/IBSCBS")
    if summary is None and declaration is None:
        return None
    return {
        "summary": element_to_raw(summary) if summary is not None else None,
        "declaration": element_to_raw(declaration) if declaration is not None else None,
    }


def normalize_nfse(raw: dict[str, Any]) -> FiscalDocument:
    xml = raw.get("xml")
    if not isinstance(xml, dict) or not isinstance(xml.get("original"), str):
        raise IngestionFailure("invalid_raw", "O RAW XML nao preservou o documento original.")
    root = parse_xml(xml["original"].encode(xml.get("encoding") or "UTF-8"))
    values = XmlValues(root)
    builder = DocumentBuilder(values)

    builder.derived(
        "document_type", "nfse", "@versao",
        justification="Raiz NFSe e namespace nacional reconhecidos pelo adaptador versionado",
    )
    builder.derived(
        "layout", "national", "@versao",
        justification="Namespace oficial da NFS-e Nacional reconhecido pelo parser",
    )
    version = builder.text("version", "@versao")
    nfse_id = builder.text("metadata.nfse_id", "infNFSe/@Id")
    key = nfse_id[3:] if nfse_id and nfse_id.startswith("NFS") else (nfse_id or "")
    builder.provenance["key"] = FieldProvenance(
        xpath=values.xpath("infNFSe/@Id"),
        transformation="Remocao do prefixo literal NFS",
    )
    builder.derived("amounts.currency", "BRL", justification="Moeda legal dos valores do leiaute fiscal nacional")

    provider = _party(builder, "provider", ["infNFSe/emit", "infNFSe/DPS/infDPS/prest"])
    customer = _party(builder, "customer", ["infNFSe/DPS/infDPS/toma"])
    intermediary = _optional_party(builder, "intermediary", ["infNFSe/DPS/infDPS/interm"])

    ibs_raw = _raw_ibs_cbs(values)
    if ibs_raw is None:
        ibs_cbs = None
        builder.provenance["taxes.ibs_cbs"] = FieldProvenance(xpath=values.xpath("infNFSe/IBSCBS"))
    else:
        ibs_cbs = {
            "incidence_location_code": builder.text("taxes.ibs_cbs.incidence_location_code", "infNFSe/IBSCBS/cLocalidadeIncid"),
            "incidence_location_name": builder.text("taxes.ibs_cbs.incidence_location_name", "infNFSe/IBSCBS/xLocalidadeIncid"),
            "taxable_base": builder.money("taxes.ibs_cbs.taxable_base", "infNFSe/IBSCBS/valores/vBC"),
            "ibs_state_rate": builder.money("taxes.ibs_cbs.ibs_state_rate", "infNFSe/IBSCBS/valores/uf/pIBSUF"),
            "ibs_state_effective_rate": builder.money("taxes.ibs_cbs.ibs_state_effective_rate", "infNFSe/IBSCBS/valores/uf/pAliqEfetUF"),
            "ibs_municipal_rate": builder.money("taxes.ibs_cbs.ibs_municipal_rate", "infNFSe/IBSCBS/valores/mun/pIBSMun"),
            "ibs_municipal_effective_rate": builder.money("taxes.ibs_cbs.ibs_municipal_effective_rate", "infNFSe/IBSCBS/valores/mun/pAliqEfetMun"),
            "cbs_rate": builder.money("taxes.ibs_cbs.cbs_rate", "infNFSe/IBSCBS/valores/fed/pCBS"),
            "cbs_effective_rate": builder.money("taxes.ibs_cbs.cbs_effective_rate", "infNFSe/IBSCBS/valores/fed/pAliqEfetCBS"),
            "invoice_total": builder.money("taxes.ibs_cbs.invoice_total", "infNFSe/IBSCBS/totCIBS/vTotNF"),
            "ibs_total": builder.money("taxes.ibs_cbs.ibs_total", "infNFSe/IBSCBS/totCIBS/gIBS/vIBSTot"),
            "ibs_state": builder.money("taxes.ibs_cbs.ibs_state", "infNFSe/IBSCBS/totCIBS/gIBS/gIBSUFTot/vIBSUF"),
            "ibs_municipal": builder.money("taxes.ibs_cbs.ibs_municipal", "infNFSe/IBSCBS/totCIBS/gIBS/gIBSMunTot/vIBSMun"),
            "cbs": builder.money("taxes.ibs_cbs.cbs", "infNFSe/IBSCBS/totCIBS/gCBS/vCBS"),
            "declaration": ibs_raw,
        }
        builder.provenance["taxes.ibs_cbs.declaration"] = FieldProvenance(
            derived_from=[values.xpath("infNFSe/IBSCBS"), values.xpath("infNFSe/DPS/infDPS/IBSCBS")],
            transformation="Subarvores XML convertidas sem descartar elementos, atributos ou namespaces",
        )

    reference_element = root.find(f".//{{{SIGNATURE_NAMESPACE}}}Reference")
    signature_reference = reference_element.attrib.get("URI") if reference_element is not None else None
    digest, digest_xpath = _signature_text(root, "DigestValue")
    signature_value, signature_value_xpath = _signature_text(root, "SignatureValue")
    certificate, certificate_xpath = _signature_text(root, "X509Certificate")
    builder.provenance["metadata.signature_reference"] = FieldProvenance(
        xpath="/nfse:NFSe/ds:Signature//ds:Reference/@URI",
        transformation="Valor do atributo URI",
    )
    builder.provenance["metadata.digest_value"] = FieldProvenance(xpath=digest_xpath)
    builder.provenance["metadata.signature_value"] = FieldProvenance(xpath=signature_value_xpath)
    builder.provenance["metadata.certificate"] = FieldProvenance(xpath=certificate_xpath)
    builder.provenance["metadata.unknown_groups"] = FieldProvenance(
        xpath=values.xpath("infNFSe"),
        transformation="Nomes dos grupos nao consumidos pelo adaptador",
    )

    document = {
        "document_type": "nfse",
        "layout": "national",
        "version": version,
        "key": key,
        "number": builder.text("number", "infNFSe/nNFSe"),
        "dps": {
            "id": builder.text("dps.id", "infNFSe/DPS/infDPS/@Id"),
            "number": builder.text("dps.number", "infNFSe/DPS/infDPS/nDPS"),
            "series": builder.text("dps.series", "infNFSe/DPS/infDPS/serie"),
        },
        "environment": builder.text("environment", "infNFSe/DPS/infDPS/tpAmb"),
        "status": builder.text("status", "infNFSe/cStat"),
        "issue_datetime": builder.text("issue_datetime", "infNFSe/DPS/infDPS/dhEmi"),
        "competence_date": builder.text("competence_date", "infNFSe/DPS/infDPS/dCompet"),
        "provider": provider,
        "customer": customer,
        "intermediary": intermediary,
        "locations": {
            "issue_code": builder.text("locations.issue_code", "infNFSe/DPS/infDPS/cLocEmi"),
            "issue_name": builder.text("locations.issue_name", "infNFSe/xLocEmi"),
            "service_code": builder.text("locations.service_code", "infNFSe/DPS/infDPS/serv/locPrest/cLocPrestacao"),
            "service_name": builder.text("locations.service_name", "infNFSe/xLocPrestacao"),
            "incidence_code": builder.text("locations.incidence_code", "infNFSe/cLocIncid"),
            "incidence_name": builder.text("locations.incidence_name", "infNFSe/xLocIncid"),
        },
        "service": {
            "national_tax_code": builder.text("service.national_tax_code", "infNFSe/DPS/infDPS/serv/cServ/cTribNac"),
            "municipal_tax_code": builder.text("service.municipal_tax_code", "infNFSe/DPS/infDPS/serv/cServ/cTribMun"),
            "nbs_code": builder.text("service.nbs_code", "infNFSe/DPS/infDPS/serv/cServ/cNBS"),
            "national_tax_description": builder.text("service.national_tax_description", "infNFSe/xTribNac"),
            "municipal_tax_description": builder.text("service.municipal_tax_description", "infNFSe/xTribMun"),
            "nbs_description": builder.text("service.nbs_description", "infNFSe/xNBS"),
            "description": builder.text("service.description", "infNFSe/DPS/infDPS/serv/cServ/xDescServ"),
            "contributor_internal_code": builder.text("service.contributor_internal_code", "infNFSe/DPS/infDPS/serv/cServ/cIntContrib"),
        },
        "amounts": {
            "currency": "BRL",
            "gross": builder.money("amounts.gross", "infNFSe/DPS/infDPS/valores/vServPrest/vServ"),
            "taxable_base": builder.money("amounts.taxable_base", "infNFSe/valores/vBC"),
            "rate": builder.money("amounts.rate", "infNFSe/valores/pAliqAplic"),
            "iss": builder.money("amounts.iss", "infNFSe/valores/vISSQN"),
            "retained_total": builder.money("amounts.retained_total", "infNFSe/valores/vTotalRet"),
            "unconditional_discount": builder.money("amounts.unconditional_discount", "infNFSe/DPS/infDPS/valores/vDescCondIncond/vDescIncond"),
            "conditional_discount": builder.money("amounts.conditional_discount", "infNFSe/DPS/infDPS/valores/vDescCondIncond/vDescCond"),
            "net": builder.money("amounts.net", "infNFSe/valores/vLiq"),
        },
        "retentions": {
            "iss_type": builder.text("retentions.iss_type", "infNFSe/DPS/infDPS/valores/trib/tribMun/tpRetISSQN"),
            "pis_cofins_type": builder.text("retentions.pis_cofins_type", "infNFSe/DPS/infDPS/valores/trib/tribFed/piscofins/tpRetPisCofins"),
            "pis": builder.money("retentions.pis", "infNFSe/DPS/infDPS/valores/trib/tribFed/piscofins/vPis"),
            "cofins": builder.money("retentions.cofins", "infNFSe/DPS/infDPS/valores/trib/tribFed/piscofins/vCofins"),
            "csll": builder.money("retentions.csll", "infNFSe/DPS/infDPS/valores/trib/tribFed/vRetCSLL"),
            "inss": builder.money("retentions.inss", "infNFSe/DPS/infDPS/valores/trib/tribFed/vRetCP"),
            "ir": builder.money("retentions.ir", "infNFSe/DPS/infDPS/valores/trib/tribFed/vRetIRRF"),
        },
        "taxes": {
            "iss_taxation": builder.text("taxes.iss_taxation", "infNFSe/DPS/infDPS/valores/trib/tribMun/tribISSQN"),
            "pis_cofins_status": builder.text("taxes.pis_cofins_status", "infNFSe/DPS/infDPS/valores/trib/tribFed/piscofins/CST"),
            "federal_total": builder.money("taxes.federal_total", "infNFSe/DPS/infDPS/valores/trib/totTrib/vTotTrib/vTotTribFed"),
            "state_total": builder.money("taxes.state_total", "infNFSe/DPS/infDPS/valores/trib/totTrib/vTotTrib/vTotTribEst"),
            "municipal_total": builder.money("taxes.municipal_total", "infNFSe/DPS/infDPS/valores/trib/totTrib/vTotTrib/vTotTribMun"),
            "ibs_cbs": ibs_cbs,
        },
        "metadata": {
            "nfse_id": nfse_id,
            "application_version": builder.text("metadata.application_version", "infNFSe/verAplic"),
            "generation_environment": builder.text("metadata.generation_environment", "infNFSe/ambGer"),
            "emission_type": builder.text("metadata.emission_type", "infNFSe/tpEmis"),
            "processing_datetime": builder.text("metadata.processing_datetime", "infNFSe/dhProc"),
            "generated_document_number": builder.text("metadata.generated_document_number", "infNFSe/nDFSe"),
            "signature_reference": signature_reference,
            "digest_value": digest,
            "signature_value": signature_value,
            "certificate": certificate,
            "unknown_groups": list(xml.get("unknown_groups") or []),
        },
        "provenance": builder.provenance,
    }
    try:
        fiscal_document = FiscalDocument.model_validate(document)
    except ValidationError as exc:
        raise IngestionFailure(
            "invalid_fiscal_document",
            "A NFS-e nao atende ao contrato normalizado.",
            reason=str(exc),
        ) from exc
    validate_fiscal_document(fiscal_document)
    return fiscal_document


def _check_digit(value: str) -> int:
    weights = [2, 3, 4, 5, 6, 7, 8, 9]
    total = sum(int(digit) * weights[index % len(weights)] for index, digit in enumerate(reversed(value)))
    remainder = total % 11
    return 0 if remainder in (0, 1) else 11 - remainder


def _valid_cnpj(value: str) -> bool:
    if len(value) != 14 or not value.isdigit() or len(set(value)) == 1:
        return False
    digits = [int(item) for item in value]
    first = sum(item * weight for item, weight in zip(digits[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])) % 11
    first = 0 if first < 2 else 11 - first
    second = sum(item * weight for item, weight in zip(digits[:12] + [first], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])) % 11
    second = 0 if second < 2 else 11 - second
    return digits[-2:] == [first, second]


def _valid_cpf(value: str) -> bool:
    if len(value) != 11 or not value.isdigit() or len(set(value)) == 1:
        return False
    digits = [int(item) for item in value]
    first_sum = sum(item * weight for item, weight in zip(digits[:9], range(10, 1, -1)))
    first = 0 if first_sum % 11 < 2 else 11 - first_sum % 11
    second_sum = sum(item * weight for item, weight in zip(digits[:9] + [first], range(11, 1, -1)))
    second = 0 if second_sum % 11 < 2 else 11 - second_sum % 11
    return digits[-2:] == [first, second]


def validate_fiscal_document(document: FiscalDocument, *, require_complete: bool = True) -> None:
    key = document.key
    if key is None:
        if require_complete:
            raise IngestionFailure("missing_nfse_key", "A NFS-e nao possui chave de acesso.")
    elif len(key) != 50 or not key.isdigit() or _check_digit(key[:-1]) != int(key[-1]):
        raise IngestionFailure("invalid_nfse_key", "A chave da NFS-e e invalida.", key=key)
    if key is not None:
        expected = {
            "issue_municipality": (key[:7], document.locations.issue_code),
            "environment": (key[7], document.metadata.generation_environment),
            "provider_tax_id_type": (key[8], {"CPF": "1", "CNPJ": "2"}.get(document.provider.tax_id_type)),
            "provider_tax_id": (key[9:23], document.provider.tax_id.zfill(14) if document.provider.tax_id else None),
            "number": (key[23:36], document.number.zfill(13) if document.number else None),
        }
        inconsistent = {
            field: {"key": key_value, "document": document_value}
            for field, (key_value, document_value) in expected.items()
            if document_value is not None and key_value != document_value
        }
        if inconsistent:
            raise IngestionFailure(
                "inconsistent_nfse_key",
                "A chave da NFS-e diverge dos campos do documento.",
                inconsistent=inconsistent,
            )

    for field, party in (("provider", document.provider), ("customer", document.customer)):
        if not party.tax_id:
            if require_complete:
                raise IngestionFailure("missing_tax_id", "Um participante obrigatorio nao possui identificador fiscal.", field=field)
            continue
        if party.tax_id_type == "CNPJ" and not _valid_cnpj(party.tax_id):
            raise IngestionFailure("invalid_tax_id", "Um CNPJ da NFS-e e invalido.", field=field, value=party.tax_id)
        if party.tax_id_type == "CPF" and not _valid_cpf(party.tax_id):
            raise IngestionFailure("invalid_tax_id", "Um CPF da NFS-e e invalido.", field=field, value=party.tax_id)

    amounts = document.amounts
    for field in ("gross", "taxable_base", "iss", "retained_total", "unconditional_discount", "conditional_discount", "net"):
        value = getattr(amounts, field)
        if value is not None and value < 0:
            raise IngestionFailure("negative_fiscal_value", "Um valor fiscal que deveria ser positivo e negativo.", field=field, value=str(value))
    if amounts.gross is not None and amounts.net is not None and amounts.net > amounts.gross:
        raise IngestionFailure(
            "inconsistent_totals",
            "O valor liquido excede o valor bruto do servico.",
            gross=str(amounts.gross),
            net=str(amounts.net),
        )
    if amounts.taxable_base is not None and amounts.rate is not None and amounts.iss is not None:
        expected_iss = (amounts.taxable_base * amounts.rate / Decimal("100")).quantize(Decimal("0.01"))
        if abs(expected_iss - amounts.iss) > Decimal("0.01"):
            raise IngestionFailure(
                "inconsistent_iss_total",
                "O ISS nao corresponde a base e aliquota informadas.",
                expected=str(expected_iss),
                actual=str(amounts.iss),
            )
