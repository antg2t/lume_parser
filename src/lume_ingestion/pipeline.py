from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from lume_ingestion import parsers as _registered_parsers  # noqa: F401
from lume_ingestion.artifacts import OutputDirectory, read_json, write_json
from lume_ingestion.bank_statement import normalize_bank_statement, reconcile_bank_statement
from lume_ingestion.bank_statement_spreadsheet import normalize_spreadsheet_bank_statement
from lume_ingestion.format_registry import FormatMatch, extract_bank_statement, extract_cash_ledger, match_from_layout_map
from lume_ingestion.accounting import normalize_accounting_history, normalize_chart_of_accounts
from lume_ingestion.cash_ledger import (
    normalize_cash_ledger_pdf,
    normalize_cash_ledger_xlsx,
    validate_cash_ledger_collection,
)
from lume_ingestion.detection import Detection, FileTypeDetector
from lume_ingestion.errors import IngestionFailure
from lume_ingestion.files import DEFAULT_MAX_FILE_SIZE, require_regular_file, sha256_file, source_file
from lume_ingestion.models import (
    ArtifactPaths,
    BankStatement,
    AccountingHistory,
    ChartOfAccounts,
    CashLedgerCollection,
    Error,
    FiscalDocument,
    IngestionResult,
    SourceFile,
    Warning,
)
from lume_ingestion.nfse import normalize_nfse, validate_fiscal_document
from lume_ingestion.parsers.nfse_pdf import normalize_danfse
from lume_ingestion.parsers.pdf import PdfLimits
from lume_ingestion.registry import registry


def inspect(path: str | Path, max_size: int = DEFAULT_MAX_FILE_SIZE) -> Detection:
    return FileTypeDetector().inspect(path, max_size=max_size)


def extract(
    path: str | Path,
    output_root: str | Path = "output",
    max_size: int = DEFAULT_MAX_FILE_SIZE,
    max_pages: int = 500,
    layout_map: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], OutputDirectory]:
    detection = inspect(path, max_size=max_size)
    parser = registry.get(detection.format)
    destination = OutputDirectory(output_root, detection.source)
    raw = parser.extract(
        Path(detection.source.path),
        detection.source,
        list(detection.warnings),
        PdfLimits(max_pages=max_pages),
    )
    if layout_map is not None:
        if detection.format not in {"pdf", "xlsx", "xls", "csv"}:
            raise IngestionFailure("layout_map_invalid", "Mapa de colunas so pode ser aplicado a arquivo tabular.")
        matched = match_from_layout_map(raw, layout_map)
        raw["document_type"] = matched.family
        raw["document_recognition"] = matched.as_recognition()
        raw.pop("layout_proposal", None)
    write_json(destination.path / "raw.json", raw)
    if isinstance(raw.get("pages"), list):
        destination.write_page_texts(raw["pages"])
    return raw, destination


