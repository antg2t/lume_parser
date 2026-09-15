from __future__ import annotations

import re
from pathlib import Path
from time import perf_counter
from typing import Any

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException

from lume_ingestion.errors import IngestionFailure
from lume_ingestion.models import SourceFile, Warning


NFSE_NAMESPACE = "http://www.sped.fazenda.gov.br/nfse"
SIGNATURE_NAMESPACE = "http://www.w3.org/2000/09/xmldsig#"
SUPPORTED_VERSIONS = {"1.01"}
XML_ADAPTERS = {(NFSE_NAMESPACE, "NFSe", "1.01"): "nfse-national-v1.01"}


def split_tag(tag: str) -> tuple[str | None, str]:
    if tag.startswith("{") and "}" in tag:
        namespace, local_name = tag[1:].split("}", 1)
        return namespace, local_name
    return None, tag


def element_to_raw(element: Any) -> dict[str, Any]:
    namespace, name = split_tag(element.tag)
    attributes: dict[str, str] = {}
    for key, value in element.attrib.items():
        attribute_namespace, attribute_name = split_tag(key)
        rendered = f"{{{attribute_namespace}}}{attribute_name}" if attribute_namespace else attribute_name
        attributes[rendered] = value
    text = (element.text or "").strip() or None
    return {
        "name": name,
        "namespace": namespace,
        "attributes": attributes,
        "text": text,
        "children": [element_to_raw(child) for child in element],
    }


def parse_xml(content: bytes) -> Any:
    try:
        return SafeElementTree.fromstring(content)
    except DefusedXmlException as exc:
        raise IngestionFailure(
            "unsafe_xml",
            "O XML contem DTD, entidade ou construcao externa proibida.",
            reason=type(exc).__name__,
        ) from exc
    except SafeElementTree.ParseError as exc:
        raise IngestionFailure("invalid_xml", "O XML nao esta bem formado.", reason=str(exc)) from exc


def decode_xml(content: bytes) -> tuple[str, str]:
    declaration = re.match(br"\s*<\?xml[^>]*encoding=[\"']([^\"']+)[\"']", content[:256], re.IGNORECASE)
    encoding = declaration.group(1).decode("ascii", errors="replace") if declaration else "UTF-8"
    try:
        return content.decode(encoding), encoding
    except (LookupError, UnicodeDecodeError) as exc:
        raise IngestionFailure("invalid_xml_encoding", "A codificacao declarada pelo XML e invalida.", encoding=encoding) from exc


def _find_first(root: Any, namespace: str, local_name: str) -> Any | None:
    return root.find(f".//{{{namespace}}}{local_name}")


class NationalNfseXmlParser:
    name = "nfse-national-xml"
    version = "0.1.0"

    def extract(
        self,
        path: Path,
        source: SourceFile,
        warnings: list[Warning],
        limits: Any = None,
    ) -> dict[str, Any]:
        del limits
        started = perf_counter()
        content = path.read_bytes()
        root = parse_xml(content)
        namespace, root_name = split_tag(root.tag)
        version = root.attrib.get("versao")

        if root_name != "NFSe":
            raise IngestionFailure("unsupported_xml_root", "A raiz XML nao e NFSe.", root=root_name)
        if namespace != NFSE_NAMESPACE:
            raise IngestionFailure(
                "unknown_xml_namespace",
                "O namespace da NFS-e nao e o padrao nacional suportado.",
                namespace=namespace,
            )
        if version not in SUPPORTED_VERSIONS:
            raise IngestionFailure(
                "unsupported_nfse_version",
                "A versao da NFS-e Nacional nao possui adaptador exato.",
                version=version,
                supported=sorted(SUPPORTED_VERSIONS),
            )
        adapter = XML_ADAPTERS.get((namespace, root_name, version))
        if adapter is None:
            raise IngestionFailure(
                "unsupported_xml_layout",
                "Nao existe adaptador exato para o leiaute XML identificado.",
                namespace=namespace,
                root=root_name,
                version=version,
            )

        original, encoding = decode_xml(content)
        signature = _find_first(root, SIGNATURE_NAMESPACE, "Signature")
        digests = [
            (element.text or "").strip()
            for element in root.findall(f".//{{{SIGNATURE_NAMESPACE}}}DigestValue")
            if (element.text or "").strip()
        ]
        known_inf_groups = {
            "xLocEmi", "xLocPrestacao", "nNFSe", "cLocIncid", "xLocIncid", "xTribNac",
            "xTribMun", "xNBS", "verAplic", "ambGer", "tpEmis", "cStat", "dhProc", "nDFSe",
            "emit", "valores", "IBSCBS", "DPS",
        }
        inf_nfse = root.find(f"{{{namespace}}}infNFSe")
        unknown_groups = []
        if inf_nfse is not None:
            unknown_groups = sorted(
                {split_tag(child.tag)[1] for child in inf_nfse if split_tag(child.tag)[1] not in known_inf_groups}
            )

        return {
            "schema_version": "1.0",
            "source": source.model_dump(mode="json"),
            "source_format": "xml",
            "document_type": "nfse",
            "parser": self.name,
            "parser_version": self.version,
            "adapter": adapter,
            "extraction_duration_ms": round((perf_counter() - started) * 1000),
            "xml": {
                "root": root_name,
                "namespace": namespace,
                "version": version,
                "encoding": encoding,
                "original": original,
                "tree": element_to_raw(root),
                "signature": element_to_raw(signature) if signature is not None else None,
                "digest_values": digests,
                "unknown_groups": unknown_groups,
            },
            "warnings": [warning.model_dump(mode="json") for warning in warnings],
            "errors": [],
        }
