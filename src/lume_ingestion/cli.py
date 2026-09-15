from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lume_ingestion.artifacts import write_json
from lume_ingestion.cash_ledger import compare_cash_ledger_collections
from lume_ingestion.errors import IngestionFailure
from lume_ingestion.files import DEFAULT_MAX_FILE_SIZE
from lume_ingestion.models import Error, IngestionResult
from lume_ingestion.pipeline import discover_files, extract, inspect, normalize, run_pipeline, validate
from lume_ingestion.regression import markdown_report, run_regression


def _print_json(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _limits(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-size-mb", type=int, default=DEFAULT_MAX_FILE_SIZE // (1024 * 1024))
    parser.add_argument("--max-pages", type=int, default=500)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m lume_ingestion", description="Laboratorio local de ingestao da Lume")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Detecta o formato sem extrair o documento")
    inspect_parser.add_argument("file")
    inspect_parser.add_argument("--max-size-mb", type=int, default=DEFAULT_MAX_FILE_SIZE // (1024 * 1024))

    extract_parser = subparsers.add_parser("extract", help="Gera raw.json e texto por pagina")
    extract_parser.add_argument("file")
    extract_parser.add_argument("--output", default="output")
    _limits(extract_parser)

    normalize_parser = subparsers.add_parser("normalize", help="Transforma raw.json em normalized.json")
    normalize_parser.add_argument("raw_json")
    normalize_parser.add_argument("--output")

    validate_parser = subparsers.add_parser("validate", help="Valida normalized.json e gera result.json")
    validate_parser.add_argument("normalized_json")
    validate_parser.add_argument("--output")

    batch_parser = subparsers.add_parser("batch", help="Executa o pipeline em arquivos ou diretorios")
    batch_parser.add_argument("paths", nargs="+")
    batch_parser.add_argument("--output", default="output")
    batch_parser.add_argument("--summary", help="Caminho opcional para o resumo JSON")
    _limits(batch_parser)

    regression_parser = subparsers.add_parser(
        "regress",
        help="Executa a baseline por SHA-256 e produz relatorios JSON e Markdown",
    )
    regression_parser.add_argument("fixtures_root", nargs="?", default="docs", help="Diretorio do acervo (padrao: docs)")
    regression_parser.add_argument("--manifest", default="tests/fixtures_manifest.json")
    regression_parser.add_argument("--output", default="output/regression")
    regression_parser.add_argument("--report-json", help="Destino do relatorio JSON (padrao: <output>/report.json)")
    regression_parser.add_argument("--report-markdown", help="Destino do relatorio Markdown (padrao: <output>/report.md)")
    _limits(regression_parser)

    compare_parser = subparsers.add_parser(
        "compare-cash-ledger",
        help="Processa um XLSX e um PDF de caixa e aponta cada divergencia entre os lancamentos",
    )
    compare_parser.add_argument("xlsx")
    compare_parser.add_argument("pdf")
    compare_parser.add_argument("--output", default="output")
    _limits(compare_parser)
    return parser


def _failure(exc: IngestionFailure) -> int:
    _print_json(IngestionResult(success=False, errors=[Error(code=exc.code, message=exc.message, details=exc.details)]))
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            detection = inspect(args.file, max_size=args.max_size_mb * 1024 * 1024)
            _print_json(
                {
                    "source": detection.source.model_dump(mode="json"),
                    "source_format": detection.format,
                    "warnings": [warning.model_dump(mode="json") for warning in detection.warnings],
                }
            )
            return 0 if detection.format else 1
        if args.command == "extract":
            raw, destination = extract(
                args.file,
                args.output,
                max_size=args.max_size_mb * 1024 * 1024,
                max_pages=args.max_pages,
            )
            response = {"success": True, "raw_json": str(destination.path / "raw.json")}
            if isinstance(raw.get("pages"), list):
                response["page_count"] = len(raw["pages"])
            _print_json(response)
            return 0
        if args.command == "normalize":
            normalized = normalize(args.raw_json, args.output)
            destination = Path(args.output) if args.output else Path(args.raw_json).with_name("normalized.json")
            response = {"success": True, "normalized_json": str(destination.resolve())}
            if isinstance(normalized.get("pages"), list):
                response["page_count"] = len(normalized["pages"])
            response["document_type"] = normalized.get("document_type")
            _print_json(response)
            return 0
        if args.command == "validate":
            result = validate(args.normalized_json, args.output)
            _print_json(result)
            return 0 if result.success else 1
        if args.command == "batch":
            files = discover_files(args.paths)
            results = [
                run_pipeline(
                    path,
                    args.output,
                    max_size=args.max_size_mb * 1024 * 1024,
                    max_pages=args.max_pages,
                )
                for path in files
            ]
            summary = {
                "success": all(result.success for result in results),
                "processed": len(results),
                "succeeded": sum(result.success for result in results),
                "failed": sum(not result.success for result in results),
                "results": [result.model_dump(mode="json") for result in results],
            }
            if args.summary:
                write_json(args.summary, summary)
            _print_json(summary)
            return 0 if summary["success"] else 1
        if args.command == "regress":
            report = run_regression(
                args.fixtures_root,
                args.manifest,
                args.output,
                max_size=args.max_size_mb * 1024 * 1024,
                max_pages=args.max_pages,
            )
            json_destination = Path(args.report_json) if args.report_json else Path(args.output) / "report.json"
            markdown_destination = Path(args.report_markdown) if args.report_markdown else Path(args.output) / "report.md"
            write_json(json_destination, report)
            markdown_destination.parent.mkdir(parents=True, exist_ok=True)
            markdown_destination.write_text(markdown_report(report), encoding="utf-8", newline="\n")
            _print_json(
                {
                    "success": report["success"],
                    "report_json": str(json_destination.resolve()),
                    "report_markdown": str(markdown_destination.resolve()),
                    "summary": report["summary"],
                }
            )
            return 0 if report["success"] else 1
        if args.command == "compare-cash-ledger":
            xlsx_result = run_pipeline(
                args.xlsx,
                args.output,
                max_size=args.max_size_mb * 1024 * 1024,
                max_pages=args.max_pages,
            )
            pdf_result = run_pipeline(
                args.pdf,
                args.output,
                max_size=args.max_size_mb * 1024 * 1024,
                max_pages=args.max_pages,
            )
            comparison: dict[str, Any] | None = None
            failures = [result for result in (xlsx_result, pdf_result) if not result.success]
            if not failures and xlsx_result.document_type == pdf_result.document_type == "cash_ledger":
                comparison = compare_cash_ledger_collections(xlsx_result.data, pdf_result.data)
            else:
                comparison = {
                    "matches": False,
                    "differences": [
                        {
                            "code": "cash_ledger_comparison_unavailable",
                            "message": "Os dois arquivos precisam ser controles de caixa validos antes da comparacao.",
                        }
                    ],
                }
            response = {
                "success": not failures and bool(comparison["matches"]),
                "xlsx": xlsx_result.model_dump(mode="json"),
                "pdf": pdf_result.model_dump(mode="json"),
                "comparison": comparison,
            }
            _print_json(response)
            return 0 if response["success"] else 1
    except IngestionFailure as exc:
        return _failure(exc)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _print_json(IngestionResult(success=False, errors=[Error(code="invalid_input", message=str(exc))]))
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
