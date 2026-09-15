"""Deterministic regression runner for the local document collection.

The manifest identifies a fixture by its SHA-256, never by the directory or
filename in which it happens to be stored.  This lets the same baseline prove
classification after a collection is reorganised or files are renamed.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter
from typing import Any

from lume_ingestion.artifacts import read_json
from lume_ingestion.cash_ledger import compare_cash_ledger_collections
from lume_ingestion.errors import IngestionFailure
from lume_ingestion.files import sha256_file
from lume_ingestion.pipeline import run_pipeline


DOCUMENT_EXTENSIONS = frozenset({".pdf", ".xml", ".xlsx", ".xls"})
MISSING = object()


def _manifest_error(message: str, **details: Any) -> IngestionFailure:
    return IngestionFailure("invalid_regression_manifest", message, **details)


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        manifest = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise _manifest_error("O manifesto de regressao nao pode ser lido.", reason=str(exc)) from exc
    if manifest.get("schema_version") != "1.0":
        raise _manifest_error("A versao do manifesto de regressao nao e suportada.")
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise _manifest_error("O manifesto precisa conter ao menos uma fixture.")

    ids: set[str] = set()
    hashes: set[str] = set()
    for index, fixture in enumerate(fixtures):
        if not isinstance(fixture, dict):
            raise _manifest_error("Cada fixture deve ser um objeto.", index=index)
        fixture_id = fixture.get("id")
        digest = fixture.get("sha256")
        if not isinstance(fixture_id, str) or not fixture_id:
            raise _manifest_error("A fixture nao possui id.", index=index)
        if fixture_id in ids:
            raise _manifest_error("Os ids das fixtures devem ser unicos.", id=fixture_id)
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise _manifest_error("A fixture nao possui SHA-256 valido.", id=fixture_id)
        if digest in hashes:
            raise _manifest_error("Cada fixture precisa de um SHA-256 exclusivo.", sha256=digest)
        for required in ("expected_source_format", "expected_parser", "golden", "golden_kind"):
            if required not in fixture:
                raise _manifest_error("A fixture nao possui todos os campos obrigatorios.", id=fixture_id, field=required)
        ids.add(fixture_id)
        hashes.add(digest)
    return manifest


def _inventory(root: Path) -> dict[str, list[Path]]:
    if not root.is_dir():
        raise IngestionFailure("fixtures_root_not_found", "O diretorio do acervo nao existe.", path=str(root))
    indexed: dict[str, list[Path]] = defaultdict(list)
    for candidate in sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: str(path).casefold()):
        indexed[sha256_file(candidate)].append(candidate)
    return dict(indexed)


def _dotted(value: Any, field: str) -> Any:
    current = value
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cash_ledger_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    fields = ("date", "issue_date", "document", "counterparty", "notes", "inflow", "outflow", "balance")
    ledgers = data.get("ledgers", [])
    entries = [{field: entry.get(field) for field in fields} for ledger in ledgers for entry in ledger.get("entries", [])]
    return {
        "entry_count": len(entries),
        "ledger_entry_counts": [len(ledger.get("entries", [])) for ledger in ledgers],
        "entries_sha256": _digest(entries),
        "first_entry": entries[0] if entries else None,
        "last_entry": entries[-1] if entries else None,
    }


def _bank_statement_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    transaction_fields = (
        "date", "description", "counterparty", "counterparty_tax_id", "document", "amount", "transaction_type", "balance"
    )
    transactions = [{field: item.get(field) for field in transaction_fields} for item in data.get("transactions", [])]
    daily_balances = [{field: item.get(field) for field in ("date", "balance")} for item in data.get("daily_balances", [])]
    return {
        "transaction_count": len(transactions),
        "transactions_sha256": _digest(transactions),
        "daily_balance_count": len(daily_balances),
        "daily_balances_sha256": _digest(daily_balances),
        "initial_balance": data.get("initial_balance"),
        "final_balance": data.get("final_balance"),
        "period_start": data.get("period_start"),
        "period_end": data.get("period_end"),
        "first_transaction": transactions[0] if transactions else None,
        "last_transaction": transactions[-1] if transactions else None,
    }


def _chart_of_accounts_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    fields = ("code", "description", "reduced_code")
    accounts = [{field: account.get(field) for field in fields} for account in data.get("accounts", [])]
    return {
        "account_count": len(accounts),
        "accounts_sha256": _digest(accounts),
        "first_account": accounts[0] if accounts else None,
        "last_account": accounts[-1] if accounts else None,
    }


def _accounting_history_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    fields = ("entry_number", "date", "debit_account", "credit_account", "amount", "standard_history", "complement", "debit_cost_center", "credit_cost_center", "tax_id")
    entries = [{field: entry.get(field) for field in fields} for entry in data.get("entries", [])]
    return {
        "entry_count": len(entries),
        "entries_sha256": _digest(entries),
        "first_entry": entries[0] if entries else None,
        "last_entry": entries[-1] if entries else None,
    }


def _golden_expectations(fixture: dict[str, Any], result_data: dict[str, Any], manifest_path: Path) -> dict[str, tuple[Any, Any]]:
    golden_reference = fixture["golden"]
    if not isinstance(golden_reference, str):
        raise _manifest_error("A referencia da golden deve ser texto.", id=fixture["id"])
    golden_path = (manifest_path.parent.parent / golden_reference).resolve()
    try:
        golden = read_json(golden_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise _manifest_error("A golden associada nao pode ser lida.", id=fixture["id"], golden=golden_reference, reason=str(exc)) from exc

    kind = fixture["golden_kind"]
    if kind in {"fiscal", "pdf_textual"}:
        selector = fixture.get("golden_selector")
        if not isinstance(selector, str) or not isinstance(golden.get(selector), dict):
            raise _manifest_error("A golden nao possui a chave da fixture.", id=fixture["id"], selector=selector)
        expected = golden[selector]
        expectations = {field: (value, _dotted(result_data, field)) for field, value in expected.items()}
    elif kind == "cash_ledger":
        actual = _cash_ledger_snapshot(result_data)
        expectations = {field: (value, actual.get(field, MISSING)) for field, value in golden.items()}
    elif kind == "bank_statement":
        actual = _bank_statement_snapshot(result_data)
        expectations = {field: (value, actual.get(field, MISSING)) for field, value in golden.items()}
    elif kind == "chart_of_accounts":
        actual = _chart_of_accounts_snapshot(result_data)
        expectations = {field: (value, actual.get(field, MISSING)) for field, value in golden.items()}
    elif kind == "accounting_history":
        actual = _accounting_history_snapshot(result_data)
        expectations = {field: (value, actual.get(field, MISSING)) for field, value in golden.items()}
    else:
        raise _manifest_error("O tipo de golden nao e suportado.", id=fixture["id"], golden_kind=kind)
    fields = fixture.get("golden_fields")
    if fields is not None:
        if not isinstance(fields, list) or not all(isinstance(field, str) and field in expectations for field in fields):
            raise _manifest_error("Os campos selecionados da golden sao invalidos.", id=fixture["id"])
        return {field: expectations[field] for field in fields}
    return expectations


def _render_value(value: Any) -> Any:
    return "<ausente>" if value is MISSING else value


def _field_checks(fixture: dict[str, Any], result_data: dict[str, Any], manifest_path: Path) -> list[dict[str, Any]]:
    expectations = _golden_expectations(fixture, result_data, manifest_path)
    return [
        {"field": field, "expected": expected, "found": _render_value(found), "matches": found is not MISSING and found == expected}
        for field, (expected, found) in sorted(expectations.items())
    ]


def _expected_checks(fixture: dict[str, Any], result: Any, source_unchanged: bool) -> list[dict[str, Any]]:
    expected_success = bool(fixture.get("expected_success", True))
    expected = {
        "success": expected_success,
        "source_format": fixture["expected_source_format"],
        "document_type": fixture.get("expected_document_type"),
        "parser": fixture["expected_parser"],
        "requires_ocr": bool(fixture.get("expected_requires_ocr", False)),
        "sha256": fixture["sha256"],
        "source_unchanged": True,
    }
    found = {
        "success": result.success,
        "source_format": result.source_format,
        "document_type": result.document_type,
        "parser": result.parser,
        "requires_ocr": result.requires_ocr,
        "sha256": result.source.sha256 if result.source else None,
        "source_unchanged": source_unchanged,
    }
    expected_error = fixture.get("expected_error_code")
    if expected_error is not None:
        expected["error_code"] = expected_error
        found["error_code"] = result.errors[0].code if result.errors else None
    return [
        {"field": field, "expected": value, "found": found[field], "matches": found[field] == value}
        for field, value in expected.items()
    ]


def _coverage(documents: list[dict[str, Any]]) -> dict[str, Any]:
    type_matrix: dict[str, Counter[str]] = defaultdict(Counter)
    field_matrix: dict[str, Counter[str]] = defaultdict(Counter)
    for document in documents:
        document_type = document["expected"]["document_type"] or "unclassified_pdf"
        type_counts = type_matrix[document_type]
        type_counts["fixtures"] += 1
        type_counts["found"] += int(document["actual"]["document_type"] == document["expected"]["document_type"])
        type_counts["passed"] += int(document["matches_expectation"])
        for check in document["field_checks"]:
            key = f"{document_type}.{check['field']}"
            counts = field_matrix[key]
            counts["expected"] += 1
            counts["found"] += int(check["found"] != "<ausente>")
            counts["matched"] += int(check["matches"])
            counts["missing"] += int(check["found"] == "<ausente>")
            counts["mismatched"] += int(not check["matches"] and check["found"] != "<ausente>")
    return {
        "document_types": {key: dict(value) for key, value in sorted(type_matrix.items())},
        "fields": {key: dict(value) for key, value in sorted(field_matrix.items())},
    }


def run_regression(
    fixtures_root: str | Path,
    manifest_path: str | Path,
    output_root: str | Path = "output/regression",
    max_size: int = 50 * 1024 * 1024,
    max_pages: int = 500,
) -> dict[str, Any]:
    """Run the declared collection and return a JSON-serialisable report."""

    started = perf_counter()
    root = Path(fixtures_root).expanduser().resolve()
    manifest_file = Path(manifest_path).expanduser().resolve()
    manifest = load_manifest(manifest_file)
    inventory = _inventory(root)
    documents: list[dict[str, Any]] = []
    results_by_id: dict[str, Any] = {}
    manifest_hashes = {fixture["sha256"] for fixture in manifest["fixtures"]}

    for fixture in sorted(manifest["fixtures"], key=lambda item: item["id"]):
        candidates = inventory.get(fixture["sha256"], [])
        expected = {
            "source_format": fixture["expected_source_format"],
            "document_type": fixture.get("expected_document_type"),
            "parser": fixture["expected_parser"],
            "requires_ocr": bool(fixture.get("expected_requires_ocr", False)),
            "golden": fixture["golden"],
        }
        if not candidates:
            field_checks = _field_checks(fixture, {}, manifest_file)
            documents.append(
                {
                    "fixture_id": fixture["id"],
                    "manifest_path": fixture.get("path"),
                    "resolved_path": None,
                    "duplicate_sources": [],
                    "expected": expected,
                    "actual": {"success": False, "source_format": None, "document_type": None, "parser": None, "requires_ocr": False},
                    "stage_durations_ms": {"extraction": 0, "normalization": 0, "validation": 0, "total": 0},
                    "warnings": [],
                    "errors": [{"code": "fixture_not_found", "message": "Nenhum arquivo no acervo possui o SHA-256 declarado."}],
                    "field_checks": field_checks,
                    "expected_checks": [{"field": "fixture_present", "expected": True, "found": False, "matches": False}],
                    "ocr": {"required": False, "used": False},
                    "ai": {"used": False},
                    "matches_expectation": False,
                }
            )
            continue

        source = sorted(candidates, key=lambda item: str(item).casefold())[0]
        result = run_pipeline(source, output_root, max_size=max_size, max_pages=max_pages)
        source_unchanged = sha256_file(source) == fixture["sha256"]
        expected_checks = _expected_checks(fixture, result, source_unchanged)
        field_checks = _field_checks(fixture, result.data, manifest_file) if result.success else []
        matches = all(check["matches"] for check in expected_checks) and all(check["matches"] for check in field_checks)
        results_by_id[fixture["id"]] = result
        documents.append(
            {
                "fixture_id": fixture["id"],
                "manifest_path": fixture.get("path"),
                "resolved_path": str(source),
                "duplicate_sources": [str(item) for item in candidates[1:]],
                "expected": expected,
                "actual": {
                    "success": result.success,
                    "source_format": result.source_format,
                    "document_type": result.document_type,
                    "parser": result.parser,
                    "requires_ocr": result.requires_ocr,
                },
                "stage_durations_ms": {
                    "extraction": result.extraction_duration_ms,
                    "normalization": result.normalization_duration_ms,
                    "validation": result.validation_duration_ms,
                    "total": result.duration_ms,
                },
                "warnings": [warning.model_dump(mode="json") for warning in result.warnings],
                "errors": [error.model_dump(mode="json") for error in result.errors],
                "field_checks": field_checks,
                "expected_checks": expected_checks,
                "ocr": {"required": result.requires_ocr, "used": False},
                "ai": {"used": False},
                "matches_expectation": matches,
            }
        )

    cross_validations: list[dict[str, Any]] = []
    for validation in manifest.get("cross_validations", []):
        validation_id = validation.get("id", "unnamed")
        reference_id = validation.get("reference_fixture")
        candidate_id = validation.get("candidate_fixture")
        reference = results_by_id.get(reference_id)
        candidate = results_by_id.get(candidate_id)
        if not reference or not candidate or not reference.success or not candidate.success:
            cross_validations.append(
                {"id": validation_id, "kind": validation.get("kind"), "matches": False, "differences": [{"code": "comparison_unavailable"}]}
            )
        elif validation.get("kind") == "cash_ledger_equivalence":
            comparison = compare_cash_ledger_collections(reference.data, candidate.data)
            cross_validations.append({"id": validation_id, "kind": "cash_ledger_equivalence", **comparison})
        else:
            cross_validations.append(
                {"id": validation_id, "kind": validation.get("kind"), "matches": False, "differences": [{"code": "unknown_cross_validation"}]}
            )

    material_files = [
        path
        for candidates in inventory.values()
        for path in candidates
        if path.suffix.lower() in DOCUMENT_EXTENSIONS
    ]
    untracked = sorted(
        {
            str(path.relative_to(root)).replace("\\", "/")
            for digest, candidates in inventory.items()
            if digest not in manifest_hashes
            for path in candidates
            if path.suffix.lower() in DOCUMENT_EXTENSIONS
        },
        key=str.casefold,
    )
    coverage = _coverage(documents)
    warning_count = sum(len(document["warnings"]) for document in documents)
    error_count = sum(len(document["errors"]) for document in documents)
    passed = sum(document["matches_expectation"] for document in documents)
    succeeded = sum(document["actual"]["success"] for document in documents)
    source_integrity = all(
        next((check["matches"] for check in document["expected_checks"] if check["field"] == "source_unchanged"), False)
        for document in documents
        if document["resolved_path"] is not None
    )
    overall_success = (
        passed == len(documents)
        and source_integrity
        and all(validation["matches"] for validation in cross_validations)
    )
    return {
        "schema_version": "1.0",
        "success": overall_success,
        "fixtures_root": str(root),
        "manifest": str(manifest_file),
        "summary": {
            "declared_fixtures": len(documents),
            "material_files_found": len(material_files),
            "succeeded": succeeded,
            "failed": len(documents) - succeeded,
            "passed": passed,
            "regressions": len(documents) - passed,
            "duration_ms": round((perf_counter() - started) * 1000),
            "warnings": warning_count,
            "errors": error_count,
            "ocr": {"required": sum(document["ocr"]["required"] for document in documents), "used": 0},
            "ai": {"used": 0},
            "source_integrity": source_integrity,
        },
        "coverage": coverage,
        "documents": documents,
        "cross_validations": cross_validations,
        "untracked_files": untracked,
        "known_limitations": manifest.get("known_limitations", []),
    }


def markdown_report(report: dict[str, Any]) -> str:
    """Render the portable human-readable counterpart of the JSON report."""

    summary = report["summary"]
    result = "APROVADA" if report["success"] else "REPROVADA"
    lines = [
        "# Relatório de regressão do acervo",
        "",
        f"**Resultado:** {result}",
        "",
        f"- Fixtures declaradas: {summary['declared_fixtures']}",
        f"- Sucesso do pipeline: {summary['succeeded']}; falhas: {summary['failed']}",
        f"- Conformes com a baseline: {summary['passed']}; regressões: {summary['regressions']}",
        f"- Duração total: {summary['duration_ms']} ms",
        f"- Warnings: {summary['warnings']}; erros: {summary['errors']}",
        f"- OCR — solicitado: {summary['ocr']['required']}; usado: {summary['ocr']['used']}",
        f"- IA usada: {summary['ai']['used']}",
        f"- Integridade das fontes: {'ok' if summary['source_integrity'] else 'falhou'}",
        "",
        "## Cobertura por tipo documental",
        "",
        "| Tipo | Fixtures | Tipo encontrado | Baseline aprovada |",
        "| --- | ---: | ---: | ---: |",
    ]
    for document_type, counts in report["coverage"]["document_types"].items():
        lines.append(f"| {document_type} | {counts['fixtures']} | {counts['found']} | {counts['passed']} |")
    lines.extend(["", "## Cobertura por campo", "", "| Campo | Esperados | Encontrados | Corretos | Ausentes | Divergentes |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for field, counts in report["coverage"]["fields"].items():
        lines.append(
            f"| {field} | {counts['expected']} | {counts['found']} | {counts['matched']} | {counts['missing']} | {counts['mismatched']} |"
        )
    lines.extend(["", "## Validações cruzadas", "", "| Validação | Resultado |", "| --- | --- |"])
    for validation in report["cross_validations"]:
        lines.append(f"| {validation['id']} | {'ok' if validation['matches'] else 'falhou'} |")
    lines.extend(["", "## Limitações conhecidas", ""])
    limitations = list(report["known_limitations"])
    if report["untracked_files"]:
        limitations.append({"id": "untracked_files", "description": "Arquivos materiais fora da baseline: " + ", ".join(report["untracked_files"])})
    if limitations:
        for limitation in limitations:
            description = limitation.get("description", str(limitation)) if isinstance(limitation, dict) else str(limitation)
            lines.append(f"- {description}")
    else:
        lines.append("- Nenhuma.")
    return "\n".join(lines) + "\n"
