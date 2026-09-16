"""Recover usable text from print-capture PDFs.

Itaú PDFCreator pages embed CID TrueType (Identity-H) without ToUnicode, so
pdfplumber emits ``(cid:N)`` tokens. Bradesco PDFCreator pages draw the grid as
filled paths with no text operators. Existing bank adapters stay in charge:
this module only restores ``text`` and ``words`` in PDF space.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CID_TOKEN = re.compile(r"\(cid:\d+\)", re.IGNORECASE)
_DEFAULT_DPI = 300
_OCR_PSM = 6
_OCR_LANG = "por"
_LINE_SNAP = 5.0


@dataclass(frozen=True)
class RecoveredPage:
    words: list[dict[str, Any]]
    layout_text: str
    method: str


def cid_marked_text(text: str) -> str:
    return _CID_TOKEN.sub("\ufffd", text or "")


def valid_character_ratio(text: str) -> float:
    significant = [character for character in cid_marked_text(text) if not character.isspace()]
    if not significant:
        return 0.0
    valid = sum(character.isprintable() and character != "\ufffd" for character in significant)
    return round(valid / len(significant), 6)


def _page_text_parts(page: dict[str, Any]) -> tuple[str, str, str]:
    text_obj = page.get("text") if isinstance(page, dict) else None
    if isinstance(text_obj, dict):
        basic = str(text_obj.get("basic") or "")
        layout = str(text_obj.get("layout") or "")
    else:
        basic = str(text_obj or "")
        layout = ""
    words = page.get("words") if isinstance(page, dict) else None
    word_text = ""
    if isinstance(words, list):
        word_text = " ".join(
            str(word.get("text") or "") for word in words if isinstance(word, dict)
        )
    return basic, layout, word_text


def page_needs_recovery(
    page: dict[str, Any],
    *,
    minimum_useful_characters: int = 20,
    minimum_valid_ratio: float = 0.85,
) -> bool:
    if not isinstance(page, dict):
        return True
    metrics = page.get("metrics")
    if isinstance(metrics, dict) and metrics.get("has_useful_text") is False:
        return True
    _basic, layout, word_text = _page_text_parts(page)
    if _CID_TOKEN.search(layout) or _CID_TOKEN.search(word_text):
        return True
    adapter_text = word_text or layout or _basic
    return (
        len(adapter_text.strip()) < minimum_useful_characters
        or valid_character_ratio(adapter_text) < minimum_valid_ratio
    )


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _snap_tops(words: list[dict[str, Any]], tolerance: float = _LINE_SNAP) -> list[dict[str, Any]]:
    if not words:
        return words
    ordered = sorted(words, key=lambda word: (float(word["top"]), float(word["x0"])))
    clusters: list[list[dict[str, Any]]] = []
    for word in ordered:
        if not clusters or abs(float(word["top"]) - float(clusters[-1][0]["top"])) > tolerance:
            clusters.append([word])
        else:
            clusters[-1].append(word)
    snapped: list[dict[str, Any]] = []
    for group in clusters:
        average_top = sum(float(word["top"]) for word in group) / len(group)
        for word in group:
            height = float(word["bottom"]) - float(word["top"])
            snapped.append({**word, "top": average_top, "bottom": average_top + height})
    return snapped


def ocr_image(
    image: Any,
    page_width: float,
    page_height: float,
    *,
    lang: str = _OCR_LANG,
    psm: int = _OCR_PSM,
) -> tuple[list[dict[str, Any]], str]:
    import pytesseract

    image_width, image_height = image.size
    if image_width <= 0 or image_height <= 0 or page_width <= 0 or page_height <= 0:
        return [], ""
    data = pytesseract.image_to_data(
        image,
        lang=lang,
        config=f"--psm {psm}",
        output_type=pytesseract.Output.DICT,
    )
    words: list[dict[str, Any]] = []
    lines: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    count = len(data.get("text") or [])
    for index in range(count):
        text = str(data["text"][index] or "").strip()
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0
        if not text or confidence < 0:
            continue
        left = float(data["left"][index])
        top = float(data["top"][index])
        width = float(data["width"][index])
        height = float(data["height"][index])
        word = {
            "text": text,
            "x0": left * page_width / image_width,
            "x1": (left + width) * page_width / image_width,
            "top": top * page_height / image_height,
            "bottom": (top + height) * page_height / image_height,
            "width": width * page_width / image_width,
            "height": height * page_height / image_height,
            "upright": True,
            "direction": "ltr",
        }
        words.append(word)
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        lines.setdefault(key, []).append(word)
    words = _snap_tops(words)
    layout = "\n".join(" ".join(word["text"] for word in line) for line in lines.values())
    return words, layout


def _render_with_pdftoppm(content: bytes, page_index: int, dpi: int) -> Any:
    from PIL import Image

    if shutil.which("pdftoppm") is None:
        raise FileNotFoundError("pdftoppm")
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "page.pdf"
        pdf_path.write_bytes(content)
        prefix = Path(tmp) / "out"
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-r",
                str(dpi),
                "-f",
                str(page_index + 1),
                "-l",
                str(page_index + 1),
                str(pdf_path),
                str(prefix),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        rendered = sorted(Path(tmp).glob("out*.png"))
        if not rendered:
            raise FileNotFoundError("pdftoppm output")
        with Image.open(rendered[0]) as image:
            return image.convert("RGB")


def render_page(
    content: bytes,
    page_index: int,
    *,
    dpi: int = _DEFAULT_DPI,
) -> tuple[Any, float | None, float | None]:
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(content)
        try:
            page = pdf[page_index]
            width, height = page.get_size()
            bitmap = page.render(scale=dpi / 72.0)
            image = bitmap.to_pil().convert("RGB")
            return image, float(width), float(height)
        finally:
            pdf.close()
    except Exception:
        return _render_with_pdftoppm(content, page_index, dpi), None, None


def recover_page(
    content: bytes,
    page_index: int,
    *,
    page_width: float,
    page_height: float,
    dpi: int = _DEFAULT_DPI,
) -> RecoveredPage | None:
    if not tesseract_available():
        return None
    try:
        image, rendered_width, rendered_height = render_page(content, page_index, dpi=dpi)
    except Exception:
        return None
    width = float(rendered_width or page_width)
    height = float(rendered_height or page_height)
    try:
        words, layout = ocr_image(image, width, height)
    except Exception:
        try:
            words, layout = ocr_image(image, width, height, lang="eng")
        except Exception:
            return None
    if not layout.strip():
        return None
    return RecoveredPage(words=words, layout_text=layout, method="ocr")
