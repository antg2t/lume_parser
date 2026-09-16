from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import pdfplumber
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from lume_ingestion.errors import IngestionFailure
from lume_ingestion.bank_statement import recognize_bank_statement
from lume_ingestion.cash_ledger import recognize_cash_ledger_pdf
from lume_ingestion.models import PageMetrics, SourceFile, Warning
from lume_ingestion.parsers.nfse_pdf import is_national_danfse
from lume_ingestion.parsers.pdf_recover import (
    page_needs_recovery,
    recover_page,
    tesseract_available,
    valid_character_ratio,
)


@dataclass(frozen=True)
class PdfLimits:
    max_pages: int = 500
    minimum_useful_characters: int = 20
    minimum_valid_ratio: float = 0.85


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _valid_character_ratio(text: str) -> float:
    return valid_character_ratio(text)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _classification(metrics: list[PageMetrics]) -> tuple[str, bool]:
    useful_pages = sum(metric.has_useful_text for metric in metrics)
    if useful_pages == len(metrics):
        return "textual", False
    if useful_pages > 0:
        return "hybrid", True
    return "probable_image", True


class PdfTextParser:
    name = "pdf-text"
    version = "0.2.0"

    def extract(
        self,
        path: Path,
        source: SourceFile,
        warnings: list[Warning],
        limits: PdfLimits | None = None,
    ) -> dict[str, Any]:
        limits = limits or PdfLimits()
        started = perf_counter()
        content = path.read_bytes()
        if b"%PDF-" not in content[:1024]:
            raise IngestionFailure("invalid_pdf_signature", "A assinatura binaria de PDF e invalida.")
        if b"%%EOF" not in content[-8192:]:
            raise IngestionFailure("truncated_pdf", "O marcador final do PDF nao foi encontrado; o arquivo pode estar truncado.")

        try:
            reader = PdfReader(io.BytesIO(content), strict=False)
        except (PdfReadError, ValueError, TypeError, OSError) as exc:
            raise IngestionFailure("invalid_pdf", "O PDF nao pode ser aberto.", reason=str(exc)) from exc

        if reader.is_encrypted:
            raise IngestionFailure("encrypted_pdf", "O PDF e protegido por criptografia e nao sera processado nesta release.")
        page_count = len(reader.pages)
        if page_count == 0:
            raise IngestionFailure("empty_pdf", "O PDF nao possui paginas.")
        if page_count > limits.max_pages:
            raise IngestionFailure(
                "page_limit_exceeded",
                "O PDF excede o limite de paginas.",
                page_count=page_count,
                max_pages=limits.max_pages,
            )

        basic_texts: list[str] = []
        try:
            for page in reader.pages:
                basic_texts.append(_clean_text(page.extract_text()))
        except Exception as exc:
            raise IngestionFailure("pdf_text_extraction_failed", "Falha ao extrair o texto basico do PDF.", reason=str(exc)) from exc

        pages: list[dict[str, Any]] = []
        metrics: list[PageMetrics] = []
        try:
            with pdfplumber.open(io.BytesIO(content)) as document:
                if len(document.pages) != page_count:
                    warnings.append(
                        Warning(
                            code="page_count_disagreement",
                            message="As bibliotecas de PDF divergiram sobre o numero de paginas.",
                            details={"pypdf": page_count, "pdfplumber": len(document.pages)},
                        )
                    )
                for index, page in enumerate(document.pages):
                    basic_text = basic_texts[index] if index < len(basic_texts) else ""
                    layout_text = _clean_text(page.extract_text(layout=True))
                    words = _json_safe(page.extract_words(keep_blank_chars=False))
                    tables = _json_safe(page.extract_tables())
                    images = _json_safe(page.images)
                    metric_text = basic_text or layout_text
                    ratio = _valid_character_ratio(metric_text)
                    word_count = len(re.findall(r"\S+", metric_text))
                    has_useful_text = (
                        len(metric_text) >= limits.minimum_useful_characters
                        and ratio >= limits.minimum_valid_ratio
                    )
                    metric = PageMetrics(
                        page_number=index + 1,
                        characters=len(metric_text),
                        words=word_count,
                        valid_character_ratio=ratio,
                        image_count=len(images),
                        has_images=bool(images),
                        has_useful_text=has_useful_text,
                    )
                    metrics.append(metric)
                    pages.append(
                        {
                            "page_number": index + 1,
                            "text": {"basic": basic_text, "layout": layout_text},
                            "words": words,
                            "tables": tables,
                            "images": images,
                            "metrics": metric.model_dump(mode="json"),
                        }
                    )
        except IngestionFailure:
            raise
        except Exception as exc:
            raise IngestionFailure("pdf_layout_extraction_failed", "Falha ao extrair o layout do PDF.", reason=str(exc)) from exc

        recovered_any = False
        recovery_attempted = False
        for index, page in enumerate(pages):
            if not page_needs_recovery(
                page,
                minimum_useful_characters=limits.minimum_useful_characters,
                minimum_valid_ratio=limits.minimum_valid_ratio,
            ):
                continue
            recovery_attempted = True
            try:
                box = reader.pages[index].mediabox
            except (IndexError, TypeError, ValueError):
                continue
            recovered = recover_page(
                content,
                index,
                page_width=float(box.width),
                page_height=float(box.height),
            )
            if recovered is None:
                continue
            page["text"] = {"basic": recovered.layout_text, "layout": recovered.layout_text}
            page["words"] = recovered.words
            page["recovery"] = recovered.method
            metric_text = recovered.layout_text
            ratio = valid_character_ratio(metric_text)
            word_count = len(re.findall(r"\S+", metric_text))
            has_useful_text = (
                len(metric_text) >= limits.minimum_useful_characters
                and ratio >= limits.minimum_valid_ratio
            )
            metric = PageMetrics(
                page_number=index + 1,
                characters=len(metric_text),
                words=word_count,
                valid_character_ratio=ratio,
                image_count=int(page["metrics"].get("image_count") or 0),
                has_images=bool(page["metrics"].get("has_images")),
                has_useful_text=has_useful_text,
            )
            metrics[index] = metric
            page["metrics"] = metric.model_dump(mode="json")
            recovered_any = True
            warnings.append(
                Warning(
                    code="ocr_applied",
                    message="Texto util recuperado por OCR nesta pagina digitalizada.",
                    details={"page_number": index + 1, "method": recovered.method},
                )
            )

        classification, requires_ocr = _classification(metrics)
        if requires_ocr and not recovered_any:
            warnings.append(
                Warning(
                    code="ocr_recommended" if tesseract_available() or not recovery_attempted else "ocr_unavailable",
                    message=(
                        "Uma ou mais paginas nao possuem texto util; OCR e recomendado, mas nao foi executado."
                        if tesseract_available() or not recovery_attempted
                        else "Uma ou mais paginas nao possuem texto util e o Tesseract nao esta disponivel."
                    ),
                )
            )

        metadata = {str(key).lstrip("/"): _json_safe(value) for key, value in (reader.metadata or {}).items()}
        nfse_recognition = is_national_danfse(pages)
        bank_statement_recognition = recognize_bank_statement(pages)
        cash_ledger_recognition = recognize_cash_ledger_pdf(pages)
        recognition = nfse_recognition or bank_statement_recognition or cash_ledger_recognition
        document_type = (
            "nfse"
            if nfse_recognition
            else "bank_statement"
            if bank_statement_recognition
            else "cash_ledger"
            if cash_ledger_recognition
            else None
        )
        return {
            "schema_version": "1.0",
            "source": source.model_dump(mode="json"),
            "source_format": "pdf",
            "document_type": document_type,
            "parser": self.name,
            "parser_version": self.version,
            "extraction_duration_ms": round((perf_counter() - started) * 1000),
            "pdf": {"page_count": page_count, "metadata": metadata},
            "classification": classification,
            "requires_ocr": requires_ocr,
            "document_recognition": recognition,
            "pages": pages,
            "warnings": [warning.model_dump(mode="json") for warning in warnings],
            "errors": [],
        }
