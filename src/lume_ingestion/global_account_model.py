"""Fallback global: texto e direção -> conta semântica -> código local."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import pickle
import platform
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from lume_ingestion.accounting_entries import AccountChoice, CashEvent, plain
from lume_ingestion.artifacts import read_json, write_json
from lume_ingestion.models import AccountingHistory, AccountingHistoryEntry, ChartOfAccounts, ChartOfAccountsEntry
from lume_ingestion.pipeline import run_pipeline


MODEL_SCHEMA = "global-account-counterpart.v1"
TEXT_SCHEMA = "accounting-text-direction.v1"
SEMANTIC_SCHEMA = "chart-description-path.v1"


@dataclass(frozen=True)
class SemanticAccount:
    key: str
    path: str
    description: str
    local_code: str


@dataclass(frozen=True)
class TrainingRow:
    client_id: str
    date: date
    text: str
    direction: str
    semantic_key: str

    @property
    def month(self) -> str:
        return self.date.isoformat()[:7]

    @property
    def features(self) -> str:
        return feature_text(self.text, self.direction)


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    replacements = (
        (r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b|\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", " documento_fiscal "),
        (r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2})\b|\b\d{4}-\d{2}-\d{2}\b", " data "),
        (r"\b(?:0?[1-9]|1[0-2])[/.-](?:\d{4}|\d{2})\b", " competencia "),
        (r"(?:r\$\s*)?\b\d+(?:\.\d{3})*,\d{2}\b|r\$\s*\d+(?:\.\d{2})?\b", " valor "),
        (r"\b((?:nf|nfe|nf-e|fat|fatura|nota)\s*[.:#-]?\s*)\d+\b", r"\1 numero "),
        (r"\b\d{4,}\b", " numero "),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return re.sub(r"\s+", " ", text).strip()


def feature_text(text: object, direction: str) -> str:
    if direction not in {"inflow", "outflow"}:
        raise ValueError("Direction must be inflow or outflow.")
    return f"direcao_{direction} {normalize_text(text)}".strip()


def ancestor_codes(full_code: str) -> list[str]:
    parts = full_code.split(".")
    if len(parts) < 2 and re.fullmatch(r"\d{8}", full_code):
        widths = (1, 1, 1, 2, 3)
        ends, total = [], 0
        for width in widths:
            total += width
            ends.append(total)
        return [full_code[:end] + "0" * (len(full_code) - end) for end in ends]
    if len(parts) < 2:
        return [full_code]
    return [
        ".".join(parts[:depth] + ["0" * len(part) for part in parts[depth:]])
        for depth in range(1, len(parts) + 1)
    ]


def semantic_catalog(accounts: Iterable[ChartOfAccountsEntry]) -> dict[str, SemanticAccount]:
    rows = tuple(accounts)
    descriptions = {row.code: row.description for row in rows}
    catalog: dict[str, SemanticAccount] = {}
    for row in rows:
        if not row.reduced_code:
            continue
        names = [descriptions[code] for code in ancestor_codes(row.code) if code in descriptions]
        normalized_path = " > ".join(filter(None, (plain(name) for name in names))) or plain(row.description)
        digest = hashlib.sha256(f"{SEMANTIC_SCHEMA}\0{normalized_path}".encode()).hexdigest()
        catalog[row.reduced_code] = SemanticAccount(digest, normalized_path, row.description, row.reduced_code)
    return catalog


def financial_codes(catalog: dict[str, SemanticAccount]) -> set[str]:
    terms = ("bancos conta movimento", "aplicacoes de curto prazo")
    return {code for code, account in catalog.items() if any(term in account.path for term in terms)}


def rows_from_history(
    client_id: str,
    history: Iterable[AccountingHistoryEntry],
    accounts: Iterable[ChartOfAccountsEntry],
    before_period: str,
) -> tuple[list[TrainingRow], Counter]:
    catalog = semantic_catalog(accounts)
    financial = financial_codes(catalog)
    audit: Counter = Counter()
    rows: list[TrainingRow] = []
    for entry in history:
        audit["history_rows"] += 1
        if entry.date.isoformat()[:7] >= before_period:
            audit["on_or_after_cutoff"] += 1
            continue
        debit_financial, credit_financial = entry.debit_account in financial, entry.credit_account in financial
        if debit_financial == credit_financial:
            audit["not_single_financial_side"] += 1
            continue
        direction = "inflow" if debit_financial else "outflow"
        counterpart = entry.credit_account if debit_financial else entry.debit_account
        semantic = catalog.get(counterpart)
        if not semantic:
            audit["counterpart_not_in_chart"] += 1
            continue
        if not normalize_text(entry.complement):
            audit["missing_text"] += 1
            continue
        rows.append(TrainingRow(client_id, entry.date, entry.complement or "", direction, semantic.key))
        audit["eligible_rows"] += 1
    return rows, audit


def _model():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import SGDClassifier
    from sklearn.pipeline import FeatureUnion, Pipeline

    vectorizer = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=12_000, sublinear_tf=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, max_features=16_000, sublinear_tf=True)),
    ])
    return Pipeline([
        ("tfidf", vectorizer),
        ("classifier", SGDClassifier(loss="log_loss", alpha=1e-5, max_iter=1_000, tol=1e-3, random_state=42)),
    ])


def _supported(rows: list[TrainingRow], minimum: int = 2) -> list[TrainingRow]:
    support = Counter(row.semantic_key for row in rows)
    return [row for row in rows if support[row.semantic_key] >= minimum]


def fit(rows: list[TrainingRow]):
    eligible = _supported(rows)
    if len({row.semantic_key for row in eligible}) < 2:
        raise ValueError("Training requires at least two semantic accounts with two examples each.")
    model = _model()
    model.fit([row.features for row in eligible], [row.semantic_key for row in eligible])
    return model, eligible


def _score(model, rows: list[TrainingRow]) -> tuple[dict[str, Any], list[tuple[float, bool]]]:
    if not rows:
        return {"n": 0}, []
    probabilities = model.predict_proba([row.features for row in rows])
    classes = list(model.classes_)
    class_set = set(classes)
    correct = 0
    top3_correct = 0
    seen = 0
    observations: list[tuple[float, bool]] = []
    for row, scores in zip(rows, probabilities):
        order = sorted(range(len(classes)), key=lambda index: float(scores[index]), reverse=True)
        predicted = classes[order[0]]
        is_correct = predicted == row.semantic_key
        correct += is_correct
        top3_correct += row.semantic_key in {classes[index] for index in order[:3]}
        seen += row.semantic_key in class_set
        observations.append((float(scores[order[0]]), is_correct))
    return {
        "n": len(rows),
        "accuracy": correct / len(rows),
        "top3_accuracy": top3_correct / len(rows),
        "seen_semantic_account_rate": seen / len(rows),
        "mean_confidence": sum(value for value, _ in observations) / len(observations),
    }, observations


def _load_bundle(artifact: str | Path) -> dict[str, Any]:
    path = Path(artifact)
    model_path = path / "model.pkl" if path.is_dir() else path
    # Pickle is executable: only load artifacts produced locally by this trainer.
    with model_path.open("rb") as stream:
        bundle = pickle.load(stream)
    if bundle.get("metadata", {}).get("model_schema") != MODEL_SCHEMA:
        raise ValueError("Global model schema is incompatible.")
    return bundle


def _validate_prediction_period(metadata: dict[str, Any], period: str) -> None:
    if not re.fullmatch(r"\d{4}-\d{2}", period):
        raise ValueError("period must use YYYY-MM.")
    cutoff = str(metadata.get("trained_before_period") or "")
    if not re.fullmatch(r"\d{4}-\d{2}", cutoff) or cutoff > period:
        raise ValueError(f"Model cutoff {cutoff or 'missing'} is unsafe for period {period}.")


def _threshold(observations: list[tuple[float, bool]], target_precision: float, minimum: int = 20) -> dict[str, Any]:
    chosen: tuple[float, int, float] | None = None
    for threshold in sorted({confidence for confidence, _ in observations}, reverse=True):
        accepted = [correct for confidence, correct in observations if confidence >= threshold]
        precision = sum(accepted) / len(accepted)
        if len(accepted) >= minimum and precision >= target_precision:
            chosen = threshold, len(accepted), precision
    return {
        "target_precision": target_precision,
        "threshold": chosen[0] if chosen else None,
        "accepted": chosen[1] if chosen else 0,
        "precision": chosen[2] if chosen else None,
        "coverage": chosen[1] / len(observations) if chosen and observations else 0.0,
        "observations": len(observations),
    }


def evaluate(rows: list[TrainingRow], target_precision: float = 0.99) -> dict[str, Any]:
    temporal: dict[str, Any] = {"n": 0}
    if rows:
        test_month = max(row.month for row in rows)
        train_rows = [row for row in rows if row.month < test_month]
        test_rows = [row for row in rows if row.month == test_month]
        if train_rows and test_rows:
            temporal_model, temporal_train = fit(train_rows)
            temporal, _ = _score(temporal_model, test_rows)
            temporal.update({"test_month": test_month, "train_rows": len(temporal_train)})

    clients: dict[str, Any] = {}
    held_out_observations: list[tuple[float, bool]] = []
    for client_id in sorted({row.client_id for row in rows}):
        client_rows = [row for row in rows if row.client_id == client_id]
        test_month = max(row.month for row in client_rows)
        train_rows = [row for row in rows if row.client_id != client_id and row.month < test_month]
        test_rows = [row for row in client_rows if row.month == test_month]
        try:
            held_model, held_train = fit(train_rows)
        except ValueError as exc:
            clients[client_id] = {"n": 0, "reason": str(exc)}
            continue
        metrics, observations = _score(held_model, test_rows)
        metrics.update({"test_month": test_month, "train_rows": len(held_train)})
        clients[client_id] = metrics
        held_out_observations.extend(observations)
    return {
        "temporal": temporal,
        "leave_one_client_out": clients,
        "auto_acceptance": _threshold(held_out_observations, target_precision),
    }


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value)
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def build_dataset(manifest_path: Path, imports: Path, before_period: str) -> tuple[list[TrainingRow], dict[str, str], dict[str, Any]]:
    if not re.fullmatch(r"\d{4}-\d{2}", before_period):
        raise ValueError("before_period must use YYYY-MM.")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != "1.0" or not isinstance(manifest.get("clients"), list):
        raise ValueError("Training manifest must have schema_version 1.0 and a clients list.")
    base = manifest_path.resolve().parent
    result_cache: dict[Path, Any] = {}

    def imported(path: Path):
        if path not in result_cache:
            result = run_pipeline(path, imports)
            if not result.success:
                raise ValueError(f"Could not import {path}: {result.errors}")
            result_cache[path] = result
        return result_cache[path]

    rows: list[TrainingRow] = []
    semantic_paths: dict[str, str] = {}
    clients_audit: dict[str, Any] = {}
    seen_clients: set[str] = set()
    for item in manifest["clients"]:
        client_id = str(item.get("client_id") or "").strip()
        if not client_id or client_id in seen_clients:
            raise ValueError("Each manifest client_id must be non-empty and unique.")
        seen_clients.add(client_id)
        chart_path = _resolve(base, str(item.get("chart") or ""))
        chart_result = imported(chart_path)
        if chart_result.document_type != "chart_of_accounts":
            raise ValueError(f"{chart_path} is not a chart of accounts.")
        chart = ChartOfAccounts.model_validate(chart_result.data)
        catalog = semantic_catalog(chart.accounts)
        for account in catalog.values():
            previous = semantic_paths.get(account.key)
            if previous and previous != account.path:
                raise ValueError("Semantic account hash collision detected.")
            semantic_paths[account.key] = account.path

        client_audit: Counter = Counter()
        history_paths = item.get("history")
        if isinstance(history_paths, str):
            history_paths = [history_paths]
        if not isinstance(history_paths, list) or not history_paths:
            raise ValueError(f"Client {client_id} must provide at least one history file.")
        for raw_path in history_paths:
            history_path = _resolve(base, str(raw_path))
            history_result = imported(history_path)
            if history_result.document_type != "accounting_history":
                raise ValueError(f"{history_path} is not accounting history.")
            history = AccountingHistory.model_validate(history_result.data)
            extracted, audit = rows_from_history(client_id, history.entries, chart.accounts, before_period)
            client_audit.update(audit)
            rows.extend(extracted)
        clients_audit[client_id] = dict(client_audit)
    if not rows:
        raise ValueError("No eligible financial history rows were found before the cutoff.")
    return rows, semantic_paths, {
        "clients": clients_audit,
        "source_files": [
            {"path": str(path), "sha256": result.source.sha256 if result.source else None}
            for path, result in sorted(result_cache.items(), key=lambda item: str(item[0]).casefold())
        ],
    }


def train(manifest: Path, output: Path, before_period: str, target_precision: float = 0.99) -> dict[str, Any]:
    if not 0 < target_precision <= 1:
        raise ValueError("target_precision must be greater than zero and at most one.")
    imports = output / "imports"
    rows, semantic_paths, audit = build_dataset(manifest, imports, before_period)
    evaluation = evaluate(rows, target_precision)
    model, fitted_rows = fit(rows)
    dataset_digest = hashlib.sha256("\n".join(
        f"{row.client_id}|{row.date.isoformat()}|{row.features}|{row.semantic_key}" for row in sorted(
            fitted_rows, key=lambda row: (row.client_id, row.date, row.features, row.semantic_key)
        )
    ).encode()).hexdigest()
    model_version = f"global-account-v1-{dataset_digest[:12]}"
    auto_threshold = evaluation["auto_acceptance"]["threshold"]
    metadata = {
        "schema_version": "1.0",
        "model_schema": MODEL_SCHEMA,
        "text_schema": TEXT_SCHEMA,
        "semantic_schema": SEMANTIC_SCHEMA,
        "model_version": model_version,
        "trained_before_period": before_period,
        "training_rows": len(fitted_rows),
        "clients": sorted({row.client_id for row in fitted_rows}),
        "semantic_accounts": len({row.semantic_key for row in fitted_rows}),
        "semantic_paths": {key: semantic_paths[key] for key in sorted({row.semantic_key for row in fitted_rows})},
        "training_data_sha256": dataset_digest,
        "auto_threshold": auto_threshold,
        "target_precision": target_precision,
        "python_version": platform.python_version(),
        "scikit_learn_version": importlib.metadata.version("scikit-learn"),
        "audit": audit,
    }
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / f".model.{os.getpid()}.tmp"
    with temporary.open("wb") as stream:
        pickle.dump({"metadata": metadata, "model": model}, stream, protocol=pickle.HIGHEST_PROTOCOL)
    temporary.replace(output / "model.pkl")
    write_json(output / "metadata.json", metadata)
    write_json(output / "evaluation.json", evaluation)
    (output / "report.md").write_text(render_report(metadata, evaluation), encoding="utf-8", newline="\n")
    return {"metadata": metadata, "evaluation": evaluation}


def _period_after(period: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", period):
        raise ValueError("period must use YYYY-MM.")
    year, month = map(int, period.split("-"))
    return f"{year + (month == 12):04d}-{1 if month == 12 else month + 1:02d}"


def test_artifact(artifact: Path, history_path: Path, chart_path: Path, period: str, output: Path) -> dict[str, Any]:
    imports = output / "imports"
    history_result = run_pipeline(history_path, imports)
    chart_result = run_pipeline(chart_path, imports)
    if not history_result.success or history_result.document_type != "accounting_history":
        raise ValueError(f"Could not import accounting history: {history_result.errors}")
    if not chart_result.success or chart_result.document_type != "chart_of_accounts":
        raise ValueError(f"Could not import chart of accounts: {chart_result.errors}")
    history = AccountingHistory.model_validate(history_result.data)
    chart = ChartOfAccounts.model_validate(chart_result.data)
    rows, audit = rows_from_history("holdout", history.entries, chart.accounts, _period_after(period))
    rows = [row for row in rows if row.month == period]
    if not rows:
        raise ValueError(f"No eligible financial entries were found in {period}.")
    bundle = _load_bundle(artifact)
    model, metadata = bundle["model"], bundle["metadata"]
    _validate_prediction_period(metadata, period)
    metrics, observations = _score(model, rows)
    probabilities = model.predict_proba([row.features for row in rows])
    classes = list(model.classes_)
    local_keys = {account.key for account in semantic_catalog(chart.accounts).values()}
    threshold = float(metadata.get("auto_threshold")) if metadata.get("auto_threshold") is not None else 1.0
    mappable = 0
    decisions: list[tuple[bool, float]] = []
    for scores in probabilities:
        best = max(range(len(classes)), key=lambda index: float(scores[index]))
        maps = classes[best] in local_keys
        mappable += maps
        decisions.append((maps, float(scores[best])))
    accepted_indexes = [
        index for index, (maps, confidence) in enumerate(decisions)
        if maps and confidence >= threshold
    ]
    correct = [observations[index][1] for index in accepted_indexes]
    result = {
        "schema_version": "1.0",
        "model_version": metadata["model_version"],
        "period": period,
        "metrics": {
            **metrics,
            "mappable_prediction_rate": mappable / len(rows),
            "auto_threshold": threshold,
            "auto_accepted": len(accepted_indexes),
            "auto_coverage": len(accepted_indexes) / len(rows),
            "auto_precision": sum(correct) / len(correct) if correct else None,
        },
        "audit": dict(audit),
    }
    write_json(output / "evaluation.json", result)
    metrics = result["metrics"]
    (output / "report.md").write_text(
        "\n".join([
            "# Teste do fallback global",
            "",
            f"- Modelo: `{result['model_version']}`",
            f"- Período: `{period}`",
            f"- Lançamentos financeiros: {metrics['n']}",
            f"- Acurácia top-1: {metrics['accuracy']:.1%}",
            f"- Acurácia top-3: {metrics['top3_accuracy']:.1%}",
            f"- Previsões mapeáveis no plano: {metrics['mappable_prediction_rate']:.1%}",
            f"- Cobertura automática: {metrics['auto_coverage']:.1%}",
            f"- Precisão automática: {metrics['auto_precision']:.1%}" if metrics["auto_precision"] is not None else "- Precisão automática: indisponível",
            "",
        ]),
        encoding="utf-8",
        newline="\n",
    )
    return result


def render_report(metadata: dict[str, Any], evaluation: dict[str, Any]) -> str:
    temporal = evaluation["temporal"]
    acceptance = evaluation["auto_acceptance"]
    lines = [
        "# Fallback contábil global",
        "",
        f"- Modelo: `{metadata['model_version']}`",
        f"- Treinado somente com períodos anteriores a: `{metadata['trained_before_period']}`",
        f"- Linhas de treino: {metadata['training_rows']}",
        f"- Clientes: {len(metadata['clients'])}",
        f"- Contas semânticas: {metadata['semantic_accounts']}",
        "",
        "## Validação temporal",
        "",
        f"- Mês: `{temporal.get('test_month', 'indisponível')}`",
        f"- Linhas: {temporal.get('n', 0)}",
        f"- Acurácia: {temporal.get('accuracy', 0):.1%}",
        f"- Top-3: {temporal.get('top3_accuracy', 0):.1%}",
        "",
        "## Cliente inteiramente fora do treino",
        "",
        "| Cliente | Mês | Linhas | Acurácia | Top-3 | Classe vista |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for client, values in evaluation["leave_one_client_out"].items():
        lines.append(
            f"| {client} | {values.get('test_month', '')} | {values.get('n', 0)} | "
            f"{values.get('accuracy', 0):.1%} | {values.get('top3_accuracy', 0):.1%} | "
            f"{values.get('seen_semantic_account_rate', 0):.1%} |"
        )
    lines += [
        "",
        "## Política de automação",
        "",
        f"- Precisão-alvo: {acceptance['target_precision']:.1%}",
        f"- Threshold: `{acceptance['threshold']}`",
        f"- Cobertura observada: {acceptance['coverage']:.1%}",
        f"- Precisão observada: {acceptance['precision']:.1%}" if acceptance["precision"] is not None else "- Precisão observada: indisponível; manter sugestões em revisão.",
        "",
        "O modelo prevê a identidade semântica da contrapartida. O motor converte essa identidade para o código do plano recebido e se abstém quando o mapeamento é ausente ou ambíguo.",
    ]
    return "\n".join(lines) + "\n"


class GlobalAccountFallback:
    """Callable injected after semantic rules and local-history matching."""

    def __init__(
        self,
        artifact: str | Path,
        accounts: Iterable[ChartOfAccountsEntry],
        period: str,
        auto_threshold: float | None = None,
    ) -> None:
        bundle = _load_bundle(artifact)
        metadata = bundle.get("metadata", {})
        _validate_prediction_period(metadata, period)
        self.model = bundle["model"]
        self.model_version = metadata["model_version"]
        self.semantic_paths = metadata["semantic_paths"]
        configured = metadata.get("auto_threshold") if auto_threshold is None else auto_threshold
        self.auto_threshold = float(configured) if configured is not None else 1.0
        if not 0 <= self.auto_threshold <= 1:
            raise ValueError("Global auto threshold must be between zero and one.")
        grouped: dict[str, list[SemanticAccount]] = {}
        for account in semantic_catalog(accounts).values():
            grouped.setdefault(account.key, []).append(account)
        self.local_accounts = grouped

    def __call__(self, event: CashEvent, direction: str) -> AccountChoice | None:
        if not normalize_text(event.text):
            return None
        probabilities = self.model.predict_proba([feature_text(event.text, direction)])[0]
        classes = list(self.model.classes_)
        best = max(range(len(classes)), key=lambda index: float(probabilities[index]))
        semantic_key, confidence = classes[best], float(probabilities[best])
        matches = self.local_accounts.get(semantic_key, [])
        if len(matches) != 1:
            return None
        account = matches[0]
        return AccountChoice(
            counterpart=account.local_code,
            standard_history=None,
            confidence="Alta" if confidence >= self.auto_threshold else "Baixa",
            reason=f"global_tfidf:{confidence:.2f}:{self.model_version}:{account.path}",
        )


def self_check() -> None:
    assert normalize_text("NF 12345 em 01/07/2026") == "nf numero em data"
    assert ancestor_codes("1.1.1.02.004")[-2:] == ["1.1.1.02.000", "1.1.1.02.004"]
    assert ancestor_codes("11102004")[-2:] == ["11102000", "11102004"]
    assert feature_text("Tarifa", "outflow") == "direcao_outflow tarifa"
    _validate_prediction_period({"trained_before_period": "2026-08"}, "2026-08")


def main() -> None:
    parser = argparse.ArgumentParser(description="Treina o fallback contábil global por identidade semântica.")
    commands = parser.add_subparsers(dest="command", required=True)
    train_parser = commands.add_parser("train")
    train_parser.add_argument("manifest", type=Path)
    train_parser.add_argument("--before-period", required=True, help="Exclusivo, no formato YYYY-MM.")
    train_parser.add_argument("--output", type=Path, default=Path("output/global_account_model"))
    train_parser.add_argument("--target-precision", type=float, default=0.99)
    test_parser = commands.add_parser("test")
    test_parser.add_argument("artifact", type=Path)
    test_parser.add_argument("history", type=Path)
    test_parser.add_argument("chart", type=Path)
    test_parser.add_argument("--period", required=True)
    test_parser.add_argument("--output", type=Path, default=Path("output/global_account_model_test"))
    commands.add_parser("self-check")
    args = parser.parse_args()
    if args.command == "self-check":
        self_check()
        print("self-check ok")
        return
    if args.command == "test":
        result = test_artifact(args.artifact, args.history, args.chart, args.period, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    result = train(args.manifest, args.output, args.before_period, args.target_precision)
    print(json.dumps({
        "model_version": result["metadata"]["model_version"],
        "training_rows": result["metadata"]["training_rows"],
        "evaluation": result["evaluation"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
