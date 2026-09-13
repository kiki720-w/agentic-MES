"""Bounded, local-only extraction for user-selected manufacturing attachments."""

import csv
import io
import json
import re
import zipfile
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any

from docx import Document  # type: ignore[import-untyped]
from openpyxl import load_workbook  # type: ignore[import-untyped]
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader  # type: ignore[import-untyped]
from rapidocr import RapidOCR  # type: ignore[import-untyped]

from autonomous_mes.domain.errors import ValidationError

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 30_000
MAX_PDF_PAGES = 100
MAX_SHEETS = 20
MAX_ROWS_PER_SHEET = 1_000
MAX_ARCHIVE_MEMBERS = 2_000
MAX_ARCHIVE_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
SUPPORTED_SUFFIXES = frozenset({
    ".txt", ".md", ".log", ".json", ".yaml", ".yml", ".xml",
    ".csv", ".xlsx", ".docx", ".pdf", ".png", ".jpg", ".jpeg",
})
TEXT_SUFFIXES = frozenset({".txt", ".md", ".log", ".json", ".yaml", ".yml", ".xml"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})

_ocr_engine: RapidOCR | None = None
_ocr_lock = Lock()


def _clean_text(value: str) -> str:
    value = value.replace("\x00", "")
    value = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    return value.strip()


def _bounded(value: str) -> tuple[str, bool]:
    cleaned = _clean_text(value)
    return cleaned[:MAX_EXTRACTED_CHARACTERS], len(cleaned) > MAX_EXTRACTED_CHARACTERS


