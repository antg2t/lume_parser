"""Deterministic reader for the current national DANFSe PDF layout.

The reader deliberately works from ``pdfplumber`` words and their positions.
The linear text is useful for diagnostics, but is not reliable for this layout:
several labelled cells share a physical line and become glued together there.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from lume_ingestion.errors import IngestionFailure
from lume_ingestion.models import FieldProvenance, FiscalDocument, Warning


def _fold(value: str) -> str:
    """Compare labels independently of accents, spaces and punctuation."""
    decomposed = unicodedata.normalize("NFD", value)
    without_marks = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return re.sub(r"[^A-Z0-9]", "", without_marks.upper())


def _normal_text(value: str | None) -> str | None:
    if not value:
        return None
    rendered = unicodedata.normalize("NFC", value).replace("\u00a0", " ")
    # Some PDF generators expose UTF-8 text as Latin-1.  Only repair it when
    # the characteristic mojibake marker is present and the round trip works.
    if "Ã" in rendered or "Â" in rendered:
        try:
            rendered = rendered.encode("latin-1").decode("utf-8")
        except UnicodeError:
            pass
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return None if not rendered or rendered == "-" else rendered


def _decimal(value: str | None, *, percentage: bool = False) -> Decimal | None:
    if value is None:
        return None
    compact = value.replace("R$", "").replace("%", "").replace(" ", "").replace("\u00a0", "")
    compact = compact.replace(".", "").replace(",", ".")
    try:
        parsed = Decimal(compact)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


@dataclass(frozen=True)
class Line:
    words: tuple[dict[str, Any], ...]
    top: float
    bottom: float

    @property
    def text(self) -> str:
        return " ".join(str(word["text"]) for word in self.words)

    @property
    def folded(self) -> str:
        return _fold(self.text)

    def text_in(self, left: float, right: float) -> str:
        selected = [
            word for word in self.words
            if left <= (float(word["x0"]) + float(word["x1"])) / 2 < right
        ]
        return " ".join(str(word["text"]) for word in selected)


class PageLayout:
    """Small spatial index for the one-page national DANFSe layout."""

    def __init__(self, page: dict[str, Any]) -> None:
        self.page_number = int(page["page_number"])
        raw_words = page.get("words")
        if not isinstance(raw_words, list):
            raw_words = []
        words: list[dict[str, Any]] = []
        seen: set[tuple[str, float, float, float]] = set()
        for raw_word in raw_words:
            if not isinstance(raw_word, dict) or not raw_word.get("text"):
                continue
            try:
                key = (
                    str(raw_word["text"]),
                    round(float(raw_word["x0"]), 2),
                    round(float(raw_word["x1"]), 2),
                    round(float(raw_word["top"]), 2),
                )
            except (KeyError, TypeError, ValueError):
                continue
            # Duplicated text layers occur in real PDFs.  A duplicate at the
            # same coordinates is diagnostic noise, not a second value.
            if key in seen:
                continue
            seen.add(key)
            words.append(raw_word)
        words.sort(key=lambda word: (float(word["top"]), float(word["x0"])))

        grouped: list[list[dict[str, Any]]] = []
        tops: list[float] = []
        for word in words:
            top = float(word["top"])
            if not grouped or abs(top - tops[-1]) > 2.0:
                grouped.append([word])
                tops.append(top)
            else:
                grouped[-1].append(word)
        self.lines = tuple(
            Line(
                words=tuple(sorted(group, key=lambda word: float(word["x0"]))),
                top=min(float(word["top"]) for word in group),
                bottom=max(float(word.get("bottom", word["top"])) for word in group),
            )
            for group in grouped
        )

    def find(self, label: str, *, after: int = -1, before: int | None = None) -> int | None:
        wanted = _fold(label)
        end = len(self.lines) if before is None else before
        for index in range(after + 1, end):
            if wanted in self.lines[index].folded:
                return index
        return None

    def next(self, index: int | None) -> int | None:
        if index is None or index + 1 >= len(self.lines):
            return None
        return index + 1

    def between(self, start: int | None, end: int | None) -> tuple[Line, ...]:
        if start is None:
            return ()
        last = len(self.lines) if end is None else end
        return self.lines[start + 1 : last]


@dataclass(frozen=True)
class Evidence:
    page_number: int
    text: str
    left: float
    right: float
    top: float
    bottom: float

    def provenance(self, *, transformation: str | None = None, justification: str | None = None) -> FieldProvenance:
        return FieldProvenance(
            transformation=transformation,
            justification=justification,
            page_number=self.page_number,
            region={"x0": self.left, "x1": self.right, "top": self.top, "bottom": self.bottom},
            excerpt=self.text,
        )


class DanfseBuilder:
    def __init__(self, layout: PageLayout) -> None:
        self.layout = layout
        self.provenance: dict[str, FieldProvenance] = {}
        self.warnings: list[Warning] = []

    def evidence(self, line_index: int | None, left: float = 0, right: float = 595) -> Evidence | None:
        if line_index is None:
            return None
        line = self.layout.lines[line_index]
        raw = line.text_in(left, right)
        # A right-hand QR-code explanation sometimes sits vertically between a
        # label and its value.  It has no overlap with the requested cell.  In
        # that case advance only a short visual distance to the next line that
        # does overlap the same cell.
        if not raw:
            for candidate in self.layout.lines[line_index + 1 :]:
                if candidate.top - line.top > 18:
                    break
                candidate_raw = candidate.text_in(left, right)
                if candidate_raw:
                    line, raw = candidate, candidate_raw
                    break
        return Evidence(self.layout.page_number, raw, left, right, line.top, line.bottom)

    def value(
        self,
        field: str,
        line_index: int | None,
        left: float = 0,
        right: float = 595,
        *,
        transformation: str | None = "Texto impresso normalizado sem alterar o RAW",
        required: bool = False,
    ) -> str | None:
        evidence = self.evidence(line_index, left, right)
        if evidence is None:
            self.provenance[field] = FieldProvenance(
                justification="Rotulo ou regiao esperada nao foi localizado no DANFSe.",
                page_number=self.layout.page_number,
            )
            if required:
                self._missing(field)
            return None
        result = _normal_text(evidence.text)
        self.provenance[field] = evidence.provenance(transformation=transformation)
        if result is None and required:
            self._missing(field)
        return result

    def money(
        self,
        field: str,
        line_index: int | None,
        left: float,
        right: float,
        *,
        percentage: bool = False,
        required: bool = False,
    ) -> Decimal | None:
        text = self.value(
            field,
            line_index,
            left,
            right,
            transformation="Valor brasileiro convertido para Decimal exato; o texto impresso permanece no RAW",
            required=required,
        )
        if text is None:
            return None
        parsed = _decimal(text, percentage=percentage)
        if parsed is None:
            self.warnings.append(
                Warning(
                    code="unparseable_printed_value",
                    message="Um valor impresso no DANFSe nao pode ser convertido com seguranca.",
                    details={"field": field, "value": text},
                )
            )
        return parsed

    def derived(self, field: str, value: Any, source: str, *, transformation: str, justification: str) -> Any:
        source_provenance = self.provenance.get(source)
        self.provenance[field] = FieldProvenance(
            derived_from=[source],
            transformation=transformation,
            justification=justification,
            page_number=source_provenance.page_number if source_provenance else self.layout.page_number,
            region=source_provenance.region if source_provenance else None,
            excerpt=source_provenance.excerpt if source_provenance else None,
        )
        return value

    def _missing(self, field: str) -> None:
        self.warnings.append(
            Warning(
                code="missing_required_evidence",
                message="Um campo obrigatorio da NFS-e nao possui evidencia confiavel no DANFSe.",
                details={"field": field, "page_number": self.layout.page_number},
            )
        )


def _find_value_row(layout: PageLayout, label: str, *, after: int = -1, before: int | None = None) -> tuple[int | None, int | None]:
    label_index = layout.find(label, after=after, before=before)
    return label_index, layout.next(label_index)


def _tax_identifier(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    digits = re.sub(r"\D", "", value)
    if len(digits) == 14:
        return digits, "CNPJ"
    if len(digits) == 11:
        return digits, "CPF"
    return digits or None, "unknown" if digits else None


def _city_state(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    match = re.match(r"^(.*?)\s*/\s*([A-Za-z]{2})$", value)
    return (match.group(1).strip(), match.group(2).upper()) if match else (value, None)


def _code_postal(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parts = [part.strip() for part in value.split("/")]
    return (parts[0] or None, parts[1] if len(parts) > 1 and parts[1] else None)


def _party(builder: DanfseBuilder, field: str, section: str, *, after: int, before: int | None) -> dict[str, Any]:
    layout = builder.layout
    section_index = layout.find(section, after=after, before=before)
    tax_label, tax_value = _find_value_row(layout, "CPF / CNPJ / NIF", after=section_index or after, before=before)
    identifier_raw = builder.value(f"{field}.tax_id", tax_value, 0, 190, required=True)
    tax_id, tax_id_type = _tax_identifier(identifier_raw)
    builder.provenance[f"{field}.tax_id_type"] = FieldProvenance(
        derived_from=[f"{field}.tax_id"],
        transformation="Tipo determinado pela quantidade de digitos do identificador impresso",
        justification="O DANFSe apresenta CPF, CNPJ ou NIF no mesmo campo visual.",
        page_number=builder.provenance[f"{field}.tax_id"].page_number,
        region=builder.provenance[f"{field}.tax_id"].region,
        excerpt=identifier_raw,
    )
    municipal_registration = builder.value(f"{field}.municipal_registration", tax_value, 190, 385)
    phone = builder.value(f"{field}.phone", tax_value, 385, 595)

    name_label, name_value = _find_value_row(layout, "NOME / NOME EMPRESARIAL", after=tax_value or (tax_label or after), before=before)
    name = builder.value(f"{field}.name", name_value, 0, 290, required=True)
    city_state = builder.value(f"{field}.address.municipality_code", name_value, 290, 440)
    city, state = _city_state(city_state)
    builder.provenance[f"{field}.address.state"] = FieldProvenance(
        derived_from=[f"{field}.address.municipality_code"],
        transformation="UF separada da celula impressa Municipio / UF",
        justification="O layout nacional imprime municipio e UF na mesma celula.",
        page_number=builder.provenance[f"{field}.address.municipality_code"].page_number,
        region=builder.provenance[f"{field}.address.municipality_code"].region,
        excerpt=city_state,
    )
    builder.provenance[f"{field}.address.municipality_code"] = FieldProvenance(
        derived_from=[f"{field}.address.municipality_code"],
        transformation="Codigo municipal vem da celula Codigo IBGE / CEP da mesma linha visual",
        justification="O municipio em texto e preservado na evidencia da celula Municipio / UF.",
        page_number=builder.provenance[f"{field}.address.municipality_code"].page_number,
        region=builder.provenance[f"{field}.address.municipality_code"].region,
        excerpt=city_state,
    )
    # The label above shares the line with the printed IBGE/CEP value.  Read
    # it from the same value row before overwriting the provenance above.
    ibge_cep = builder.value(f"{field}.address.postal_code", name_value, 440, 595)
    municipality_code, postal_code = _code_postal(ibge_cep)
    ibge_provenance = builder.provenance[f"{field}.address.postal_code"]
    builder.provenance[f"{field}.address.municipality_code"] = FieldProvenance(
        transformation="Codigo IBGE separado da celula Codigo IBGE / CEP",
        justification="O codigo municipal e impresso junto ao CEP.",
        page_number=ibge_provenance.page_number,
        region=ibge_provenance.region,
        excerpt=ibge_cep,
    )
    builder.provenance[f"{field}.address.postal_code"] = FieldProvenance(
        transformation="CEP separado da celula Codigo IBGE / CEP",
        justification="O codigo municipal e impresso junto ao CEP.",
        page_number=ibge_provenance.page_number,
        region=ibge_provenance.region,
        excerpt=ibge_cep,
    )

    address_label, address_value = _find_value_row(layout, "ENDEREÇO", after=name_value or (name_label or after), before=before)
    street = builder.value(f"{field}.address.street", address_value, 0, 290)
    email = builder.value(f"{field}.email", address_value, 290, 595)
    for address_field in ("number", "complement", "district"):
        builder.provenance[f"{field}.address.{address_field}"] = FieldProvenance(
            derived_from=[f"{field}.address.street"],
            justification="O DANFSe apresenta o endereco como texto livre; nao ha evidencia confiavel para separar este componente.",
            page_number=builder.provenance[f"{field}.address.street"].page_number,
            region=builder.provenance[f"{field}.address.street"].region,
            excerpt=street,
        )
    return {
        "tax_id": tax_id,
        "tax_id_type": tax_id_type,
        "name": name,
        "trade_name": None,
        "municipal_registration": municipal_registration,
        "phone": phone,
        "email": email,
        "address": {
            "municipality_code": municipality_code,
            "street": street,
            "number": None,
            "complement": None,
            "district": None,
            "state": state,
            "postal_code": postal_code,
        },
    }


def _iso_datetime(value: str | None) -> str | None:
    if not value:
        return None
    for pattern in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(value, pattern).isoformat()
        except ValueError:
            continue
    return None


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def _section_text(layout: PageLayout, start: int | None, end: int | None) -> str | None:
    text = " ".join(line.text for line in layout.between(start, end))
    return _normal_text(text)


def is_national_danfse(pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return classification evidence only for a positively identified DANFSe."""
    for page in pages:
        layouts = PageLayout(page)
        rendered = " ".join(line.text for line in layouts.lines)
        folded = _fold(rendered)
        keys = re.findall(r"(?<!\d)(\d{50})(?!\d)", rendered)
        if (
            "DANFSE" in folded
            and "DOCUMENTOAUXILIARDANFSE" in folded
            and "CHAVEDEACESSODANFSE" in folded
            and "NUMERODANFSE" in folded
            and "PRESTADORFORNECEDORDANFSE" in folded
            and keys
        ):
            return {
                "adapter": "nfse-national-danfse-v2",
                "page_number": int(page["page_number"]),
                "key": keys[0],
                "signals": [
                    "DANFSe",
                    "Documento Auxiliar da NFS-e",
                    "Chave de acesso da NFS-e",
                    "Numero da NFS-e",
                    "Prestador / Fornecedor da NFS-e",
                ],
            }
    return None


