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
from lume_ingestion.format_registry import layout_proposal_from_extract, match_extract
from lume_ingestion.models import PageMetrics, SourceFile, Warning
from lume_ingestion.parsers.nfse_pdf import is_national_danfse
from lume_ingestion.parsers.pdf_ai import (
    extract_bank_statement_with_xai,
    vision_recognition,
    vision_warning,
)
from lume_ingestion.parsers.pdf_recover import recover_unusable_pages, valid_character_ratio


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
    version = "0.3.5"

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
        widths: list[float] = []
        heights: list[float] = []
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
                    widths.append(float(page.width or 595.0))
                    heights.append(float(page.height or 842.0))
                    pages.append(
                        {
                            "page_number": index + 1,
                            "text": {"basic": basic_text, "layout": layout_text},
                            "words": words,
                            "tables": tables,
                            "images": images,
                        }
                    )
        except IngestionFailure:
            raise
        except Exception as exc:
            raise IngestionFailure("pdf_layout_extraction_failed", "Falha ao extrair o layout do PDF.", reason=str(exc)) from exc

        pages, recovery_methods = recover_unusable_pages(content, pages, widths=widths, heights=heights)
        if "cid" in recovery_methods:
            warnings.append(
                Warning(
                    code="cid_decoded",
                    message="Texto CID sem ToUnicode foi decodificado para os adapters existentes.",
                )
            )
        if "ocr" in recovery_methods:
            warnings.append(
                Warning(
                    code="ocr_applied",
                    message="Paginas sem texto util foram recuperadas por OCR para os adapters existentes.",
                )
            )
        elif "ocr_unavailable" in recovery_methods or "ocr_failed" in recovery_methods:
            warnings.append(
                Warning(
                    code="ocr_unavailable" if "ocr_unavailable" in recovery_methods else "ocr_recommended",
                    message="Paginas sem texto util precisariam de OCR, mas a recuperacao nao esteve disponivel.",
                )
            )

        metrics: list[PageMetrics] = []
        for index, page in enumerate(pages):
            text_obj = page.get("text") if isinstance(page.get("text"), dict) else {}
            words = page.get("words") if isinstance(page.get("words"), list) else []
            word_text = " ".join(str(word.get("text") or "") for word in words if isinstance(word, dict))
            layout_text = str(text_obj.get("layout") or "")
            basic_text = str(text_obj.get("basic") or "")
            candidates = [layout_text, word_text, basic_text]
            metric_text = max(candidates, key=_valid_character_ratio) if any(candidates) else ""
            ratio = _valid_character_ratio(metric_text)
            word_count = len(re.findall(r"\S+", metric_text))
            images = page.get("images") if isinstance(page.get("images"), list) else []
            metric = PageMetrics(
                page_number=index + 1,
                characters=len(metric_text),
                words=word_count,
                valid_character_ratio=ratio,
                image_count=len(images),
                has_images=bool(images),
                has_useful_text=(
                    len(metric_text) >= limits.minimum_useful_characters
                    and ratio >= limits.minimum_valid_ratio
                ),
            )
            metrics.append(metric)
            page["metrics"] = metric.model_dump(mode="json")

        classification, requires_ocr = _classification(metrics)
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
        ai_statement = None
        if document_type is None:
            matched = match_extract({"pages": pages, "source_format": "pdf", "source": source.model_dump(mode="json")}, source.name)
            if matched:
                recognition = matched.as_recognition()
                document_type = matched.family
        if document_type is None:
            extracted = extract_bank_statement_with_xai(content)
            if extracted is not None:
                statement, payload = extracted
                ai_statement = payload
                recognition = vision_recognition(statement)
                document_type = "bank_statement"
                requires_ocr = False
                warnings.append(vision_warning())
        elif requires_ocr and "ocr" not in recovery_methods:
            warnings.append(
                Warning(
                    code="ocr_recommended",
                    message="Uma ou mais paginas ainda nao possuem texto util apos a recuperacao.",
                )
            )
        raw = {
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
            "ai_statement": ai_statement,
            "pages": pages,
            "warnings": [warning.model_dump(mode="json") for warning in warnings],
            "errors": [],
        }
        if document_type is None and any(page.get("tables") for page in pages):
            proposal = layout_proposal_from_extract(raw)
            if len(proposal.get("headers") or []) >= 2:
                raw["layout_proposal"] = proposal
        return raw