def _decode_text(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValidationError("text attachment encoding is not supported")


def _validate_ooxml(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            expanded = sum(member.file_size for member in members)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValidationError("Office attachment is invalid or damaged") from exc
    if len(members) > MAX_ARCHIVE_MEMBERS or expanded > MAX_ARCHIVE_EXPANDED_BYTES:
        raise ValidationError("Office attachment expands beyond the safe parsing limit")


def _spreadsheet_text(content: bytes) -> tuple[str, dict[str, Any], list[str]]:
    _validate_ooxml(content)
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise ValidationError("XLSX attachment is invalid or damaged") from exc
    chunks: list[str] = []
    row_count = 0
    sheet_names = workbook.sheetnames[:MAX_SHEETS]
    for sheet_name in sheet_names:
        sheet = workbook[sheet_name]
        chunks.append(f"## Sheet: {sheet_name}")
        for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
            if row_index >= MAX_ROWS_PER_SHEET:
                break
            values = ["" if value is None else str(value) for value in row]
            while values and not values[-1]:
                values.pop()
            if values:
                chunks.append("\t".join(values))
                row_count += 1
    warnings = []
    if len(workbook.sheetnames) > MAX_SHEETS:
        warnings.append("工作表数量超过解析上限，仅提取前 20 个。")
    return "\n".join(chunks), {"sheetCount": len(workbook.sheetnames), "rowCount": row_count}, warnings


def _csv_text(content: bytes) -> tuple[str, dict[str, Any], list[str]]:
    decoded, encoding = _decode_text(content)
    rows = list(csv.reader(io.StringIO(decoded)))
    bounded_rows = rows[:MAX_ROWS_PER_SHEET]
    text = "\n".join("\t".join(cell for cell in row) for row in bounded_rows)
    warnings = ["CSV 行数超过解析上限，仅提取前 1000 行。"] if len(rows) > len(bounded_rows) else []
    return text, {"rowCount": len(rows), "encoding": encoding}, warnings


def _document_text(content: bytes) -> tuple[str, dict[str, Any], list[str]]:
    _validate_ooxml(content)
    try:
        document = Document(io.BytesIO(content))
    except Exception as exc:
        raise ValidationError("DOCX attachment is invalid or damaged") from exc
    chunks = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table_index, table in enumerate(document.tables, start=1):
        chunks.append(f"## Table {table_index}")
        for row in table.rows:
            chunks.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(chunks), {
        "paragraphCount": len(document.paragraphs), "tableCount": len(document.tables),
    }, []


def _ocr(content: bytes) -> tuple[str, dict[str, Any]]:
    global _ocr_engine
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ValidationError("image dimensions exceed the safe OCR limit")
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValidationError("image attachment could not be decoded") from exc
    with _ocr_lock:
        if _ocr_engine is None:
            _ocr_engine = RapidOCR()
        result: Any = _ocr_engine(content)
    texts = list(getattr(result, "txts", ()) or ())
    scores = [float(score) for score in (getattr(result, "scores", ()) or ())]
    confidence = round(sum(scores) / len(scores), 4) if scores else None
    return "\n".join(texts), {"ocrLineCount": len(texts), "ocrMeanConfidence": confidence}


def _pdf_text(content: bytes) -> tuple[str, dict[str, Any], list[str]]:
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:
        raise ValidationError("PDF attachment is invalid, encrypted, or damaged") from exc
    chunks: list[str] = []
    ocr_pages = 0
    pages = list(reader.pages[:MAX_PDF_PAGES])
    for page_index, page in enumerate(pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            image_texts: list[str] = []
            try:
                for image in list(page.images)[:4]:
                    extracted, _ = _ocr(image.data)
                    if extracted:
                        image_texts.append(extracted)
            except Exception:  # noqa: BLE001 - malformed embedded images are optional OCR input
                image_texts = []
            text = "\n".join(image_texts)
            if text:
                ocr_pages += 1
        if text:
            chunks.append(f"## Page {page_index}\n{text}")
    warnings = []
    if len(reader.pages) > MAX_PDF_PAGES:
        warnings.append("PDF 页数超过解析上限，仅提取前 100 页。")
    if not chunks:
        warnings.append("PDF 中没有提取到可读文字。")
    return "\n".join(chunks), {"pageCount": len(reader.pages), "ocrPageCount": ocr_pages}, warnings


def parse_attachment(content: bytes, filename: str) -> dict[str, Any]:
    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.lower()
    if not safe_name or suffix not in SUPPORTED_SUFFIXES:
        raise ValidationError("attachment type is not supported")
    if not content:
        raise ValidationError("attachment is empty")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValidationError("attachment exceeds 15 MB limit")

    warnings: list[str] = []
    metadata: dict[str, Any] = {}
    parser = "LOCAL_TEXT"
    kind = "TEXT"
    if suffix in TEXT_SUFFIXES:
        text, encoding = _decode_text(content)
        metadata = {"encoding": encoding}
        if suffix == ".json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                warnings.append("JSON 语法无效，已按普通文字提取。")
    elif suffix == ".csv":
        kind, parser = "TABLE", "LOCAL_CSV"
        text, metadata, warnings = _csv_text(content)
    elif suffix == ".xlsx":
        kind, parser = "WORKBOOK", "LOCAL_OPENPYXL"
        text, metadata, warnings = _spreadsheet_text(content)
    elif suffix == ".docx":
        kind, parser = "DOCUMENT", "LOCAL_PYTHON_DOCX"
        text, metadata, warnings = _document_text(content)
    elif suffix == ".pdf":
        kind, parser = "DOCUMENT", "LOCAL_PYPDF_OCR"
        text, metadata, warnings = _pdf_text(content)
    elif suffix in IMAGE_SUFFIXES:
        kind, parser = "IMAGE_OCR", "LOCAL_RAPIDOCR"
        try:
            text, metadata = _ocr(content)
        except Exception as exc:
            raise ValidationError("image attachment could not be decoded") from exc
        if not text:
            warnings.append("图片中没有识别到文字；当前版本不判断外观、缺陷或图纸几何。")
    else:  # pragma: no cover - guarded by SUPPORTED_SUFFIXES
        raise ValidationError("attachment type is not supported")

    text, truncated = _bounded(text)
    if truncated:
        warnings.append("提取内容超过 30000 字符，已截断。")
    return {
        "name": safe_name,
        "kind": kind,
        "parser": parser,
        "sha256": sha256(content).hexdigest(),
        "status": "PARSED" if text else "NO_TEXT",
        "text": text,
        "characterCount": len(text),
        "truncated": truncated,
        "metadata": metadata,
        "warnings": warnings,
        "localOnly": True,
    }
