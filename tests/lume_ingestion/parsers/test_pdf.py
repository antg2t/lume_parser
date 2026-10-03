"""A cash report splits words the default reader glues. A bank PDF does not."""

from __future__ import annotations

import hashlib
from pathlib import Path

from lume_ingestion.models import SourceFile
from lume_ingestion.parsers.pdf import PdfTextParser


def _pdf(path: Path, tokens: list[tuple[float, float, str]]) -> None:
    lines = ["BT", "/F1 8 Tf"]
    for x, top, text in tokens:
        y = 595 - top
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines.append(f"1 0 0 1 {x:.2f} {y:.2f} Tm ({escaped}) Tj")
    lines.append("ET")
    stream = ("\n".join(lines) + "\n").encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def _source(path: Path) -> SourceFile:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return SourceFile(
        path=str(path),
        name=path.name,
        extension=".pdf",
        size_bytes=path.stat().st_size,
        sha256=digest,
        short_hash=digest[:12],
        media_type="application/pdf",
    )


# Helvetica 8pt. Banco ends at 245.68, so 248.28 leaves a 2.6pt gap.
# The default reader glues that gap. The cash cut must open it again.
_TIGHT_NAME = [(223.0, 120.0, "Banco"), (248.28, 120.0, "Bradesco"), (284.67, 120.0, "S/A")]
_CASH_LABELS = [
    (40.0, 21.0, "Controle de Fechamento de Caixa"),
    (40.0, 48.0, "SALDO INICIAL"),
    (35.0, 91.0, "DATA"),
    (90.0, 91.0, "EMISSAO"),
    (647.0, 91.0, "ENTRADA"),
    (769.0, 91.0, "SALDO"),
]


def _words(path: Path) -> list[str]:
    raw = PdfTextParser().extract(path, _source(path), [])
    return [str(word.get("text") or "") for page in raw["pages"] for word in page["words"]]


def test_cash_pdf_splits_a_name_the_default_reader_glues(tmp_path: Path) -> None:
    path = tmp_path / "caixa.pdf"
    _pdf(path, [*_CASH_LABELS, *_TIGHT_NAME])

    words = _words(path)

    assert "Banco" in words
    assert "Bradesco" in words
    assert "S/A" in words
    assert "BancoBradescoS/A" not in words
    assert not any("BancoBradesco" in word for word in words)


def test_bank_pdf_keeps_the_default_glued_name(tmp_path: Path) -> None:
    path = tmp_path / "extrato.pdf"
    _pdf(path, _TIGHT_NAME)

    words = _words(path)

    assert any("BancoBradesco" in word for word in words)
    assert "Bradesco" not in words