def normalize(raw_path: str | Path, output_path: str | Path | None = None) -> dict[str, Any]:
    started = perf_counter()
    raw = read_json(raw_path)
    required = ("source", "source_format", "parser", "parser_version")
    missing = [field for field in required if field not in raw]
    if missing:
        raise IngestionFailure("invalid_raw", "O RAW nao possui todos os campos obrigatorios.", missing=missing)
    if raw.get("source_format") == "pdf" and raw.get("document_type") == "nfse":
        fiscal_document, parser_warnings = normalize_danfse(raw)
        normalized = {
            "schema_version": "1.0",
            "source": raw["source"],
            "source_format": "pdf",
            "document_type": "nfse",
            "parser": raw["parser"],
            "parser_version": raw["parser_version"],
            "classification": raw["classification"],
            "requires_ocr": raw["requires_ocr"],
            "fiscal_document": fiscal_document.model_dump(mode="json"),
            "warnings": [*raw.get("warnings", []), *(warning.model_dump(mode="json") for warning in parser_warnings)],
            "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") in {"pdf", "xlsx", "xls", "csv"} and raw.get("document_type") == "bank_statement" and isinstance(raw.get("document_recognition"), dict) and raw["document_recognition"].get("extractor") in {"format_registry", "layout_map"}:
        bank_statement, parser_warnings = extract_bank_statement(raw, FormatMatch.from_recognition(raw, raw["document_recognition"]))
        normalized = {
            "schema_version": "1.0",
            "source": raw["source"],
            "source_format": raw["source_format"],
            "document_type": "bank_statement",
            "parser": raw["parser"],
            "parser_version": raw["parser_version"],
            "classification": raw.get("classification"),
            "requires_ocr": bool(raw.get("requires_ocr")),
            "bank_statement": bank_statement.model_dump(mode="json"),
            "warnings": [*raw.get("warnings", []), *(warning.model_dump(mode="json") for warning in parser_warnings)],
            "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") == "pdf" and raw.get("document_type") == "bank_statement":
        bank_statement, parser_warnings = normalize_bank_statement(raw)
        normalized = {
            "schema_version": "1.0",
            "source": raw["source"],
            "source_format": "pdf",
            "document_type": "bank_statement",
            "parser": raw["parser"],
            "parser_version": raw["parser_version"],
            "classification": raw["classification"],
            "requires_ocr": raw["requires_ocr"],
            "bank_statement": bank_statement.model_dump(mode="json"),
            "warnings": [*raw.get("warnings", []), *(warning.model_dump(mode="json") for warning in parser_warnings)],
            "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") in {"xlsx", "xls", "csv"} and raw.get("document_type") == "bank_statement":
        bank_statement, parser_warnings = normalize_spreadsheet_bank_statement(raw)
        normalized = {
            "schema_version": "1.0",
            "source": raw["source"],
            "source_format": raw["source_format"],
            "document_type": "bank_statement",
            "parser": raw["parser"],
            "parser_version": raw["parser_version"],
            "classification": None,
            "requires_ocr": False,
            "bank_statement": bank_statement.model_dump(mode="json"),
            "warnings": [*raw.get("warnings", []), *(warning.model_dump(mode="json") for warning in parser_warnings)],
            "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") == "pdf" and raw.get("document_type") == "cash_ledger":
        cash_ledger, parser_warnings = normalize_cash_ledger_pdf(raw)
        normalized = {
            "schema_version": "1.0",
            "source": raw["source"],
            "source_format": "pdf",
            "document_type": "cash_ledger",
            "parser": raw["parser"],
            "parser_version": raw["parser_version"],
            "classification": raw["classification"],
            "requires_ocr": raw["requires_ocr"],
            "cash_ledger": cash_ledger.model_dump(mode="json"),
            "warnings": [*raw.get("warnings", []), *(warning.model_dump(mode="json") for warning in parser_warnings)],
            "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") == "pdf":
        if not isinstance(raw.get("pages"), list):
            raise IngestionFailure("invalid_raw", "O RAW de PDF nao possui paginas.")
        pages = [
            {
                "page_number": page["page_number"],
                "text": page.get("text", {}).get("basic") or page.get("text", {}).get("layout") or "",
                "metrics": page["metrics"],
            }
            for page in raw["pages"]
        ]
        normalized = {
            "schema_version": "1.0",
            "source": raw["source"],
            "source_format": raw["source_format"],
            "document_type": None,
            "parser": raw["parser"],
            "parser_version": raw["parser_version"],
            "classification": raw["classification"],
            "requires_ocr": raw["requires_ocr"],
            "page_count": len(pages),
            "pages": pages,
            "warnings": raw.get("warnings", []),
            "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") == "xml" and raw.get("document_type") == "nfse":
        fiscal_document = normalize_nfse(raw)
        normalized = {
            "schema_version": "1.0",
            "source": raw["source"],
            "source_format": "xml",
            "document_type": "nfse",
            "parser": raw["parser"],
            "parser_version": raw["parser_version"],
            "classification": None,
            "requires_ocr": False,
            "fiscal_document": fiscal_document.model_dump(mode="json"),
            "warnings": raw.get("warnings", []),
            "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") in {"pdf", "xlsx", "xls", "csv"} and raw.get("document_type") == "cash_ledger" and isinstance(raw.get("document_recognition"), dict) and raw["document_recognition"].get("extractor") in {"format_registry", "layout_map"}:
        cash_ledger, parser_warnings = extract_cash_ledger(raw, FormatMatch.from_recognition(raw, raw["document_recognition"]))
        normalized = {
            "schema_version": "1.0",
            "source": raw["source"],
            "source_format": raw["source_format"],
            "document_type": "cash_ledger",
            "parser": raw["parser"],
            "parser_version": raw["parser_version"],
            "classification": raw.get("classification"),
            "requires_ocr": bool(raw.get("requires_ocr")),
            "cash_ledger": cash_ledger.model_dump(mode="json"),
            "warnings": [*raw.get("warnings", []), *(warning.model_dump(mode="json") for warning in parser_warnings)],
            "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") == "xlsx" and raw.get("document_type") == "cash_ledger":
        cash_ledger, parser_warnings = normalize_cash_ledger_xlsx(raw)
        normalized = {
            "schema_version": "1.0",
            "source": raw["source"],
            "source_format": "xlsx",
            "document_type": "cash_ledger",
            "parser": raw["parser"],
            "parser_version": raw["parser_version"],
            "classification": None,
            "requires_ocr": False,
            "cash_ledger": cash_ledger.model_dump(mode="json"),
            "warnings": [*raw.get("warnings", []), *(warning.model_dump(mode="json") for warning in parser_warnings)],
            "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") == "xlsx" and raw.get("document_type") == "chart_of_accounts":
        chart = normalize_chart_of_accounts(raw)
        normalized = {
            "schema_version": "1.0", "source": raw["source"], "source_format": "xlsx", "document_type": "chart_of_accounts",
            "parser": raw["parser"], "parser_version": raw["parser_version"], "classification": None, "requires_ocr": False,
            "chart_of_accounts": chart.model_dump(mode="json"), "warnings": raw.get("warnings", []), "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") == "xlsx" and raw.get("document_type") == "accounting_history":
        history = normalize_accounting_history(raw)
        normalized = {
            "schema_version": "1.0", "source": raw["source"], "source_format": "xlsx", "document_type": "accounting_history",
            "parser": raw["parser"], "parser_version": raw["parser_version"], "classification": None, "requires_ocr": False,
            "accounting_history": history.model_dump(mode="json"), "warnings": raw.get("warnings", []), "errors": raw.get("errors", []),
            "normalization_duration_ms": round((perf_counter() - started) * 1000),
        }
    elif raw.get("source_format") in {"pdf", "xlsx", "xls", "csv"} and raw.get("document_type") is None and raw.get("layout_proposal"):
        raise IngestionFailure(
            "spreadsheet_columns_not_mapped",
            "Os titulos foram lidos, mas ainda precisam ser associados.",
            layout=raw.get("layout_proposal") or {},
        )
    else:
        raise IngestionFailure(
            "unsupported_raw_format",
            "Nao existe normalizador para o RAW informado.",
            source_format=raw.get("source_format"),
            document_type=raw.get("document_type"),
        )
    destination = Path(output_path) if output_path else Path(raw_path).with_name("normalized.json")
    write_json(destination, normalized)
    return normalized


