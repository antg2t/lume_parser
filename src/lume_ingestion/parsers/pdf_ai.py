"""xAI vision fallback when the deterministic PDF parser cannot read a statement."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from lume_ingestion.models import (
    BankStatement,
    BankStatementDailyBalance,
    BankStatementOrigin,
    BankStatementTransaction,
    Warning,
)

_DEFAULT_BASE = "https://api.x.ai/v1"
_DEFAULT_MODEL = "grok-4.3"
_MAX_PAGES = 12
_RENDER_DPI = 160
_DEFAULT_CACHE = Path.home() / ".cache" / "lume-ingestion" / "xai"


def cache_dir() -> Path:
    override = os.environ.get("XAI_CACHE_DIR", "").strip()
    if override:
        return Path(override)
    host = Path("/var/lib/lume/xai-cache")
    if host.parent.exists() and os.access(host.parent, os.W_OK):
        return host
    return _DEFAULT_CACHE


def cache_path(content: bytes, model: str) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    safe_model = re.sub(r"[^a-zA-Z0-9._-]+", "_", model.strip()) or "model"
    return cache_dir() / f"{digest}.{safe_model}.json"


def load_cached_statement(content: bytes, model: str) -> dict[str, Any] | None:
    path = cache_path(content, model)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_cached_statement(content: bytes, model: str, payload: dict[str, Any]) -> None:
    path = cache_path(content, model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
_PROMPT = """Leia o extrato bancario brasileiro nestas paginas (Itaú ou Bradesco).
Devolva SOMENTE JSON valido, sem markdown, neste formato:
{
  "bank": "Itaú" ou "Bradesco",
  "layout": "digital-v1" se Itaú, "monthly-v1" se Bradesco,
  "branch": string ou null,
  "account": string ou null,
  "holder": string ou null,
  "holder_tax_id": string ou null,
  "period_start": "YYYY-MM-DD" ou null,
  "period_end": "YYYY-MM-DD" ou null,
  "initial_balance": string decimal com ponto (saldo anterior, com sinal),
  "final_balance": string decimal com ponto,
  "transactions": [
    {
      "date": "YYYY-MM-DD",
      "description": string,
      "counterparty": string ou null,
      "counterparty_tax_id": string ou null,
      "amount": string decimal COM SINAL (saida negativa, entrada positiva),
      "balance": string decimal ou null,
      "page_number": inteiro
    }
  ],
  "daily_balances": [{"date": "YYYY-MM-DD", "balance": string decimal}]
}
Regras:
- Nao invente linha. So o que esta impresso.
- SALDO ANTERIOR nao e transacao; vai em initial_balance.
- SALDO TOTAL DISPONIVEL DIA / saldo do dia vai em daily_balances, nao em transactions.
- Valores no padrao brasileiro (1.234,56) convertidos para "1234.56".
- Debito/pagamento/PIX enviado/boleto pago: amount negativo.
- Credito/PIX recebido/TED recebida/rendimento: amount positivo.
- Pule cabecalho, rodape, lancamentos futuros e avisos.
"""


def xai_configured() -> bool:
    return bool(os.environ.get("XAI_API_KEY", "").strip())


def _origin(page_number: int, excerpt: str) -> BankStatementOrigin:
    return BankStatementOrigin(
        source_format="pdf",
        page_number=page_number,
        region={"x0": 0.0, "x1": 1.0, "top": 0.0, "bottom": 1.0},
        excerpt=(excerpt or "")[:180],
    )


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        parsed = value
    else:
        text = str(value).strip().replace("R$", "").replace(" ", "")
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        try:
            parsed = Decimal(text)
        except InvalidOperation:
            return None
    if not parsed.is_finite():
        return None
    return parsed.quantize(Decimal("0.01"))


def render_pdf_jpegs(content: bytes, *, max_pages: int = _MAX_PAGES, dpi: int = _RENDER_DPI) -> list[bytes]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(content)
    try:
        scale = dpi / 72.0
        images: list[bytes] = []
        count = min(len(pdf), max_pages)
        for index in range(count):
            bitmap = pdf[index].render(scale=scale)
            image = bitmap.to_pil().convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=80, optimize=True)
            images.append(buffer.getvalue())
        return images
    finally:
        pdf.close()


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError("no json object")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("json is not an object")
    return payload


def _post_chat(images: list[bytes], *, api_key: str, model: str, base_url: str) -> str:
    content: list[dict[str, Any]] = []
    for image in images:
        encoded = base64.b64encode(image).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{encoded}",
                    "detail": "high",
                },
            }
        )
    content.append({"type": "text", "text": _PROMPT})
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"xai_http_{exc.code}") from exc
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("xai_empty")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("xai_empty")
    return text


def statement_from_ai(payload: dict[str, Any]) -> BankStatement:
    bank = str(payload.get("bank") or "unknown")
    layout = str(payload.get("layout") or "vision-v1")
    if "itaú" in bank.casefold() or "itau" in bank.casefold():
        bank, layout = "Itaú", "digital-v1"
    elif "bradesco" in bank.casefold():
        bank, layout = "Bradesco", "monthly-v1"
    transactions: list[BankStatementTransaction] = []
    for raw in payload.get("transactions") or []:
        if not isinstance(raw, dict):
            continue
        amount = _parse_money(raw.get("amount"))
        day = _parse_date(raw.get("date"))
        if amount is None or day is None or amount == 0:
            continue
        description = str(raw.get("description") or "").strip() or None
        excerpt = description or str(amount)
        page_number = int(raw.get("page_number") or 1)
        transactions.append(
            BankStatementTransaction(
                date=day,
                description=description,
                counterparty=(str(raw.get("counterparty")).strip() or None) if raw.get("counterparty") else None,
                counterparty_tax_id=(str(raw.get("counterparty_tax_id")).strip() or None)
                if raw.get("counterparty_tax_id")
                else None,
                amount=amount,
                transaction_type="credit" if amount > 0 else "debit",
                balance=_parse_money(raw.get("balance")),
                origin=_origin(page_number, excerpt),
            )
        )
    daily: list[BankStatementDailyBalance] = []
    for raw in payload.get("daily_balances") or []:
        if not isinstance(raw, dict):
            continue
        day = _parse_date(raw.get("date"))
        balance = _parse_money(raw.get("balance"))
        if day is None or balance is None:
            continue
        daily.append(
            BankStatementDailyBalance(
                date=day,
                balance=balance,
                origin=_origin(1, f"saldo {day.isoformat()}"),
            )
        )
    daily.sort(key=lambda item: item.date)
    return BankStatement(
        bank=bank,
        layout=layout,
        branch=(str(payload.get("branch")).strip() or None) if payload.get("branch") else None,
        account=(str(payload.get("account")).strip() or None) if payload.get("account") else None,
        holder=(str(payload.get("holder")).strip() or None) if payload.get("holder") else None,
        holder_tax_id=(str(payload.get("holder_tax_id")).strip() or None) if payload.get("holder_tax_id") else None,
        period_start=_parse_date(payload.get("period_start")),
        period_end=_parse_date(payload.get("period_end")),
        initial_balance=_parse_money(payload.get("initial_balance")),
        final_balance=_parse_money(payload.get("final_balance")),
        transactions=transactions,
        daily_balances=daily,
    )


def extract_bank_statement_with_xai(
    content: bytes,
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    post=None,
) -> tuple[BankStatement, dict[str, Any]] | None:
    chosen_model = (model or os.environ.get("XAI_MODEL") or _DEFAULT_MODEL).strip()
    cached = load_cached_statement(content, chosen_model)
    if cached is not None:
        try:
            statement = statement_from_ai(cached)
        except Exception:
            statement = None  # type: ignore[assignment]
        else:
            if statement.transactions and statement.bank in {"Itaú", "Bradesco"}:
                return statement, cached
    key = (api_key if api_key is not None else os.environ.get("XAI_API_KEY", "")).strip()
    if not key:
        return None
    chosen_base = (base_url or os.environ.get("XAI_API_BASE") or _DEFAULT_BASE).strip()
    try:
        images = render_pdf_jpegs(content)
        if not images:
            return None
        sender = post or _post_chat
        text = sender(images, api_key=key, model=chosen_model, base_url=chosen_base)
        payload = _extract_json(text)
        statement = statement_from_ai(payload)
    except Exception:
        return None
    if not statement.transactions or statement.bank not in {"Itaú", "Bradesco"}:
        return None
    try:
        save_cached_statement(content, chosen_model, payload)
    except OSError:
        pass
    return statement, payload


def vision_recognition(statement: BankStatement) -> dict[str, Any]:
    adapter = (
        "itau-digital-bank-statement-v1"
        if statement.bank == "Itaú"
        else "bradesco-bank-statement-v1"
        if statement.bank == "Bradesco"
        else "vision-bank-statement-v1"
    )
    return {
        "adapter": adapter,
        "bank": statement.bank,
        "layout": statement.layout,
        "page_number": 1,
        "source": "xai-vision",
    }


def vision_warning() -> Warning:
    return Warning(
        code="xai_vision_applied",
        message="Extrato lido por visao xAI porque o parser de texto nao recuperou o layout.",
    )
