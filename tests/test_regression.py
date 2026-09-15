from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lume_ingestion.cli import main
from lume_ingestion.regression import load_manifest, markdown_report, run_regression


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
MANIFEST = PROJECT_ROOT / "tests" / "fixtures_manifest.json"


def test_manifest_freezes_the_r05_collection() -> None:
    manifest = load_manifest(MANIFEST)

    assert len(manifest["fixtures"]) == 26
    assert len({fixture["sha256"] for fixture in manifest["fixtures"]}) == 26
    assert all(fixture["golden"] for fixture in manifest["fixtures"])
    assert {fixture["expected_source_format"] for fixture in manifest["fixtures"]} == {"pdf", "xml", "xlsx"}


@pytest.mark.integration
def test_r05_regression_reports_the_complete_baseline_and_cross_validation(tmp_path: Path) -> None:
    report = run_regression(DOCS_ROOT, MANIFEST, tmp_path / "output")

    assert report["success"]
    assert report["summary"] == {
        **report["summary"],
        "declared_fixtures": 26,
        "succeeded": 26,
        "failed": 0,
        "passed": 26,
        "regressions": 0,
        "source_integrity": True,
        "ocr": {"required": 0, "used": 0},
        "ai": {"used": 0},
    }
    assert report["cross_validations"] == [
        {
            "id": "controle-caixa-pdf-xlsx",
            "kind": "cash_ledger_equivalence",
            "matches": True,
            "reference_entry_count": 261,
            "candidate_entry_count": 261,
            "differences": [],
        }
    ]
    assert report["coverage"]["document_types"] == {
        "bank_statement": {"fixtures": 1, "found": 1, "passed": 1},
        "cash_ledger": {"fixtures": 2, "found": 2, "passed": 2},
        "chart_of_accounts": {"fixtures": 2, "found": 2, "passed": 2},
        "nfse": {"fixtures": 21, "found": 21, "passed": 21},
    }
    assert "Extrato/xlsx/18-08-2026 - Extrato Bradesco.xls" in report["untracked_files"]
    rendered = markdown_report(report)
    assert "Cobertura por campo" in rendered
    assert "controle-caixa-pdf-xlsx" in rendered


def test_regression_locates_a_renamed_document_by_hash_not_by_folder_or_name(tmp_path: Path) -> None:
    original = next((DOCS_ROOT / "Nota fiscal" / "pdf").glob("*.pdf"))
    renamed = tmp_path / "unrelated" / "nested" / "anything.bin"
    renamed.parent.mkdir(parents=True)
    shutil.copyfile(original, renamed)

    report = run_regression(tmp_path, MANIFEST, tmp_path / "output")
    fixture = next(document for document in report["documents"] if document["fixture_id"].endswith(original.stem))

    assert fixture["resolved_path"] == str(renamed)
    assert fixture["actual"]["document_type"] == "nfse"
    assert fixture["matches_expectation"] is True


def test_recursive_batch_isolates_empty_and_false_extension_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = next((DOCS_ROOT / "Nota fiscal" / "pdf").glob("*.pdf"))
    nested = tmp_path / "any" / "directory"
    nested.mkdir(parents=True)
    shutil.copyfile(source, nested / "renamed.bin")
    (nested / "empty.pdf").touch()
    (nested / "false.pdf").write_text("not a document", encoding="utf-8")

    code = main(["batch", str(tmp_path), "--output", str(tmp_path / "output")])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["processed"] == 3
    assert payload["succeeded"] == 1
    assert payload["failed"] == 2
    assert {result["errors"][0]["code"] for result in payload["results"] if not result["success"]} == {
        "empty_file",
        "unknown_format",
    }