def validate(normalized_path: str | Path, output_path: str | Path | None = None) -> IngestionResult:
    started = perf_counter()
    normalized = read_json(normalized_path)
    errors = [Error.model_validate(error) for error in normalized.get("errors", [])]
    warnings = [Warning.model_validate(warning) for warning in normalized.get("warnings", [])]
    source_format = normalized.get("source_format")
    pages: list[dict[str, Any]] = []
    fiscal_data: dict[str, Any] | None = None
    bank_statement_data: dict[str, Any] | None = None
    cash_ledger_data: dict[str, Any] | None = None
    chart_of_accounts_data: dict[str, Any] | None = None
    accounting_history_data: dict[str, Any] | None = None
    if source_format in {"xml", "pdf"} and normalized.get("document_type") == "nfse":
        try:
            fiscal_document = FiscalDocument.model_validate(normalized.get("fiscal_document"))
            validate_fiscal_document(fiscal_document, require_complete=source_format != "pdf")
            fiscal_data = fiscal_document.model_dump(mode="json")
        except ValidationError as exc:
            raise IngestionFailure(
                "invalid_fiscal_document",
                "A saida fiscal normalizada e invalida.",
                reason=str(exc),
            ) from exc
    elif source_format in {"xlsx", "xls", "csv", "pdf"} and normalized.get("document_type") == "cash_ledger":
        try:
            cash_ledger = CashLedgerCollection.model_validate(normalized.get("cash_ledger"))
        except ValidationError as exc:
            raise IngestionFailure(
                "invalid_cash_ledger",
                "A saida normalizada de controle de caixa e invalida.",
                reason=str(exc),
            ) from exc
        errors.extend(validate_cash_ledger_collection(cash_ledger))
        cash_ledger_data = cash_ledger.model_dump(mode="json")
    elif source_format in {"pdf", "xlsx", "xls", "csv"} and normalized.get("document_type") == "bank_statement":
        try:
            bank_statement = BankStatement.model_validate(normalized.get("bank_statement"))
        except ValidationError as exc:
            raise IngestionFailure(
                "invalid_bank_statement",
                "A saida normalizada de extrato bancario e invalida.",
                reason=str(exc),
            ) from exc
        for reconciliation_warning in reconcile_bank_statement(bank_statement):
            if not any(
                warning.code == reconciliation_warning.code and warning.details == reconciliation_warning.details
                for warning in warnings
            ):
                warnings.append(reconciliation_warning)
        bank_statement_data = bank_statement.model_dump(mode="json")
    elif source_format == "xlsx" and normalized.get("document_type") == "chart_of_accounts":
        try:
            chart_of_accounts_data = ChartOfAccounts.model_validate(normalized.get("chart_of_accounts")).model_dump(mode="json")
        except ValidationError as exc:
            raise IngestionFailure("invalid_chart_of_accounts", "A saida normalizada do plano de contas e invalida.", reason=str(exc)) from exc
    elif source_format == "xlsx" and normalized.get("document_type") == "accounting_history":
        try:
            accounting_history_data = AccountingHistory.model_validate(normalized.get("accounting_history")).model_dump(mode="json")
        except ValidationError as exc:
            raise IngestionFailure("invalid_accounting_history", "A saida normalizada do historico contabil e invalida.", reason=str(exc)) from exc
    elif source_format == "pdf":
        candidate_pages = normalized.get("pages")
        if not isinstance(candidate_pages, list) or not candidate_pages:
            errors.append(Error(code="no_pages", message="A saida normalizada nao possui paginas."))
        else:
            pages = candidate_pages
        for page in pages:
            metrics = page.get("metrics", {})
            if not metrics.get("has_useful_text", False):
                errors.append(
                    Error(
                        code="page_without_useful_text",
                        message="A pagina nao possui texto util nesta release.",
                        details={"page_number": page.get("page_number")},
                    )
                )
    else:
        raise IngestionFailure(
            "unsupported_normalized_format",
            "Nao existe validador para a saida normalizada.",
            source_format=source_format,
        )
    try:
        source = SourceFile.model_validate(normalized.get("source"))
    except ValidationError as exc:
        raise IngestionFailure("invalid_normalized", "Os dados do arquivo de origem sao invalidos.", reason=str(exc)) from exc

    destination = Path(output_path) if output_path else Path(normalized_path).with_name("result.json")
    raw_path = Path(normalized_path).with_name("raw.json")
    page_paths = sorted(str(path.resolve()) for path in Path(normalized_path).parent.glob("page-*.txt"))
    duration = round((perf_counter() - started) * 1000)
    result = IngestionResult(
        success=not errors,
        source=source,
        source_format=source_format,
        document_type=normalized.get("document_type"),
        parser=normalized.get("parser"),
        parser_version=normalized.get("parser_version"),
        duration_ms=duration,
        validation_duration_ms=duration,
        classification=normalized.get("classification"),
        requires_ocr=bool(normalized.get("requires_ocr")),
        outputs=ArtifactPaths(
            raw_json=str(raw_path.resolve()) if raw_path.exists() else None,
            normalized_json=str(Path(normalized_path).resolve()),
            result_json=str(destination.resolve()),
            page_texts=page_paths,
        ),
        warnings=warnings,
        errors=errors,
        data=fiscal_data or bank_statement_data or cash_ledger_data or chart_of_accounts_data or accounting_history_data or {
            "page_count": len(pages),
            "useful_page_count": sum(bool(page.get("metrics", {}).get("has_useful_text")) for page in pages),
        },
    )
    write_json(destination, result.model_dump(mode="json"))
    return result


