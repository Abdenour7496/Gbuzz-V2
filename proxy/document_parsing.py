"""Bounded document extraction with stable, human-readable source anchors."""
import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET

from fastapi import HTTPException
from pypdf import PdfReader

MAX_ARCHIVE_FILES = 10_000
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_PAGES = 1_000
MAX_CELLS = 1_000_000
XML_FORBIDDEN = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    content = archive.read(name)
    if XML_FORBIDDEN.search(content):
        raise ValueError("XML declarations are forbidden")
    return ET.fromstring(content)


def _bounded_archive(content: bytes) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(io.BytesIO(content))
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_FILES or sum(item.file_size for item in infos) > MAX_EXPANDED_BYTES:
        archive.close()
        raise ValueError("Expanded Office archive exceeds limit")
    return archive


def _text(node: ET.Element) -> str:
    return "".join(item.text or "" for item in node.iter() if item.tag.rsplit("}", 1)[-1] == "t").strip()


def _docx(content: bytes) -> str:
    with _bounded_archive(content) as archive:
        root = _xml(archive, "word/document.xml")
        parts: list[str] = []
        paragraph = table = 0
        for child in root.iter():
            kind = child.tag.rsplit("}", 1)[-1]
            if kind == "p" and not any(parent.tag.rsplit("}", 1)[-1] == "tc" for parent in []):
                value = _text(child)
                if value:
                    paragraph += 1
                    parts.append(f"[Paragraph {paragraph}]\n{value}")
            elif kind == "tbl":
                table += 1
                rows = [item for item in child.iter() if item.tag.rsplit("}", 1)[-1] == "tr"]
                for row_index, row in enumerate(rows, 1):
                    cells = [item for item in row if item.tag.rsplit("}", 1)[-1] == "tc"]
                    for column_index, cell in enumerate(cells, 1):
                        value = _text(cell)
                        if value:
                            parts.append(f"[Table {table} Cell {_column_name(column_index)}{row_index}]\n{value}")
        if not parts:
            raise ValueError("No readable document parts")
        return "\n\n".join(parts)


def _pptx(content: bytes) -> str:
    with _bounded_archive(content) as archive:
        names = sorted(
            (item.filename for item in archive.infolist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", item.filename)),
            key=lambda name: int(re.search(r"(\d+)\.xml$", name).group(1)),
        )
        parts = []
        for name in names:
            slide = int(re.search(r"(\d+)\.xml$", name).group(1))
            value = _text(_xml(archive, name))
            if value:
                parts.append(f"[Slide {slide}]\n{value}")
        if not parts:
            raise ValueError("No readable slides")
        return "\n\n".join(parts)


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xlsx(content: bytes) -> str:
    with _bounded_archive(content) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = [_text(item) for item in _xml(archive, "xl/sharedStrings.xml").iter() if item.tag.rsplit("}", 1)[-1] == "si"]
        workbook = _xml(archive, "xl/workbook.xml")
        rels = _xml(archive, "xl/_rels/workbook.xml.rels")
        targets = {item.attrib.get("Id"): item.attrib.get("Target") for item in rels}
        parts: list[str] = []
        cell_count = 0
        for sheet in (item for item in workbook.iter() if item.tag.rsplit("}", 1)[-1] == "sheet"):
            rel_id = next((value for key, value in sheet.attrib.items() if key.rsplit("}", 1)[-1] == "id"), None)
            target = targets.get(rel_id, "")
            name = sheet.attrib.get("name", "Sheet")
            path = "xl/" + target.lstrip("/").removeprefix("xl/")
            root = _xml(archive, path)
            for cell in (item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "c"):
                cell_count += 1
                if cell_count > MAX_CELLS:
                    raise ValueError("Workbook exceeds cell limit")
                ref = cell.attrib.get("r") or str(cell_count)
                value_node = next((item for item in cell if item.tag.rsplit("}", 1)[-1] in {"v", "is"}), None)
                value = _text(value_node) if value_node is not None and value_node.tag.rsplit("}", 1)[-1] == "is" else (value_node.text or "" if value_node is not None else "")
                if cell.attrib.get("t") == "s" and value:
                    value = shared[int(value)]
                if value.strip():
                    parts.append(f"[Sheet {name} Cell {ref}]\n{value.strip()}")
        if not parts:
            raise ValueError("No readable workbook cells")
        return "\n\n".join(parts)


def _csv(content: bytes) -> str:
    decoded = content.decode("utf-8-sig")
    parts = []
    for row_index, row in enumerate(csv.reader(io.StringIO(decoded)), 1):
        for column_index, value in enumerate(row, 1):
            if value.strip():
                parts.append(f"[Sheet CSV Cell {_column_name(column_index)}{row_index}]\n{value.strip()}")
    return "\n\n".join(parts)


def extract(content: bytes, media_type: str) -> str:
    media_type = media_type.split(";", 1)[0].strip().lower()
    try:
        if media_type == "application/pdf":
            reader = PdfReader(io.BytesIO(content))
            if len(reader.pages) > MAX_PAGES:
                raise ValueError("PDF exceeds page limit")
            parts = [f"[Page {index}]\n{text}" for index, page in enumerate(reader.pages, 1) if (text := (page.extract_text() or "").strip())]
            if not parts:
                raise ValueError("PDF has no extractable text; OCR is required")
            return "\n\n".join(parts)
        if media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return _docx(content)
        if media_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
            return _pptx(content)
        if media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            return _xlsx(content)
        if media_type in {"text/csv", "application/csv"}:
            return _csv(content)
        if media_type.startswith("image/"):
            raise ValueError("Image extraction requires the isolated parser worker")
        if not (media_type.startswith("text/") or media_type in {"application/json", "application/xml", "application/octet-stream"}):
            raise HTTPException(415, "Unsupported document format")
        text = content.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError("Binary content")
        return f"[Text]\n{text}"
    except HTTPException:
        raise
    except (UnicodeError, ValueError, KeyError, IndexError, zipfile.BadZipFile, ET.ParseError) as error:
        raise HTTPException(422, f"Unable to extract {media_type or 'document'} content: {error}") from error
