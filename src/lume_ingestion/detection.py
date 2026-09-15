from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lume_ingestion.files import DEFAULT_MAX_FILE_SIZE, require_regular_file, source_file
from lume_ingestion.errors import IngestionFailure
from lume_ingestion.models import SourceFile, Warning


@dataclass(frozen=True)
class Detection:
    source: SourceFile
    format: str | None
    warnings: list[Warning] = field(default_factory=list)


class FileTypeDetector:
    """Detecta o formato pelo conteudo; a extensao nunca decide sozinha."""

    HEADER_SCAN_BYTES = 4096
    _XLS_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

    @staticmethod
    def _is_xlsx(path: Path, prefix: bytes) -> bool:
        """Confirm an Office spreadsheet package instead of trusting .xlsx."""
        if not prefix.startswith(b"PK\x03\x04"):
            return False
        try:
            with ZipFile(path) as archive:
                content_types = archive.read("[Content_Types].xml")
        except (BadZipFile, KeyError, OSError):
            return False
        return b"spreadsheetml" in content_types

    @classmethod
    def _is_xls(cls, prefix: bytes) -> bool:
        """Recognize the Compound File signature used by legacy Excel XLS."""

        return prefix.startswith(cls._XLS_SIGNATURE)

    def inspect(self, path: str | Path, max_size: int = DEFAULT_MAX_FILE_SIZE) -> Detection:
        file_path = require_regular_file(path, max_size=max_size)
        try:
            with file_path.open("rb") as stream:
                prefix = stream.read(self.HEADER_SCAN_BYTES)
        except OSError as exc:
            raise IngestionFailure("file_unreadable", "O arquivo existe, mas nao pode ser lido.", reason=str(exc)) from exc

        if b"%PDF-" in prefix[:1024]:
            detected = "pdf"
        else:
            stripped = prefix.lstrip(b"\xef\xbb\xbf\x00\x09\x0a\x0d\x20")
            if stripped.startswith(b"<"):
                detected = "xml"
            elif self._is_xlsx(file_path, prefix):
                detected = "xlsx"
            elif self._is_xls(prefix):
                detected = "xls"
            else:
                detected = None
        warnings: list[Warning] = []
        extension_says_pdf = file_path.suffix.lower() == ".pdf"
        extension_says_xml = file_path.suffix.lower() == ".xml"
        extension_says_xlsx = file_path.suffix.lower() == ".xlsx"
        extension_says_xls = file_path.suffix.lower() == ".xls"
        if extension_says_pdf and detected != "pdf":
            warnings.append(
                Warning(
                    code="extension_content_mismatch",
                    message="A extensao indica PDF, mas a assinatura binaria nao confirma o formato.",
                )
            )
        elif detected == "pdf" and not extension_says_pdf:
            warnings.append(
                Warning(
                    code="extension_content_mismatch",
                    message="O conteudo e PDF, embora a extensao do arquivo seja diferente.",
                    details={"extension": file_path.suffix.lower()},
                )
            )
        elif extension_says_xml and detected != "xml":
            warnings.append(
                Warning(
                    code="extension_content_mismatch",
                    message="A extensao indica XML, mas o conteudo nao inicia como XML.",
                )
            )
        elif detected == "xml" and not extension_says_xml:
            warnings.append(
                Warning(
                    code="extension_content_mismatch",
                    message="O conteudo parece XML, embora a extensao do arquivo seja diferente.",
                    details={"extension": file_path.suffix.lower()},
                )
            )
        elif extension_says_xlsx and detected != "xlsx":
            warnings.append(
                Warning(
                    code="extension_content_mismatch",
                    message="A extensao indica XLSX, mas o conteudo nao e uma planilha Office valida reconhecida.",
                )
            )
        elif detected == "xlsx" and not extension_says_xlsx:
            warnings.append(
                Warning(
                    code="extension_content_mismatch",
                    message="O conteudo e uma planilha XLSX, embora a extensao do arquivo seja diferente.",
                    details={"extension": file_path.suffix.lower()},
                )
            )
        elif extension_says_xls and detected != "xls":
            warnings.append(
                Warning(
                    code="extension_content_mismatch",
                    message="A extensao indica XLS, mas a assinatura binaria nao confirma uma planilha legada.",
                )
            )
        elif detected == "xls" and not extension_says_xls:
            warnings.append(
                Warning(
                    code="extension_content_mismatch",
                    message="O conteudo e uma planilha XLS legada, embora a extensao do arquivo seja diferente.",
                    details={"extension": file_path.suffix.lower()},
                )
            )
        source = source_file(file_path)
        media_types = {
            "pdf": "application/pdf",
            "xml": "application/xml",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xls": "application/vnd.ms-excel",
        }
        source.media_type = media_types.get(detected)
        return Detection(source=source, format=detected, warnings=warnings)