def _failure_result(
    failure: IngestionFailure,
    path: str | Path,
    output_root: str | Path,
    started: float,
    detection: Detection | None = None,
) -> IngestionResult:
    source: SourceFile | None = detection.source if detection else None
    if source is None:
        candidate = Path(path).expanduser().resolve()
        if candidate.is_file():
            try:
                digest = sha256_file(candidate)
                source = source_file(candidate, digest)
            except OSError:
                pass
    parser_name: str | None = None
    parser_version: str | None = None
    if detection and detection.format:
        try:
            parser = registry.get(detection.format)
            parser_name, parser_version = parser.name, parser.version
        except IngestionFailure:
            pass
    result = IngestionResult(
        success=False,
        source=source,
        source_format=detection.format if detection else None,
        parser=parser_name,
        parser_version=parser_version,
        duration_ms=round((perf_counter() - started) * 1000),
        warnings=list(detection.warnings) if detection else [],
        errors=[Error(code=failure.code, message=failure.message, details=failure.details)],
    )
    if source is not None:
        destination = OutputDirectory(output_root, source)
        artifacts = destination.artifacts
        result.outputs = ArtifactPaths(
            raw_json=str(Path(artifacts.raw_json).resolve()) if artifacts.raw_json else None,
            normalized_json=str(Path(artifacts.normalized_json).resolve()) if artifacts.normalized_json else None,
            result_json=str((destination.path / "result.json").resolve()),
            page_texts=[str(Path(page).resolve()) for page in artifacts.page_texts],
        )
        write_json(destination.path / "result.json", result.model_dump(mode="json"))
    return result


