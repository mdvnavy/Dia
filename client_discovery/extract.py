"""Turn an uploaded intake file into questionnaire text.

Everything is converted to Markdown-ish text so the existing deterministic
parser (pipe tables + numbered sections) can read it. Supported: .txt, .md,
.markdown, .csv, .tsv, .json, .rtf, .docx, and .doc (best effort; needs
antiword, catdoc or LibreOffice on the host).
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
TEXT_TYPES = {".txt", ".md", ".markdown", ".text"}
SUPPORTED_TYPES = TEXT_TYPES | {".csv", ".tsv", ".json", ".rtf", ".docx", ".doc"}

# A .docx is a ZIP, so the 5 MB upload cap only bounds its compressed size. These limits are checked from the
# archive's directory before python-docx inflates anything (decompression-bomb guard, Codex review on PR #32).
# Real questionnaires are a few dozen entries and well under 10 MB unzipped, images included.
DOCX_MAX_ENTRIES = 1000
DOCX_MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
DOCX_MAX_RATIO = 100  # uncompressed:compressed, per entry and for the whole archive
DOCX_RATIO_FLOOR_BYTES = 1024 * 1024  # small entries may compress very well without being a risk


class ExtractionError(ValueError):
    """A friendly, user-facing reason the file could not be read."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", errors="replace")


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _table(rows: list[list[str]]) -> str:
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(_cell(c) for c in rows[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows[1:]]
    return "\n".join(lines)


def _from_csv(text: str, delimiter: str | None = None) -> str:
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    table = _table(rows)
    if not table:
        raise ExtractionError("That CSV file is empty.")
    return table


def _flatten(value: object, prefix: str = "") -> list[list[str]]:
    if isinstance(value, dict):
        rows: list[list[str]] = []
        for key, item in value.items():
            rows += _flatten(item, f"{prefix}{key}" if not prefix else f"{prefix} / {key}")
        return rows
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            return [[prefix, "; ".join(str(item) for item in value)]]
        rows = []
        for index, item in enumerate(value, 1):
            rows += _flatten(item, f"{prefix} {index}".strip())
        return rows
    return [[prefix or "Value", "" if value is None else str(value)]]


def _from_json(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ExtractionError(f"That JSON file isn't valid JSON (line {error.lineno}).") from error
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("questionnaire", "markdown", "text", "content"):
            if isinstance(data.get(key), str) and data[key].strip():
                return data[key]
    return _table([["Question", "Answer"], *_flatten(data)])


def _from_rtf(text: str) -> str:
    from striprtf.striprtf import rtf_to_text

    return rtf_to_text(text, errors="ignore")


def _check_docx_archive(data: bytes) -> None:
    """Reject archives that would expand too far, using only the ZIP central directory (nothing is inflated)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, ValueError, OSError) as error:
        raise ExtractionError("That .docx file couldn't be opened. Is it a real Word document?") from error
    too_big = ExtractionError(
        "That .docx expands to far more than a questionnaire should when unzipped, so DIA won't open it. "
        "Save it again from Word or Google Docs, or export it as .txt, then drop it in again.",
        413,
    )
    if len(entries) > DOCX_MAX_ENTRIES:
        raise too_big
    total = compressed = 0
    for entry in entries:
        total += entry.file_size
        compressed += entry.compress_size
        if total > DOCX_MAX_UNCOMPRESSED_BYTES:
            raise too_big
        if entry.file_size > DOCX_RATIO_FLOOR_BYTES and entry.file_size > DOCX_MAX_RATIO * max(entry.compress_size, 1):
            raise too_big
    if total > DOCX_RATIO_FLOOR_BYTES and total > DOCX_MAX_RATIO * max(compressed, 1):
        raise too_big


def _from_docx(data: bytes) -> str:
    _check_docx_archive(data)
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as error:  # pragma: no cover - dependency is in requirements
        raise ExtractionError("Word support isn't installed on this server.", 500) from error
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as error:  # noqa: BLE001 - corrupt or non-docx bytes
        raise ExtractionError("That .docx file couldn't be opened. Is it a real Word document?") from error

    blocks: list[str] = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name if paragraph.style is not None else "").lower()
            if style == "title":
                text = f"# {text}"
            elif style.startswith("heading"):
                level = "".join(ch for ch in style if ch.isdigit()) or "1"
                text = f"{'#' * min(int(level) + 1, 6)} {text}"
            elif "list" in style:
                text = f"- {text}"
            blocks.append(text)
        elif tag == "tbl":
            table = Table(child, document)
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            rendered = _table(rows)
            if rendered:
                blocks.append(rendered)
    return "\n\n".join(blocks)


def _from_doc(data: bytes) -> str:
    """Legacy Word 97-2003. Best effort with whatever converter the host has."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "intake.doc"
        source.write_bytes(data)
        for tool, args in (("antiword", ["-w", "0"]), ("catdoc", ["-w"])):
            if shutil.which(tool):
                result = subprocess.run([tool, *args, str(source)], capture_output=True, timeout=30)
                if result.returncode == 0 and result.stdout.strip():
                    return _decode(result.stdout)
        office = shutil.which("soffice") or shutil.which("libreoffice")
        if office:
            result = subprocess.run(
                [office, "--headless", "--convert-to", "txt:Text (encoded):UTF8", "--outdir", tmp, str(source)],
                capture_output=True,
                timeout=60,
            )
            converted = Path(tmp) / "intake.txt"
            if result.returncode == 0 and converted.exists():
                return _decode(converted.read_bytes())
    raise ExtractionError(
        "Old Word (.doc) files can't be read on this server. "
        "Open it in Word or Google Docs and save it as .docx, then drop it in again."
    )


def extract_text(filename: str, data: bytes) -> str:
    """Return questionnaire text for an uploaded file, or raise ExtractionError."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ExtractionError("That file is over 5 MB. Try a smaller export of the questionnaire.", 413)
    if not data:
        raise ExtractionError("That file is empty.")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_TYPES:
        shown = suffix or "files without an extension"
        raise ExtractionError(
            f"DIA can't read {shown} yet. Try .txt, .md, .csv, .docx, .doc, .rtf or .json."
        )

    if suffix == ".docx":
        text = _from_docx(data)
    elif suffix == ".doc":
        text = _from_doc(data)
    else:
        decoded = _decode(data)
        if suffix == ".csv":
            text = _from_csv(decoded)
        elif suffix == ".tsv":
            text = _from_csv(decoded, "\t")
        elif suffix == ".json":
            text = _from_json(decoded)
        elif suffix == ".rtf":
            text = _from_rtf(decoded)
        else:
            text = decoded

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ExtractionError("No readable text was found in that file.")
    return text + "\n"
