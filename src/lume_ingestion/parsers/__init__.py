"""Parsers registrados por formato."""

from lume_ingestion.parsers.pdf import PdfTextParser
from lume_ingestion.parsers.nfse_xml import NationalNfseXmlParser
from lume_ingestion.parsers.nfse_pdf import normalize_danfse
from lume_ingestion.parsers.xls import XlsParser
from lume_ingestion.parsers.xlsx import XlsxParser
from lume_ingestion.parsers.csv import CsvParser
from lume_ingestion.registry import registry

registry.register("pdf", PdfTextParser)
registry.register("xml", NationalNfseXmlParser)
registry.register("xlsx", XlsxParser)
registry.register("xls", XlsParser)
registry.register("csv", CsvParser)

__all__ = ["CsvParser", "NationalNfseXmlParser", "PdfTextParser", "XlsParser", "XlsxParser", "normalize_danfse"]