def run_pipeline(
    path: str | Path,
    output_root: str | Path = "output",
    max_size: int = DEFAULT_MAX_FILE_SIZE,
    max_pages: int = 500,
    layout_map: dict[str, Any] | None = None,
) -> IngestionResult:
    started = perf_counter()
    detection: Detection | None = None
    try:
        detection = inspect(path, max_size=max_size)
        raw, destination = extract(path, output_root, max_size=max_size, max_pages=max_pages, layout_map=layout_map)
        normalized = normalize(destination.path / "raw.json")
        result = validate(destination.path / "normalized.json")
        result.extraction_duration_ms = int(raw.get("extraction_duration_ms", 0))
        result.normalization_duration_ms = int(normalized.get("normalization_duration_ms", 0))
        result.duration_ms = round((perf_counter() - started) * 1000)
        write_json(destination.path / "result.json", result.model_dump(mode="json"))
        return result
    except IngestionFailure as failure:
        return _failure_result(failure, path, output_root, started, detection)
    except Exception as exc:
        # A malformed third-party document must never abort an entire batch.
        # Keep the unexpected exception observable without exposing a stack
        # trace or treating it as a successful ingestion.
        failure = IngestionFailure(
            "unexpected_processing_error",
            "O processamento falhou de forma inesperada; o lote continuou.",
            exception_type=type(exc).__name__,
            reason=str(exc),
        )
        return _failure_result(failure, path, output_root, started, detection)


def discover_files(paths: list[str | Path]) -> list[Path]:
    discovered: set[Path] = set()
    for supplied in paths:
        candidate = Path(supplied).expanduser().resolve()
        if candidate.is_file():
            discovered.add(candidate)
        elif candidate.is_dir():
            discovered.update(path for path in candidate.rglob("*") if path.is_file())
        else:
            # Mantem o caminho para que o pipeline devolva file_not_found estruturado.
            discovered.add(candidate)
    return sorted(discovered, key=lambda path: str(path).casefold())


def semantic_payload(value: Any) -> Any:
    """Remove apenas tempos volateis para comparacoes de reprodutibilidade."""
    if isinstance(value, dict):
        return {
            key: semantic_payload(item)
            for key, item in value.items()
            if not key.endswith("duration_ms")
        }
    if isinstance(value, list):
        return [semantic_payload(item) for item in value]
    return value
