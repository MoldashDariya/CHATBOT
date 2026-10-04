import asyncio
import io
import logging
import re
import zipfile

import docx
from docx.document import Document
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph

from bot.errors import ExtractionError
from bot.extractors.base import ExtractedContent, RawSource, clean_meta, find_urls
from bot.pipeline.detector import SourceFormat

logger = logging.getLogger(__name__)

# A DOCX is a zip; python-docx loads its parts fully into memory. Declared sizes are a hard
# cap on what zipfile will return, so checking them up front stops decompression bombs.
MAX_DOCUMENT_XML_BYTES = 20 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5000

_HEADING_RE = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)
_MAX_HEADING_LEVEL = 4
_WEB_SCHEMES = ("http://", "https://")


class DocxExtractor:
    """Paragraphs (headings as `#`, list items as `-`), tables as `| a | b |` rows, and
    hyperlink targets, in document order. Headers, footers, footnotes and text boxes are
    not read."""

    def __init__(
        self,
        max_document_xml_bytes: int = MAX_DOCUMENT_XML_BYTES,
        max_total_bytes: int = MAX_TOTAL_UNCOMPRESSED_BYTES,
    ) -> None:
        self._max_document_xml_bytes = max_document_xml_bytes
        self._max_total_bytes = max_total_bytes

    async def extract(self, source: RawSource) -> ExtractedContent:
        return await asyncio.to_thread(self._extract, source)

    def _extract(self, source: RawSource) -> ExtractedContent:
        self._check_archive(source.body)
        try:
            document = docx.Document(io.BytesIO(source.body))
            blocks, heading_title, link_targets = _read_body(document)
            core_title = (document.core_properties.title or "").strip()
        except Exception as exc:
            logger.warning("DOCX extraction failed: %s", type(exc).__name__, exc_info=True)
            raise ExtractionError("corrupt") from exc

        text = "\n\n".join(blocks).strip()
        links = tuple(dict.fromkeys([*link_targets, *find_urls(text)]))
        return ExtractedContent(
            text=text,
            title=core_title or heading_title,
            source_url=source.url,
            source_format=SourceFormat.DOCX,
            links=links,
            meta=clean_meta(),
        )

    def _check_archive(self, body: bytes) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                infos = archive.infolist()
        except zipfile.BadZipFile as exc:
            raise ExtractionError("corrupt") from exc

        too_many = len(infos) > MAX_ARCHIVE_ENTRIES
        too_big = sum(info.file_size for info in infos) > self._max_total_bytes
        xml_too_big = any(
            info.filename == "word/document.xml" and info.file_size > self._max_document_xml_bytes
            for info in infos
        )
        if too_many or too_big or xml_too_big:
            raise ExtractionError("unsafe_archive")


def _read_body(document: Document) -> tuple[list[str], str | None, list[str]]:
    blocks: list[str] = []
    first_heading: str | None = None
    link_targets: list[str] = []

    for item in document.iter_inner_content():
        if isinstance(item, Table):
            rendered = _render_table(item)
            if rendered:
                blocks.append(rendered)
            continue

        line, targets = _render_paragraph(item)
        link_targets.extend(targets)
        if not line:
            continue
        blocks.append(line)
        if first_heading is None and _heading_level(item) is not None:
            first_heading = line.lstrip("# ").strip() or None
    return blocks, first_heading, link_targets


def _heading_level(paragraph: Paragraph) -> int | None:
    """Markdown level: Title is `#`, "Heading N" is N+1 so the Title/Heading 1 hierarchy holds."""
    name = paragraph.style.name if paragraph.style is not None else ""
    if name.lower() == "title":
        return 1
    match = _HEADING_RE.match(name)
    return min(int(match.group(1)) + 1, _MAX_HEADING_LEVEL) if match else None


def _is_list_item(paragraph: Paragraph) -> bool:
    name = paragraph.style.name if paragraph.style is not None else ""
    properties = paragraph._p.pPr
    return name.lower().startswith("list") or (
        properties is not None and properties.numPr is not None
    )


def _render_paragraph(paragraph: Paragraph) -> tuple[str, list[str]]:
    parts: list[str] = []
    targets: list[str] = []
    for piece in paragraph.iter_inner_content():
        if isinstance(piece, Hyperlink):
            label = piece.text
            url = piece.url
            if url.lower().startswith(_WEB_SCHEMES):
                targets.append(url)
                # The target is not visible in the text; show it so the summary can use it.
                label = label if url in label else f"{label} ({url})"
            parts.append(label)
        else:
            parts.append(piece.text)

    text = " ".join("".join(parts).split())
    if not text:
        return "", targets
    level = _heading_level(paragraph)
    if level is not None:
        return f"{'#' * level} {text}", targets
    if _is_list_item(paragraph):
        return f"- {text}", targets
    return text, targets


def _render_table(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        previous = None
        for cell in row.cells:
            if previous is not None and cell._tc is previous:
                continue  # a horizontally merged cell is reported once per grid column
            previous = cell._tc
            cells.append(" ".join(cell.text.split()))
        if any(cells):
            rows.append(f"| {' | '.join(cells)} |")
    return "\n".join(rows)