def normalize_danfse(raw: dict[str, Any]) -> tuple[FiscalDocument, list[Warning]]:
    pages = raw.get("pages")
    if not isinstance(pages, list):
        raise IngestionFailure("invalid_raw", "O RAW de DANFSe nao possui paginas.")
    recognition = raw.get("document_recognition")
    recognized_page = recognition.get("page_number") if isinstance(recognition, dict) else None
    selected = next((page for page in pages if page.get("page_number") == recognized_page), pages[0] if pages else None)
    if not isinstance(selected, dict):
        raise IngestionFailure("invalid_raw", "O RAW de DANFSe nao possui uma pagina utilizavel.")
    layout = PageLayout(selected)
    builder = DanfseBuilder(layout)

    key_label, key_row = _find_value_row(layout, "CHAVE DE ACESSO DA NFS-e")
    printed_key = builder.value("key", key_row, required=True)
    key_match = re.search(r"\d{50}", printed_key or "")
    key = key_match.group(0) if key_match else ""
    if printed_key and not key_match:
        builder.warnings.append(Warning(code="invalid_printed_key", message="A chave impressa no DANFSe nao possui 50 digitos."))

    heading = layout.find("DANFSe")
    heading_text = layout.lines[heading].text if heading is not None else ""
    version_match = re.search(r"v\s*(\d+(?:\.\d+)?)", heading_text, re.IGNORECASE)
    version = version_match.group(1) if version_match else "2.0"
    builder.provenance["version"] = builder.evidence(heading).provenance(
        transformation="Versao lida do cabecalho DANFSe",
        justification="O layout e identificado pelo cabecalho impresso.",
    ) if builder.evidence(heading) else FieldProvenance(justification="Cabecalho DANFSe ausente.")
    builder.derived("document_type", "nfse", "version", transformation="Classificacao por cabecalho e rotulos internos", justification="O documento satisfaz os sinais obrigatorios do DANFSe nacional.")
    builder.derived("layout", "national", "version", transformation="Adaptador versionado do DANFSe", justification="O cabecalho DANFSe v2 e os rotulos nacionais foram confirmados.")

    number_label, number_row = _find_value_row(layout, "NÚMERO DA NFS-e")
    number = builder.value("number", number_row, 0, 145, required=True)
    competence_raw = builder.value("competence_date", number_row, 145, 290, required=True)
    competence_date = _iso_date(competence_raw)
    if competence_raw and competence_date is None:
        builder.warnings.append(Warning(code="invalid_printed_date", message="A competencia impressa nao esta em uma data brasileira valida.", details={"field": "competence_date", "value": competence_raw}))
    issue_raw = builder.value("issue_datetime", number_row, 290, 440, required=True)
    issue_datetime = _iso_datetime(issue_raw)
    if issue_raw and issue_datetime is None:
        builder.warnings.append(Warning(code="invalid_printed_date", message="A emissao impressa nao esta em uma data/hora brasileira valida.", details={"field": "issue_datetime", "value": issue_raw}))

    dps_label, dps_row = _find_value_row(layout, "NÚMERO DA DPS", after=number_row or (number_label or -1))
    dps_number = builder.value("dps.number", dps_row, 0, 145, required=True)
    dps_series = builder.value("dps.series", dps_row, 145, 290, required=True)
    dps_issue_raw = builder.value("dps.issue_datetime", dps_row, 290, 440)
    builder.provenance["dps.id"] = FieldProvenance(justification="O identificador tecnico da DPS nao e impresso no DANFSe.", page_number=layout.page_number)
    builder.provenance["environment"] = FieldProvenance(
        derived_from=["key"], transformation="Digito de ambiente da chave", justification="A chave de acesso e a evidencia fiscal mais especifica do ambiente.", page_number=layout.page_number,
    )
    environment = key[7] if len(key) > 7 else None

    status_label, status_row = _find_value_row(layout, "SITUAÇÃO DA NFS-e", after=dps_row or (dps_label or -1))
    status = builder.value("status", status_row, 145, 290, required=True)
    builder.derived("metadata.generation_environment", environment, "key", transformation="Digito de ambiente da chave", justification="A chave codifica o ambiente de geracao.")

    provider_anchor = layout.find("PRESTADOR / FORNECEDOR DA NFS-e")
    customer_anchor = layout.find("TOMADOR / ADQUIRENTE DA OPERAÇÃO", after=provider_anchor or -1)
    service_anchor = layout.find("SERVIÇO PRESTADO", after=customer_anchor or -1)
    provider = _party(builder, "provider", "PRESTADOR / FORNECEDOR DA NFS-e", after=provider_anchor or -1, before=customer_anchor)
    customer = _party(builder, "customer", "TOMADOR / ADQUIRENTE DA OPERAÇÃO", after=customer_anchor or -1, before=service_anchor)
    builder.provenance["intermediary"] = FieldProvenance(justification="Nenhum intermediario identificado e impresso neste DANFSe.", page_number=layout.page_number)

    service_codes_label, service_codes_row = _find_value_row(layout, "CÓD. TRIBUTAÇÃO NACIONAL / MUNICIPAL", after=service_anchor or -1)
    tax_codes = builder.value("service.national_tax_code", service_codes_row, 0, 190)
    national_code, municipal_code = (None, None)
    if tax_codes:
        code_parts = [part.strip() for part in tax_codes.split("/")]
        national_code = code_parts[0] or None
        municipal_code = code_parts[1] if len(code_parts) > 1 and code_parts[1] else None
    builder.provenance["service.municipal_tax_code"] = FieldProvenance(
        derived_from=["service.national_tax_code"], transformation="Codigos separados da celula Nacional / Municipal", justification="O layout imprime os dois codigos na mesma celula.", page_number=layout.page_number,
        region=builder.provenance["service.national_tax_code"].region, excerpt=tax_codes,
    )
    nbs_code = builder.value("service.nbs_code", service_codes_row, 190, 385)
    location_name = builder.value("locations.service_name", service_codes_row, 385, 595)

    description_label = layout.find("DESCRIÇÃO DO SERVIÇO", after=service_codes_row or (service_codes_label or -1))
    municipal_section = layout.find("TRIBUTAÇÃO MUNICIPAL (ISSQN)", after=description_label or (service_codes_row or -1))
    national_description = _section_text(layout, service_codes_row, description_label)
    national_evidence = builder.evidence((service_codes_row or -1) + 1) if service_codes_row is not None and description_label and service_codes_row + 1 < description_label else None
    builder.provenance["service.national_tax_description"] = national_evidence.provenance(
        transformation="Descricao impressa entre codigos do servico e descricao livre",
    ) if national_evidence else FieldProvenance(justification="Descricao de tributacao nacional nao foi localizada.", page_number=layout.page_number)
    service_description = _section_text(layout, description_label, municipal_section)
    description_evidence = builder.evidence((description_label or -1) + 1) if description_label is not None else None
    builder.provenance["service.description"] = description_evidence.provenance(
        transformation="Linhas entre o rotulo Descricao do servico e a secao tributaria; quebras fisicas foram unidas",
    ) if description_evidence else FieldProvenance(justification="Descricao do servico nao foi localizada.", page_number=layout.page_number)
    if not service_description:
        builder._missing("service.description")
    for field in ("service.municipal_tax_description", "service.nbs_description", "service.contributor_internal_code"):
        builder.provenance[field] = FieldProvenance(justification="Este detalhe nao e impresso no layout DANFSe atual.", page_number=layout.page_number)

    municipality_title = layout.find("TIPO DE TRIBUTAÇÃO DO ISSQN", after=municipal_section or -1)
    taxable_label, taxable_row = _find_value_row(layout, "BC ISSQN", after=municipality_title or (municipal_section or -1))
    taxable_base = builder.money("amounts.taxable_base", taxable_row, 100, 230)
    rate_label, rate_row = _find_value_row(layout, "ALÍQUOTA APLICADA", after=municipality_title or (municipal_section or -1))
    rate = builder.money("amounts.rate", rate_row, 230, 350, percentage=True)
    retention_label, retention_row = _find_value_row(layout, "RETENÇÃO DO ISSQN", after=municipality_title or (municipal_section or -1))
    iss_type = builder.value("retentions.iss_type", retention_row, 350, 465)
    iss_label, iss_row = _find_value_row(layout, "ISSQN APURADO", after=municipality_title or (municipal_section or -1))
    iss = builder.money("amounts.iss", iss_row, 465, 595)
    taxation_label, taxation_row = _find_value_row(layout, "TIPO DE TRIBUTAÇÃO DO ISSQN", after=municipal_section or -1)
    iss_taxation = builder.value("taxes.iss_taxation", taxation_row, 0, 190)
    incidence_label, incidence_row = _find_value_row(layout, "MUNICÍPIO / UF / PAÍS DA INCIDÊNCIA", after=municipal_section or -1)
    incidence_name = builder.value("locations.incidence_name", incidence_row, 190, 385)
    builder.derived("locations.incidence_code", None, "locations.incidence_name", transformation="Sem codigo impresso", justification="O bloco municipal exibe somente o nome da localidade de incidencia.")

    federal_section = layout.find("TRIBUTAÇÃO FEDERAL (EXCETO CBS)", after=municipal_section or -1)
    ir_label, ir_row = _find_value_row(layout, "IRRF", after=federal_section or -1)
    ir = builder.money("retentions.ir", ir_row, 0, 190)
    inss_label, inss_row = _find_value_row(layout, "CONTRIBUIÇÃO PREVIDENCIÁRIA", after=federal_section or -1)
    inss = builder.money("retentions.inss", inss_row, 190, 385)
    pis_label, pis_row = _find_value_row(layout, "PIS - DÉBITO APURAÇÃO PRÓPRIA", after=federal_section or -1)
    pis = builder.money("retentions.pis", pis_row, 0, 190)
    cofins_label, cofins_row = _find_value_row(layout, "COFINS - DÉBITO APURAÇÃO PRÓPRIA", after=federal_section or -1)
    cofins = builder.money("retentions.cofins", cofins_row, 190, 385)
    for field in ("retentions.pis_cofins_type", "retentions.csll", "taxes.pis_cofins_status", "taxes.federal_total", "taxes.state_total", "taxes.municipal_total"):
        builder.provenance[field] = FieldProvenance(justification="O campo nao e impresso de forma individualizada neste DANFSe.", page_number=layout.page_number)

    ibs_section = layout.find("TRIBUTAÇÃO IBS / CBS", after=federal_section or -1)
    total_section = layout.find("VALOR TOTAL DA NFS-e", after=ibs_section or (federal_section or -1))
    incidence_ibs_label, incidence_ibs_row = _find_value_row(layout, "IND. OPERAÇÃO / MUNICÍPIO INCIDÊNCIA", after=ibs_section or -1, before=total_section)
    ibs_incidence = builder.value("taxes.ibs_cbs.incidence_location_code", incidence_ibs_row, 190, 385)
    ibs_location_code = ibs_location_name = None
    if ibs_incidence:
        parts = [part.strip() for part in ibs_incidence.split("/")]
        ibs_location_code = parts[1] if len(parts) > 1 else None
        ibs_location_name = parts[2] if len(parts) > 2 else None
    builder.provenance["taxes.ibs_cbs.incidence_location_name"] = FieldProvenance(
        derived_from=["taxes.ibs_cbs.incidence_location_code"], transformation="Codigo de operacao, municipio e nome separados da mesma celula", justification="O layout imprime esses tres itens juntos.", page_number=layout.page_number,
        region=builder.provenance["taxes.ibs_cbs.incidence_location_code"].region, excerpt=ibs_incidence,
    )
    ibs_base_label, ibs_base_row = _find_value_row(layout, "BC APÓS EXCLUSÕES E REDUÇÕES", after=ibs_section or -1, before=total_section)
    ibs_base = builder.money("taxes.ibs_cbs.taxable_base", ibs_base_row, 0, 145)
    ibs_rate_label, ibs_rate_row = _find_value_row(layout, "ALÍQUOTA IBS UF / MUN", after=ibs_section or -1, before=total_section)
    ibs_rate_text = builder.value("taxes.ibs_cbs.ibs_state_rate", ibs_rate_row, 385, 595, transformation="Aliquotas brasileiras impressas")
    ibs_state_rate = ibs_municipal_rate = None
    if ibs_rate_text:
        parts = [part.strip() for part in ibs_rate_text.split("/")]
        ibs_state_rate = _decimal(parts[0], percentage=True) if parts else None
        ibs_municipal_rate = _decimal(parts[1], percentage=True) if len(parts) > 1 else None
    builder.provenance["taxes.ibs_cbs.ibs_municipal_rate"] = FieldProvenance(
        derived_from=["taxes.ibs_cbs.ibs_state_rate"], transformation="Aliquotas UF e Municipio separadas da mesma celula", justification="O layout imprime as aliquotas juntas.", page_number=layout.page_number,
        region=builder.provenance["taxes.ibs_cbs.ibs_state_rate"].region, excerpt=ibs_rate_text,
    )
    municipal_eff_label, municipal_eff_row = _find_value_row(layout, "ALÍQ. EFETIVA MUNICIPAL - IBS", after=ibs_section or -1, before=total_section)
    ibs_municipal_effective_rate = builder.money("taxes.ibs_cbs.ibs_municipal_effective_rate", municipal_eff_row, 0, 145, percentage=True)
    municipal_value_label, municipal_value_row = _find_value_row(layout, "VALOR APURADO MUNICIPAL - IBS", after=ibs_section or -1, before=total_section)
    ibs_municipal = builder.money("taxes.ibs_cbs.ibs_municipal", municipal_value_row, 145, 290)
    state_eff_label, state_eff_row = _find_value_row(layout, "ALÍQ. EFETIVA ESTADUAL - IBS", after=ibs_section or -1, before=total_section)
    ibs_state_effective_rate = builder.money("taxes.ibs_cbs.ibs_state_effective_rate", state_eff_row, 290, 440, percentage=True)
    state_value_label, state_value_row = _find_value_row(layout, "VALOR APURADO ESTADUAL - IBS", after=ibs_section or -1, before=total_section)
    ibs_state = builder.money("taxes.ibs_cbs.ibs_state", state_value_row, 440, 595)
    ibs_total_label, ibs_total_row = _find_value_row(layout, "VALOR TOTAL APURADO - IBS", after=ibs_section or -1, before=total_section)
    ibs_total = builder.money("taxes.ibs_cbs.ibs_total", ibs_total_row, 0, 145)
    cbs_rate_label, cbs_rate_row = _find_value_row(layout, "ALÍQUOTA - CBS", after=ibs_section or -1, before=total_section)
    cbs_rate = builder.money("taxes.ibs_cbs.cbs_rate", cbs_rate_row, 145, 290, percentage=True)
    cbs_eff_label, cbs_eff_row = _find_value_row(layout, "ALÍQUOTA EFETIVA - CBS", after=ibs_section or -1, before=total_section)
    cbs_effective_rate = builder.money("taxes.ibs_cbs.cbs_effective_rate", cbs_eff_row, 290, 440, percentage=True)
    cbs_label, cbs_row = _find_value_row(layout, "VALOR TOTAL APURADO - CBS", after=ibs_section or -1, before=total_section)
    cbs = builder.money("taxes.ibs_cbs.cbs", cbs_row, 440, 595)
    has_ibs = any(value is not None for value in (ibs_base, ibs_state_rate, ibs_municipal_rate, ibs_total, cbs))

    gross_label, gross_row = _find_value_row(layout, "VALOR DO SERVIÇO", after=total_section or -1)
    gross = builder.money("amounts.gross", gross_row, 0, 145, required=True)
    unconditional_label, unconditional_row = _find_value_row(layout, "DESCONTO INCONDICIONADO", after=total_section or -1)
    unconditional_discount = builder.money("amounts.unconditional_discount", unconditional_row, 145, 290)
    conditional_label, conditional_row = _find_value_row(layout, "DESCONTO CONDICIONADO", after=total_section or -1)
    conditional_discount = builder.money("amounts.conditional_discount", conditional_row, 290, 440)
    retained_label, retained_row = _find_value_row(layout, "TOTAL DAS RETENÇÕES", after=total_section or -1)
    retained_total = builder.money("amounts.retained_total", retained_row, 440, 595)
    net_label, net_row = _find_value_row(layout, "VALOR LÍQUIDO DA NFS-e", after=total_section or -1)
    net = builder.money("amounts.net", net_row, 0, 190)
    total_ibs_label, total_ibs_row = _find_value_row(layout, "TOTAL DO IBS/CBS", after=total_section or -1)
    summary_ibs = builder.money("taxes.ibs_cbs.summary_total", total_ibs_row, 190, 385)
    invoice_label, invoice_row = _find_value_row(layout, "VALOR LÍQUIDO + IBS/CBS", after=total_section or -1)
    invoice_total = builder.money("taxes.ibs_cbs.invoice_total", invoice_row, 385, 595)
    # The printed total keeps the tax total verifiable even if a detailed
    # sub-block is omitted by a municipality.  The detail remains preferred.
    if has_ibs and ibs_total is None and summary_ibs is not None:
        ibs_total = builder.derived("taxes.ibs_cbs.ibs_total", summary_ibs, "taxes.ibs_cbs.summary_total", transformation="Total IBS/CBS impresso", justification="O detalhamento IBS nao trouxe total proprio.")

    if not has_ibs:
        ibs_cbs = None
        builder.provenance["taxes.ibs_cbs"] = FieldProvenance(justification="O bloco IBS/CBS esta presente no formulario, mas nao possui valores impressos.", page_number=layout.page_number)
    else:
        ibs_cbs = {
            "incidence_location_code": ibs_location_code,
            "incidence_location_name": ibs_location_name,
            "taxable_base": ibs_base,
            "ibs_state_rate": ibs_state_rate,
            "ibs_state_effective_rate": ibs_state_effective_rate,
            "ibs_municipal_rate": ibs_municipal_rate,
            "ibs_municipal_effective_rate": ibs_municipal_effective_rate,
            "cbs_rate": cbs_rate,
            "cbs_effective_rate": cbs_effective_rate,
            "invoice_total": invoice_total,
            "ibs_total": ibs_total,
            "ibs_state": ibs_state,
            "ibs_municipal": ibs_municipal,
            "cbs": cbs,
            "declaration": None,
        }
        builder.provenance["taxes.ibs_cbs.declaration"] = FieldProvenance(justification="DANFSe e representacao visual; nao contem a declaracao XML IBS/CBS.", page_number=layout.page_number)

    builder.derived("amounts.currency", "BRL", "amounts.gross", transformation="Moeda legal do DANFSe nacional", justification="Os valores impressos usam R$.")
    builder.provenance["metadata.nfse_id"] = FieldProvenance(justification="O Id tecnico NFS nao e impresso; apenas a chave e exibida.", page_number=layout.page_number)
    builder.derived("metadata.application_version", version, "version", transformation="Versao DANFSe do cabecalho", justification="O PDF nao informa a versao do schema XML.")
    for field in ("metadata.emission_type", "metadata.processing_datetime", "metadata.generated_document_number", "metadata.signature_reference", "metadata.digest_value", "metadata.signature_value", "metadata.certificate"):
        builder.provenance[field] = FieldProvenance(justification="Este metadado tecnico nao e impresso no DANFSe.", page_number=layout.page_number)
    builder.provenance["metadata.unknown_groups"] = FieldProvenance(justification="DANFSe nao possui grupos XML para enumerar.", page_number=layout.page_number)
    builder.provenance["dps.issue_datetime"] = builder.provenance["dps.issue_datetime"]

    document = {
        "document_type": "nfse",
        "layout": "national",
        "version": version,
        "key": key,
        "number": number,
        "dps": {"id": None, "number": dps_number, "series": dps_series},
        "environment": environment,
        "status": status,
        "issue_datetime": issue_datetime,
        "competence_date": competence_date,
        "provider": provider,
        "customer": customer,
        "intermediary": None,
        "locations": {
            "issue_code": builder.derived("locations.issue_code", key[:7] if len(key) >= 7 else None, "key", transformation="Sete primeiros digitos da chave", justification="A chave nacional codifica o municipio emissor."),
            "issue_name": builder.derived("locations.issue_name", builder.value("locations.issue_name.printed", layout.find("DANFSe"), 500, 595), "locations.issue_name.printed", transformation="Municipio do cabecalho DANFSe", justification="O DANFSe identifica o municipio emissor no cabecalho."),
            "service_code": None,
            "service_name": location_name,
            "incidence_code": None,
            "incidence_name": incidence_name,
        },
        "service": {
            "national_tax_code": national_code,
            "municipal_tax_code": municipal_code,
            "nbs_code": nbs_code,
            "national_tax_description": national_description,
            "municipal_tax_description": None,
            "nbs_description": None,
            "description": service_description,
            "contributor_internal_code": None,
        },
        "amounts": {
            "currency": "BRL", "gross": gross, "taxable_base": taxable_base, "rate": rate, "iss": iss,
            "retained_total": retained_total, "unconditional_discount": unconditional_discount,
            "conditional_discount": conditional_discount, "net": net,
        },
        "retentions": {"iss_type": iss_type, "pis_cofins_type": None, "pis": pis, "cofins": cofins, "csll": None, "inss": inss, "ir": ir},
        "taxes": {"iss_taxation": iss_taxation, "pis_cofins_status": None, "federal_total": None, "state_total": None, "municipal_total": None, "ibs_cbs": ibs_cbs},
        "metadata": {
            "nfse_id": None, "application_version": version, "generation_environment": environment,
            "emission_type": None, "processing_datetime": None, "generated_document_number": None,
            "signature_reference": None, "digest_value": None, "signature_value": None, "certificate": None, "unknown_groups": [],
        },
        "provenance": builder.provenance,
    }
    return FiscalDocument.model_validate(document), builder.warnings
